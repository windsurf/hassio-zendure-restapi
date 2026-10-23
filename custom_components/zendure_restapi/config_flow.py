"""Config and options flow for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

import aiohttp

from .api import ZendureApiError, ZendureLocalApi
from .const import (
    CONF_BATTERY_ENTRY,
    CONF_DEVICE_NAME,
    CONF_DEVICE_TYPE,
    CONF_EXPORT_ENTITY,
    CONF_EXTERNAL_INTERVAL,
    CONF_EXTERNAL_TYPE,
    CONF_HOST,
    CONF_INVERT,
    CONF_METER_PLAN,
    CONF_SOURCE_DEVICE,
    CONF_MODEL,
    CONF_PORT,
    CONF_PRODUCT,
    CONF_SCAN_INTERVAL,
    CONF_SN,
    CONF_SOURCE_ENTITY,
    DEFAULTS,
    DEFAULT_EXTERNAL_INTERVAL,
    DEFAULT_EXTERNAL_TYPE,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_EXTERNAL,
    DEVICE_TYPE_METER,
    DEVICE_TYPE_PRICE,
    DOMAIN,
    EXTERNAL_DEVICE_NAME,
    EXTERNAL_KIND_LABELS,
    EXTERNAL_TYPE_HA_SENSOR,
    EXTERNAL_TYPE_HOMEWIZARD_V1,
    EXTERNAL_TYPE_LABELS,
    HOMEWIZARD_P1_PRODUCT,
    MAX_EXTERNAL_INTERVAL,
    MAX_SCAN_INTERVAL,
    METER_MARKER_KEY,
    MIN_EXTERNAL_INTERVAL,
    MIN_SCAN_INTERVAL,
    OPT_CREATE_DASHBOARD,
    OPT_REBUILD_DASHBOARD,
    OPT_SOLAR_ENERGY,
    OPT_SOLAR_POWER,
    OPT_DASHBOARD_OFFERED,
    OPT_PRICE_CONTAINS,
    OPT_PRICE_ENTITY,
    PRICE_CONTAINS_OPTIONS,
    PRICE_DEVICE_NAME,
    ZEROCONF_HOMEWIZARD,
)
from .hadevice import Candidate, MeterPlan, fast_route, plan as device_plan, summary
from .p1meter import ADAPTERS, HaSensorMeter, state_power
from .registry import (
    SMART_METER_3CT,
    is_known_model,
    is_meter_report,
    meter_name,
    model_name,
    normalise,
    parse_service_name,
)

_LOGGER = logging.getLogger(__name__)




def _contains_selector() -> selector.SelectSelector:
    """What the price sensor holds; the day plan works back to the spot price."""
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=list(PRICE_CONTAINS_OPTIONS),
            translation_key="price_contains",
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )

class ProbeResult:
    """What a single report fetch tells us about a device."""

    def __init__(self, payload: dict[str, Any]) -> None:
        props = payload.get("properties")
        props = props if isinstance(props, dict) else {}

        # Meters report deviceId and no serial number at all, so identity has
        # to fall back or the flow would abort on a perfectly good device.
        self.serial: str | None = payload.get("sn") or props.get("sn")
        self.handle: str | None = payload.get("deviceId") or props.get("deviceId")
        self.identity: str | None = self.serial or self.handle

        self.product: str | None = payload.get("product")
        self.version: Any = payload.get("version")

        # meterType first (P1 meter); a Smart Meter 3CT has none and is
        # recognised by its fields (issue #7).
        self.meter_type = payload.get(METER_MARKER_KEY, props.get(METER_MARKER_KEY))
        self.is_meter = is_meter_report(payload)
        self.device_type = DEVICE_TYPE_METER if self.is_meter else DEVICE_TYPE_BATTERY

        if self.is_meter:
            # Without meterType the generic meter name, unless the mDNS token
            # names the 3CT (async_step_zeroconf).
            self.model = meter_name(self.meter_type)
        else:
            # The product field is authoritative when present; the mDNS token
            # is only a fallback.
            self.model = model_name(self.product)


async def _probe(hass, host: str, port: int) -> ProbeResult:
    """Contact the device and interpret its report."""
    session = async_get_clientsession(hass)
    api = ZendureLocalApi(session, host, port)
    payload = await api.async_get_report()
    return ProbeResult(payload)


class ZendureConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle setup of a single Zendure device."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._result: ProbeResult | None = None
        self._discovered: dict[str, Any] = {}
        # The device meter being added (v1.6.5): see _device_meter.
        self._device: dict[str, Any] = {}

    def _entry_options(self, dashboard: bool) -> dict[str, Any]:
        """Options of a new Zendure entry. The dashboard request only means
        something for a battery; a meter has no dashboard of its own."""
        result = self._result
        assert result is not None
        options: dict[str, Any] = {CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL}
        if dashboard and not result.is_meter:
            options[OPT_CREATE_DASHBOARD] = True
        elif not result.is_meter:
            # Unticked while adding: the default dashboard is not made either.
            options[OPT_DASHBOARD_OFFERED] = True
        return options

    def _entry_data(self) -> dict[str, Any]:
        result = self._result
        assert result is not None
        return {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_SN: result.serial,
            CONF_MODEL: result.model,
            CONF_PRODUCT: result.product,
            CONF_DEVICE_TYPE: result.device_type,
        }

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose what to add: a Zendure device, an external meter or price control."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["zendure", "external_meter", "ha_meter", "price_control"],
        )

    async def async_step_ha_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A meter already in Home Assistant: a whole device, or sensors chosen by hand."""
        return self.async_show_menu(step_id="ha_meter", menu_options=["ha_device", "ha_sensor"])

    # ── Price control ────────────────────────────────────────────────────

    def _batteries(self) -> list[config_entries.ConfigEntry]:
        return [
            e for e in self._async_current_entries(include_ignore=False)
            if e.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_BATTERY) == DEVICE_TYPE_BATTERY
        ]

    async def async_step_price_control(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Price control for one battery: which battery, which price sensor."""
        batteries = self._batteries()
        if not batteries:
            return self.async_abort(reason="no_battery")
        if user_input is not None:
            battery_id = user_input[CONF_BATTERY_ENTRY]
            await self.async_set_unique_id(f"price_{battery_id}")
            self._abort_if_unique_id_configured()
            others = [
                e for e in self._async_current_entries(include_ignore=False)
                if e.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_PRICE
            ]
            battery = next(e for e in batteries if e.entry_id == battery_id)
            # Several batteries, several price controls: the later ones carry
            # their battery in the name, which keeps their entity ids apart.
            name = PRICE_DEVICE_NAME if not others else f"{PRICE_DEVICE_NAME} ({battery.title})"
            return self.async_create_entry(
                title=name,
                data={
                    CONF_DEVICE_TYPE: DEVICE_TYPE_PRICE,
                    CONF_BATTERY_ENTRY: battery_id,
                    CONF_DEVICE_NAME: name,
                },
                options={
                    OPT_PRICE_ENTITY: user_input[OPT_PRICE_ENTITY],
                    OPT_PRICE_CONTAINS: user_input.get(OPT_PRICE_CONTAINS, DEFAULTS[OPT_PRICE_CONTAINS]),
                },
            )
        schema = vol.Schema(
            {
                vol.Required(CONF_BATTERY_ENTRY, default=batteries[0].entry_id): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(value=e.entry_id, label=e.title)
                            for e in batteries
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(OPT_PRICE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(OPT_PRICE_CONTAINS, default=DEFAULTS[OPT_PRICE_CONTAINS]): _contains_selector(),
            }
        )
        return self.async_show_form(step_id="price_control", data_schema=schema)

    async def async_step_zendure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual entry of host and port."""
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input.get(CONF_PORT, DEFAULT_PORT)
            try:
                result = await _probe(self.hass, host, port)
            except ZendureApiError as err:
                _LOGGER.debug("Probe of %s:%s failed: %s", host, port, err)
                errors["base"] = "cannot_connect"
            else:
                if not result.identity:
                    errors["base"] = "no_identity"
                else:
                    self._host, self._port, self._result = host, port, result
                    await self.async_set_unique_id(result.identity)
                    self._abort_if_unique_id_configured(
                        updates={CONF_HOST: host, CONF_PORT: port}
                    )
                    return self.async_create_entry(
                        title=f"Zendure {result.model}",
                        data=self._entry_data(),
                        options=self._entry_options(
                            user_input.get(OPT_CREATE_DASHBOARD, True)
                        ),
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host or ""): str,
                vol.Optional(CONF_PORT, default=self._port): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Optional(OPT_CREATE_DASHBOARD, default=True): bool,
            }
        )
        return self.async_show_form(step_id="zendure", data_schema=schema, errors=errors)

    # ── External P1 meter ────────────────────────────────────────────────

    async def _async_probe_external(self, kind: str, host: str) -> str | None:
        """One request to the reader. Returns an error key, or None if it answers."""
        meter = ADAPTERS[kind](self.hass, async_get_clientsession(self.hass), host, 1000)
        try:
            await meter.async_probe()
        except (aiohttp.ClientError, TimeoutError):
            return "cannot_connect"
        except (ValueError, TypeError, KeyError, IndexError):
            return "wrong_meter_type"
        return None

    def _device_name(self, kind: str, label: str | None = None) -> str:
        """The first external meter is `External P1 meter`, as in v1.5.2, so
        its entity IDs are sensor.external_p1_meter_*. Later ones carry their
        type in the name, which keeps their IDs apart and recognisable; a
        meter from a Home Assistant device carries that device's name, so two
        of them stay apart too (v1.6.5)."""
        others = [
            e for e in self._async_current_entries(include_ignore=False)
            if e.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_EXTERNAL
        ]
        if not others:
            return EXTERNAL_DEVICE_NAME
        return f"{EXTERNAL_DEVICE_NAME} ({label or EXTERNAL_KIND_LABELS.get(kind, kind)})"

    def _external_entry(
        self, kind: str, host: str | None, interval: int | None,
        extra: dict[str, Any] | None = None, label: str | None = None,
    ) -> ConfigFlowResult:
        data = {
            CONF_DEVICE_TYPE: DEVICE_TYPE_EXTERNAL,
            CONF_EXTERNAL_TYPE: kind,
            CONF_DEVICE_NAME: self._device_name(kind, label),
            **(extra or {}),
        }
        if host is not None:
            data[CONF_HOST] = host
        return self.async_create_entry(
            title=f"{EXTERNAL_DEVICE_NAME} ({label or EXTERNAL_KIND_LABELS.get(kind, kind)})",
            data=data,
            options={CONF_EXTERNAL_INTERVAL: interval} if interval is not None else {},
        )

    async def async_step_external_meter(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """An external P1 reader: type, address, poll interval."""
        errors: dict[str, str] = {}
        if user_input is not None:
            kind = user_input[CONF_EXTERNAL_TYPE]
            host = str(user_input[CONF_HOST]).strip()
            interval = int(user_input[CONF_EXTERNAL_INTERVAL])
            self._async_abort_entries_match({CONF_HOST: host})
            error = await self._async_probe_external(kind, host)
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(f"{kind}_{host}")
                self._abort_if_unique_id_configured()
                return self._external_entry(kind, host, interval)

        current = user_input or {}
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_EXTERNAL_TYPE,
                    default=current.get(CONF_EXTERNAL_TYPE, DEFAULT_EXTERNAL_TYPE),
                ): vol.In(EXTERNAL_TYPE_LABELS),
                vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str,
                vol.Required(
                    CONF_EXTERNAL_INTERVAL,
                    default=current.get(CONF_EXTERNAL_INTERVAL, DEFAULT_EXTERNAL_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_EXTERNAL_INTERVAL, max=MAX_EXTERNAL_INTERVAL),
                ),
            }
        )
        return self.async_show_form(
            step_id="external_meter", data_schema=schema, errors=errors
        )

    async def async_step_ha_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A power sensor already in Home Assistant as external meter."""
        errors: dict[str, str] = {}
        current = user_input or {}
        if user_input is not None:
            source = user_input[CONF_SOURCE_ENTITY]
            export = user_input.get(CONF_EXPORT_ENTITY) or None
            errors = _check_power_entities(self.hass, source, export)
            if not errors:
                await self.async_set_unique_id(f"{EXTERNAL_TYPE_HA_SENSOR}_{source}")
                self._abort_if_unique_id_configured()
                extra: dict[str, Any] = {
                    CONF_SOURCE_ENTITY: source,
                    CONF_INVERT: bool(user_input.get(CONF_INVERT, False)),
                }
                if export:
                    extra[CONF_EXPORT_ENTITY] = export
                return self._external_entry(EXTERNAL_TYPE_HA_SENSOR, None, None, extra)
        return self.async_show_form(
            step_id="ha_sensor",
            data_schema=_ha_sensor_schema(current),
            errors=errors,
        )

    async def async_step_ha_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A device of another integration as external meter (v1.6.5)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            device_id = user_input[CONF_SOURCE_DEVICE]
            found = _device_meter(self.hass, device_id)
            if not found["plan"].ok:
                errors["base"] = "no_power_total"
            else:
                await self.async_set_unique_id(f"ha_device_{device_id}")
                self._abort_if_unique_id_configured()
                self._device = found
                if found["fast"]:
                    return await self.async_step_ha_device_fast()
                return await self.async_step_ha_device_confirm()
        schema = vol.Schema(
            {
                vol.Required(CONF_SOURCE_DEVICE): selector.DeviceSelector(
                    selector.DeviceSelectorConfig(
                        entity=[
                            selector.EntityFilterSelectorConfig(
                                domain="sensor", device_class="power"
                            )
                        ]
                    )
                )
            }
        )
        return self.async_show_form(step_id="ha_device", data_schema=schema, errors=errors)

    async def async_step_ha_device_fast(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """The reader can be polled directly: offer that, faster than through HA."""
        kind, host = self._device["fast"]
        return self.async_show_menu(
            step_id="ha_device_fast",
            menu_options=["ha_device_ip", "ha_device_confirm"],
            description_placeholders={
                "device": self._device["name"],
                "type": EXTERNAL_KIND_LABELS.get(kind, kind),
                "host": host,
            },
        )

    async def async_step_ha_device_ip(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add the reader over IP instead, at its own address."""
        kind, host = self._device["fast"]
        return await self.async_step_external_meter(
            {
                CONF_EXTERNAL_TYPE: kind,
                CONF_HOST: host,
                CONF_EXTERNAL_INTERVAL: DEFAULT_EXTERNAL_INTERVAL,
            }
        )

    async def async_step_ha_device_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show which sensors make the total, and the reading now, before saving."""
        found = self._device
        plan: MeterPlan = found["plan"]
        if user_input is not None:
            plan.invert = bool(user_input.get(CONF_INVERT, plan.invert))
            return self._external_entry(
                EXTERNAL_TYPE_HA_SENSOR,
                None,
                None,
                {
                    CONF_SOURCE_DEVICE: found["device_id"],
                    CONF_METER_PLAN: plan.as_data(),
                    CONF_INVERT: plan.invert,
                },
                label=found["name"],
            )
        reading = await _reading(self.hass, plan)
        schema = vol.Schema({vol.Optional(CONF_INVERT, default=plan.invert): bool})
        return self.async_show_form(
            step_id="ha_device_confirm",
            data_schema=schema,
            description_placeholders={
                "device": found["name"],
                "sensors": summary(plan, found["names"]),
                "reading": reading,
            },
        )

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """An external meter moved out of a v1.5.0/v1.5.1 battery entry.

        No probe: the address was already in use, and a reader that happens to
        be off at this moment should still keep its configuration.
        """
        kind = import_data[CONF_EXTERNAL_TYPE]
        host = import_data[CONF_HOST]
        if kind not in ADAPTERS:
            return self.async_abort(reason="wrong_meter_type")
        self._async_abort_entries_match({CONF_HOST: host})
        await self.async_set_unique_id(f"{kind}_{host}")
        self._abort_if_unique_id_configured()
        return self._external_entry(
            kind, host, int(import_data.get(CONF_EXTERNAL_INTERVAL, DEFAULT_EXTERNAL_INTERVAL))
        )

    async def _async_step_homewizard(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """A HomeWizard P1 meter announcing its local API v1.

        HomeWizard sockets, kWh meters and water meters announce themselves on
        the same service; only the P1 meter measures the grid connection.
        """
        props = discovery_info.properties or {}
        if props.get("product_type") != HOMEWIZARD_P1_PRODUCT:
            return self.async_abort(reason="not_a_p1_meter")
        host = discovery_info.host
        self._async_abort_entries_match({CONF_HOST: host})
        serial = props.get("serial") or host
        await self.async_set_unique_id(f"{EXTERNAL_TYPE_HOMEWIZARD_V1}_{serial}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})
        self._discovered = {"host": host, "serial": serial}
        self.context["title_placeholders"] = {"name": "HomeWizard P1 meter"}
        return await self.async_step_homewizard_confirm()

    async def async_step_homewizard_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a discovered HomeWizard P1 meter as external meter."""
        host = self._discovered["host"]
        errors: dict[str, str] = {}
        if user_input is not None:
            error = await self._async_probe_external(EXTERNAL_TYPE_HOMEWIZARD_V1, host)
            if error:
                # Most often the local API is switched off in the app.
                errors["base"] = "homewizard_api_off" if error == "cannot_connect" else error
            else:
                return self._external_entry(
                    EXTERNAL_TYPE_HOMEWIZARD_V1, host, DEFAULT_EXTERNAL_INTERVAL
                )
        return self.async_show_form(
            step_id="homewizard_confirm",
            description_placeholders={"host": host, "serial": self._discovered["serial"]},
            errors=errors,
        )

    async def async_step_zeroconf(
        self, discovery_info: ZeroconfServiceInfo
    ) -> ConfigFlowResult:
        """Handle a device found through mDNS."""
        if discovery_info.type == ZEROCONF_HOMEWIZARD:
            return await self._async_step_homewizard(discovery_info)
        host = discovery_info.host
        port = discovery_info.port or DEFAULT_PORT
        token, serial_hint = parse_service_name(discovery_info.name)

        self._host = host
        self._port = port

        try:
            result = await _probe(self.hass, host, port)
        except ZendureApiError:
            return self.async_abort(reason="cannot_connect")

        # Fall back to the mDNS token only when the payload names no product.
        if not result.is_meter and not result.product:
            result.model = model_name(token)
            if not is_known_model(token):
                _LOGGER.info(
                    "Discovered Zendure model token '%s' is not in the registry; "
                    "continuing as generic SolarFlow. Entities are unaffected.",
                    token,
                )

        # A meter without meterType: the mDNS token names it when it is the
        # Smart Meter 3CT's; anything else keeps the generic meter name.
        if (
            result.is_meter and result.meter_type is None
            and token and normalise(token) == SMART_METER_3CT
        ):
            result.model = model_name(token)

        if not result.identity:
            result.identity = serial_hint
        if not result.identity:
            return self.async_abort(reason="no_identity")

        self._result = result
        await self.async_set_unique_id(result.identity)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})

        self.context["title_placeholders"] = {"name": result.model}
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask the user to confirm a discovered device."""
        result = self._result
        if user_input is not None and result is not None:
            return self.async_create_entry(
                title=f"Zendure {result.model}",
                data=self._entry_data(),
                options=self._entry_options(user_input.get(OPT_CREATE_DASHBOARD, True)),
            )

        schema = (
            vol.Schema({vol.Optional(OPT_CREATE_DASHBOARD, default=True): bool})
            if result is not None and not result.is_meter
            else None
        )
        return self.async_show_form(
            step_id="confirm",
            data_schema=schema,
            description_placeholders={
                "model": result.model if result else "Zendure device",
                "host": self._host or "",
                "sn": (result.identity if result else "") or "",
            },
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> ZendureOptionsFlow:
        """Return the options flow handler."""
        return ZendureOptionsFlow()


class ZendureOptionsFlow(config_entries.OptionsFlow):
    """Adjust polling, or the address and interval of an external meter.

    The entry options hold more than this form shows: the controller keeps its
    operation mode, power ceilings, thresholds and buffers there too. An
    options flow replaces the entire options dict rather than merging into it,
    so submitting this form with only the interval in it would silently reset
    every controller setting to its default, including dropping the operation
    mode back to standby. Everything not on the form is therefore carried
    forward explicitly.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self.config_entry
        if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_PRICE:
            return await self.async_step_price(user_input)
        if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_EXTERNAL:
            if entry.data.get(CONF_METER_PLAN):
                return await self.async_step_device(user_input)
            if entry.data.get(CONF_EXTERNAL_TYPE) == EXTERNAL_TYPE_HA_SENSOR:
                return await self.async_step_sensor(user_input)
            return await self.async_step_external(user_input)

        battery = entry.data.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_METER
        if user_input is not None:
            merged = {**entry.options, **user_input}
            # An emptied selector is left out of user_input: drop the old value.
            for key in (OPT_SOLAR_POWER, OPT_SOLAR_ENERGY):
                if battery and not user_input.get(key):
                    merged.pop(key, None)
            return self.async_create_entry(title="", data=merged)

        current = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        fields: dict[Any, Any] = {
            vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                vol.Coerce(int),
                vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
            )
        }
        # A battery can ask for the dashboard later; it is created once and an
        # existing one is left alone.
        if battery:
            # Solar from outside the device, for the dashboard (v1.6.6).
            for key, device_class in ((OPT_SOLAR_POWER, "power"), (OPT_SOLAR_ENERGY, "energy")):
                current_entity = entry.options.get(key)
                fields[
                    vol.Optional(
                        key,
                        description={"suggested_value": current_entity} if current_entity else None,
                    )
                ] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor", device_class=device_class)
                )
            fields[vol.Optional(OPT_CREATE_DASHBOARD, default=False)] = bool
            fields[vol.Optional(OPT_REBUILD_DASHBOARD, default=False)] = bool
        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))

    async def async_step_price(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Price control: the price sensor and what it holds. Everything else is an entity."""
        entry = self.config_entry
        if user_input is not None:
            return self.async_create_entry(title="", data={**entry.options, **user_input})
        schema = vol.Schema(
            {
                vol.Required(
                    OPT_PRICE_ENTITY, default=entry.options.get(OPT_PRICE_ENTITY) or vol.UNDEFINED
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(
                    OPT_PRICE_CONTAINS,
                    default=entry.options.get(OPT_PRICE_CONTAINS, DEFAULTS[OPT_PRICE_CONTAINS]),
                ): _contains_selector(),
            }
        )
        return self.async_show_form(step_id="price", data_schema=schema)

    async def async_step_external(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """External meter: a new address goes into the entry data, the interval
        into the options. Either change reloads the entry.

        The form is shown under step_id "external", so Home Assistant sends the
        submitted values here, not to async_step_init."""
        entry = self.config_entry
        if user_input is not None:
            host = str(user_input[CONF_HOST]).strip()
            if host and host != entry.data.get(CONF_HOST):
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_HOST: host}
                )
            return self.async_create_entry(
                title="",
                data={**entry.options,
                      CONF_EXTERNAL_INTERVAL: int(user_input[CONF_EXTERNAL_INTERVAL])},
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=entry.data.get(CONF_HOST, "")): str,
                vol.Required(
                    CONF_EXTERNAL_INTERVAL,
                    default=entry.options.get(CONF_EXTERNAL_INTERVAL, DEFAULT_EXTERNAL_INTERVAL),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_EXTERNAL_INTERVAL, max=MAX_EXTERNAL_INTERVAL),
                ),
            }
        )
        return self.async_show_form(step_id="external", data_schema=schema)

    async def async_step_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Home Assistant sensor: the entities go into the entry data."""
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            source = user_input[CONF_SOURCE_ENTITY]
            export = user_input.get(CONF_EXPORT_ENTITY) or None
            errors = _check_power_entities(self.hass, source, export)
            if not errors:
                data = {
                    **entry.data,
                    CONF_SOURCE_ENTITY: source,
                    CONF_INVERT: bool(user_input.get(CONF_INVERT, False)),
                }
                data.pop(CONF_EXPORT_ENTITY, None)
                if export:
                    data[CONF_EXPORT_ENTITY] = export
                self.hass.config_entries.async_update_entry(entry, data=data)
                return self.async_create_entry(title="", data=dict(entry.options))
        current = user_input or {
            CONF_SOURCE_ENTITY: entry.data.get(CONF_SOURCE_ENTITY),
            CONF_EXPORT_ENTITY: entry.data.get(CONF_EXPORT_ENTITY),
            CONF_INVERT: entry.data.get(CONF_INVERT, False),
        }
        return self.async_show_form(
            step_id="sensor", data_schema=_ha_sensor_schema(current), errors=errors
        )

    async def async_step_device(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """A device meter: the sign, and the sensors found again (v1.6.5)."""
        entry = self.config_entry
        device_id = entry.data.get(CONF_SOURCE_DEVICE) or ""
        found = _device_meter(self.hass, device_id)
        plan: MeterPlan = found["plan"] if found["plan"].ok else MeterPlan.from_data(
            entry.data.get(CONF_METER_PLAN) or {}
        )
        plan.invert = bool(entry.data.get(CONF_INVERT, plan.invert))
        if user_input is not None:
            plan.invert = bool(user_input.get(CONF_INVERT, plan.invert))
            data = {**entry.data, CONF_METER_PLAN: plan.as_data(), CONF_INVERT: plan.invert}
            self.hass.config_entries.async_update_entry(entry, data=data)
            return self.async_create_entry(title="", data=dict(entry.options))
        schema = vol.Schema({vol.Optional(CONF_INVERT, default=plan.invert): bool})
        return self.async_show_form(
            step_id="device",
            data_schema=schema,
            description_placeholders={
                "device": found["name"],
                "sensors": summary(plan, found["names"]),
                "reading": await _reading(self.hass, plan),
            },
        )


def _ha_sensor_schema(current: dict[str, Any]) -> vol.Schema:
    power = selector.EntitySelector(
        selector.EntitySelectorConfig(domain="sensor", device_class="power")
    )
    export_key = (
        vol.Optional(
            CONF_EXPORT_ENTITY,
            description={"suggested_value": current[CONF_EXPORT_ENTITY]},
        )
        if current.get(CONF_EXPORT_ENTITY)
        else vol.Optional(CONF_EXPORT_ENTITY)
    )
    source_key = (
        vol.Required(CONF_SOURCE_ENTITY, default=current[CONF_SOURCE_ENTITY])
        if current.get(CONF_SOURCE_ENTITY)
        else vol.Required(CONF_SOURCE_ENTITY)
    )
    return vol.Schema(
        {
            source_key: power,
            export_key: power,
            vol.Optional(CONF_INVERT, default=bool(current.get(CONF_INVERT, False))): bool,
        }
    )


def _check_power_entities(hass, source: str, export: str | None) -> dict[str, str]:
    """An error per field whose entity is not a power reading right now."""
    errors: dict[str, str] = {}
    for key, entity_id in ((CONF_SOURCE_ENTITY, source), (CONF_EXPORT_ENTITY, export)):
        if not entity_id:
            continue
        try:
            state_power(hass.states.get(entity_id))
        except ValueError:
            errors[key] = "not_a_power_sensor"
    if export and export == source:
        errors[CONF_EXPORT_ENTITY] = "same_entity"
    return errors


def _device_meter(hass, device_id: str) -> dict[str, Any]:
    """The device, its power sensors and the plan they make (v1.6.5)."""
    device = dr.async_get(hass).async_get(device_id)
    registry = er.async_get(hass)
    candidates: list[Candidate] = []
    names: dict[str, str] = {}
    platforms: list[str] = []
    for entity in er.async_entries_for_device(registry, device_id):
        if entity.platform == DOMAIN or entity.domain != "sensor":
            continue
        state = hass.states.get(entity.entity_id)
        attributes = state.attributes if state is not None else {}
        unit = attributes.get("unit_of_measurement") or entity.unit_of_measurement
        name = entity.original_name or ""
        candidates.append(Candidate(entity.entity_id, unit, name))
        names[entity.entity_id] = attributes.get("friendly_name") or name or entity.entity_id
        platforms.append(entity.platform)
    platform = max(set(platforms), key=platforms.count) if platforms else ""
    fast = None
    if device is not None:
        model = " ".join(str(x) for x in (device.model, getattr(device, "model_id", None)) if x)
        for entry_id in device.config_entries:
            config_entry = hass.config_entries.async_get_entry(entry_id)
            if config_entry is not None and config_entry.domain == platform:
                fast = fast_route(platform, model, dict(config_entry.data))
                if fast:
                    break
    return {
        "device_id": device_id,
        "name": (device.name_by_user or device.name) if device else device_id,
        "plan": device_plan(platform, candidates),
        "names": names,
        "fast": fast,
    }


async def _reading(hass, plan: MeterPlan) -> str:
    """The net grid power now, as the meter would read it, for the confirm step."""
    meter = HaSensorMeter(hass, None, "", 250)
    meter.plan = plan
    try:
        _stamp, power, _extra = meter.parse(await meter._async_fetch())
    except (ValueError, TypeError, KeyError, IndexError):
        return "–"
    direction = "import" if power > 0 else "export" if power < 0 else "zero"
    return f"{power:.0f} W ({direction})"
