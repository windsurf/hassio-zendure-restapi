"""Config and options flow for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

import aiohttp

from .api import ZendureApiError, ZendureLocalApi
from .const import (
    CONF_DEVICE_TYPE,
    CONF_EXTERNAL_INTERVAL,
    CONF_EXTERNAL_TYPE,
    CONF_HOST,
    CONF_MODEL,
    CONF_PORT,
    CONF_PRODUCT,
    CONF_SCAN_INTERVAL,
    CONF_SN,
    DEFAULT_EXTERNAL_INTERVAL,
    DEFAULT_EXTERNAL_TYPE,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_EXTERNAL,
    DEVICE_TYPE_METER,
    EXTERNAL_DEVICE_NAME,
    EXTERNAL_TYPE_HOMEWIZARD_V1,
    HOMEWIZARD_P1_PRODUCT,
    ZEROCONF_HOMEWIZARD,
    DOMAIN,
    EXTERNAL_TYPE_LABELS,
    MAX_EXTERNAL_INTERVAL,
    MAX_SCAN_INTERVAL,
    METER_MARKER_KEY,
    MIN_EXTERNAL_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .p1meter import ADAPTERS
from .registry import is_known_model, meter_name, model_name, parse_service_name

_LOGGER = logging.getLogger(__name__)


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

        meter_type = payload.get(METER_MARKER_KEY, props.get(METER_MARKER_KEY))
        self.is_meter = meter_type is not None
        self.device_type = DEVICE_TYPE_METER if self.is_meter else DEVICE_TYPE_BATTERY

        if self.is_meter:
            self.model = meter_name(meter_type)
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
        """Choose what to add: a Zendure device or an external P1 meter."""
        return self.async_show_menu(
            step_id="user", menu_options=["zendure", "external_meter"]
        )

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
                        options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=self._host or ""): str,
                vol.Optional(CONF_PORT, default=self._port): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
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

    def _external_entry(self, kind: str, host: str, interval: int) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"{EXTERNAL_DEVICE_NAME} ({EXTERNAL_TYPE_LABELS.get(kind, kind)})",
            data={
                CONF_DEVICE_TYPE: DEVICE_TYPE_EXTERNAL,
                CONF_EXTERNAL_TYPE: kind,
                CONF_HOST: host,
            },
            options={CONF_EXTERNAL_INTERVAL: interval},
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
                options={CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL},
            )

        return self.async_show_form(
            step_id="confirm",
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
        if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_EXTERNAL:
            return await self.async_step_external(user_input)

        if user_input is not None:
            merged = {**entry.options, **user_input}
            return self.async_create_entry(title="", data=merged)

        current = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)

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
