"""Number platform for the Zendure RestAPI integration.

Three things the zenSDK document gets wrong or leaves out, all found by
comparing it against a live SolarFlow 3000 Mix AC+.

**Scaling.** ``socSet`` and ``minSoc`` are stored in tenths of a percent, not
whole percent: live values of 1000 and 100 mean 100.0% and 10.0%. The document
also gives ``minSoc`` a range of 0-50, which a second sample reading 800 (80%)
disproved. The range used here is 0-100.

**Two ceilings per direction, and they are not duplicates.** ``chargeMaxLimit``
and ``inverseMaxPower`` live on the device and are what the Zendure app sets.
They persist and bound everything, including whatever the app or an on-device
manager does. ``Max charge power`` and ``Max discharge power`` are controller
settings and bound only what this integration writes. The controller uses the
lower of the two, which is why both are kept: the device ceiling remains a hard
limit at moments when this integration is not running.

**Trim strength damps the upward direction only.** The trim loop corrects the
grid once per meter sample, but the meter itself reports 0.4 to 1.1 s behind
reality, so at full strength it commits a whole correction for a deviation
whose answer it has not seen yet. 80% is the highest value that settles at two
samples of delay as well as one, which is why it is the default.

Lowering the limit runs at full strength regardless of this setting. That is
not an oversight: a downward correction is clamped at zero and the trim loop
cannot reverse, so it has no way to overshoot, while every cycle spent easing
towards zero is a cycle of pushing power onto the grid. Measured on hardware:
at 30% both ways a load event took ten seconds to wind down against four at
50%, and the extra six seconds sat between -2500 and -800 W.

It is stored as whole percent so it travels the same integer path as every
other setting.

**Ceilings are read from the device, not hard-coded.** ``chargeMaxLimit`` bounds
the charge side and ``inverseMaxPower`` the discharge side. Their values differ
per device and per configuration — on one installation they were observed at
800 W and later at 3000 and 2400 W — which is exactly why a fixed figure would
be wrong. The integration never sets them by itself.

**The Inverter power limit is never raised from here (v1.6.10).**
``inverseMaxPower`` is the device's ceiling for feeding into the grid. It
follows the grid code (``gridStandard``) and what was registered with the grid
operator, and it is the last guard rail while Home Assistant is not running.
Zendure's own library keeps it read-only. From Home Assistant it can be
lowered; raising it is for the Zendure app (*Safe power setting*). The
entity's maximum is the value the device reports, so the slider offers
nothing higher, and a higher value from a service call is refused. While the
device has not reported a value, no write goes out at all: without it a raise
cannot be told from a lowering.

**A setting the device keeps is read back (v1.7.3).** After a write of
``inverseMaxPower``, ``chargeMaxLimit``, ``socSet`` or ``minSoc`` the reports
are watched until the device holds the new value, and the outcome goes to the
logbook — old value, new value, confirmed or not, with the user or automation
that asked — and to the log (WARNING for the Inverter power limit, INFO for the
others; ERROR when it is not confirmed). It runs in the background, so a
service call or an automation does not wait for it. A write that timed out is
not written again: its outcome is unknown.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ZendureApiError
from .const import (
    CONF_MODEL,
    DOMAIN,
    OPT_CHARGE_BUFFER,
    OPT_DISCHARGE_BUFFER,
    OPT_DIRECTION_DELAY,
    OPT_MANUAL_POWER,
    OPT_MIN_CHARGE_POWER,
    OPT_MIN_DISCHARGE_POWER,
    OPT_MODE_THRESHOLD,
    OPT_TRIM_THRESHOLD,
    OPT_MAX_CHARGE_POWER,
    OPT_MAX_DISCHARGE_POWER,
    OPT_START_CHARGE_AT,
    OPT_START_DISCHARGE_AT,
    OPT_TRIM_FACTOR,
    POWER_CEILING,
)
from .coordinator import ZendureCoordinator
from .entity import ZendureEntity
from .settings import ZendureSettings

from homeassistant.const import Platform
from .const import CONF_DEVICE_TYPE, DEVICE_TYPE_CLUSTER, DEVICE_TYPE_PRICE, DEVICE_TYPE_VIRTUAL
from .cluster_entities import cluster_entities
from .price_entities import price_entities
_LOGGER = logging.getLogger(__name__)

# The device's ceiling for feeding into the grid (v1.6.10).
INVERTER_LIMIT_KEY = "inverseMaxPower"
# Settings the device keeps across a restart: a write is read back and logged.
PERSISTENT_KEYS = (INVERTER_LIMIT_KEY, "chargeMaxLimit", "socSet", "minSoc")
# How long to watch the reports for the new value: at least this, and at least
# three polls, since a report can lag a write.
CONFIRM_MIN_S = 10.0
CONFIRM_POLLS = 3
CONFIRM_STEP_S = 1.0
EVENT_LOGBOOK_ENTRY = "logbook_entry"


@dataclass(frozen=True, kw_only=True)
class ZendureNumberDescription(NumberEntityDescription):
    """Number description with display scaling and an optional dynamic ceiling.

    ``scale`` converts raw to displayed: displayed = raw * scale. Writes apply
    the inverse.
    """

    scale: float = 1.0
    max_key: str | None = None


NUMBERS: tuple[ZendureNumberDescription, ...] = (
    ZendureNumberDescription(
        key="inputLimit",
        name="Charge limit (AC)",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=POWER_CEILING,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:battery-arrow-up",
        max_key="chargeMaxLimit",
    ),
    ZendureNumberDescription(
        key="outputLimit",
        name="Discharge limit",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=POWER_CEILING,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:battery-arrow-down",
        max_key="inverseMaxPower",
    ),
    ZendureNumberDescription(
        key="chargeMaxLimit",
        name="Charge power limit",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=POWER_CEILING,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:speedometer",
    ),
    ZendureNumberDescription(
        key="inverseMaxPower",
        name="Inverter power limit",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=POWER_CEILING,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    ZendureNumberDescription(
        key="socSet",
        name="Upper SOC limit",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=70,
        native_max_value=100,
        native_step=0.5,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-charging-high",
        entity_category=EntityCategory.CONFIG,
        scale=0.1,
    ),
    ZendureNumberDescription(
        key="minSoc",
        name="Lower SOC limit",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=0.5,
        mode=NumberMode.SLIDER,
        icon="mdi:battery-low",
        entity_category=EntityCategory.CONFIG,
        scale=0.1,
    ),
    ZendureNumberDescription(
        key="batCalTime",
        name="Calibration interval",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=0,
        native_max_value=10080,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create number entities for every reported writable key."""
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_PRICE:
        async_add_entities(price_entities(Platform.NUMBER, hass, entry))
        return
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_CLUSTER:
        async_add_entities(cluster_entities(Platform.NUMBER, hass, entry))
        return
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_VIRTUAL:
        from .sim.entities import virtual_entities

        async_add_entities(virtual_entities(Platform.NUMBER, hass, entry))
        return
    coordinator: ZendureCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = coordinator.device_id or entry.unique_id or entry.entry_id
    model = entry.data.get(CONF_MODEL) or "SolarFlow"
    data = coordinator.data or {}

    coordinator.register_known_keys({d.key for d in NUMBERS})

    entities: list[NumberEntity] = [
        ZendureNumber(coordinator, description, device_id, model)
        for description in NUMBERS
        if description.key in data
    ]

    runtime = hass.data[DOMAIN].get(f"{entry.entry_id}_runtime")
    if runtime is not None:
        entities += [
            ZendureSettingNumber(coordinator, d, device_id, model, runtime["settings"], key)
            for d, key in SETTING_NUMBERS
        ]

    async_add_entities(entities)


class ZendureNumber(ZendureEntity, NumberEntity):
    """Writable numeric property."""

    entity_description: ZendureNumberDescription

    @property
    def native_max_value(self) -> float:
        """Ceiling published by the device, falling back to the static value.

        The Inverter power limit is its own ceiling: never raised from here
        (v1.6.10).
        """
        if self.entity_description.key == INVERTER_LIMIT_KEY:
            current = self.native_value
            if current is not None and current > 0:
                return current
        max_key = self.entity_description.max_key
        if max_key:
            reported = (self.coordinator.data or {}).get(max_key)
            try:
                ceiling = float(reported)
            except (TypeError, ValueError):
                ceiling = 0.0
            if ceiling > 0:
                return ceiling
        return self.entity_description.native_max_value

    @property
    def native_value(self) -> float | None:
        value = self.raw_value
        if value is None:
            return None
        try:
            scaled = float(value) * self.entity_description.scale
        except (TypeError, ValueError):
            return None
        return round(scaled, 1)

    async def async_set_native_value(self, value: float) -> None:
        key = self.entity_description.key
        raw = int(round(value / self.entity_description.scale))
        before = self.native_value
        # Without a reported value it cannot be checked whether this raises it:
        # nothing is written then (v1.6.10).
        if key == INVERTER_LIMIT_KEY and (before is None or value > before):
            raise HomeAssistantError(
                "The Inverter power limit is the battery's ceiling for feeding into the grid, set by the "
                "grid code and your registration with the grid operator. "
                + ("The battery has not reported it yet, so a change cannot be checked; try again once it has. "
                   if before is None else f"It can be lowered here, not raised above {before:g} W: ")
                + "Raise it in the Zendure app (Safe power setting)."
            )
        try:
            await self.coordinator.async_write(key, raw)
        except ZendureApiError as err:
            _LOGGER.error("Failed to write %s=%s (raw %s): %s", key, value, raw, err)
            if key in PERSISTENT_KEYS:
                self._logbook(f"not set to {value:g}{self._unit()}: the write failed ({err})", self._context)
            raise
        if key in PERSISTENT_KEYS:
            self.hass.async_create_task(self._async_confirm(before, value, raw, self._context))

    def _unit(self) -> str:
        unit = self.entity_description.native_unit_of_measurement
        return f" {unit}" if unit else ""

    def _logbook(self, message: str, context: Context | None) -> None:
        """A line in the logbook of this entity; logbook shows who asked from the context."""
        state = self.hass.states.get(self.entity_id) if self.entity_id else None
        name = state.name if state is not None else str(self.name or self.entity_description.name)
        self.hass.bus.async_fire(
            EVENT_LOGBOOK_ENTRY,
            {"name": name, "message": message, "domain": DOMAIN, "entity_id": self.entity_id},
            context=context,
        )

    async def _async_confirm(self, before: float | None, value: float, raw: int, context: Context | None) -> None:
        """Watch the reports until the device holds `raw`; log the outcome (v1.7.3)."""
        key = self.entity_description.key
        interval = self.coordinator.update_interval
        seconds = interval.total_seconds() if interval else 0.0
        deadline = time.monotonic() + max(CONFIRM_MIN_S, CONFIRM_POLLS * seconds)
        while True:
            confirmed = _same((self.coordinator.data or {}).get(key), raw)
            if confirmed or time.monotonic() >= deadline:
                break
            await asyncio.sleep(CONFIRM_STEP_S)
        old = "unknown" if before is None else f"{before:g}"
        user = getattr(context, "user_id", None) if context is not None else None
        if confirmed and before is not None and float(before) == float(value):
            return  # Written again as it was: nothing changed, nothing to log.
        if confirmed:
            self._logbook(f"changed from {old} to {value:g}{self._unit()}, confirmed by the battery", context)
            level = logging.WARNING if key == INVERTER_LIMIT_KEY else logging.INFO
            _LOGGER.log(level, "%s changed from %s to %s%s, confirmed by the battery (user %s)",
                        key, old, f"{value:g}", self._unit(), user or "none")
            return
        shown = self.native_value
        now = "nothing" if shown is None else f"{shown:g}{self._unit()}"
        self._logbook(f"set to {value:g}{self._unit()} but not confirmed: the battery reports {now}", context)
        _LOGGER.error("%s written as %s%s but not confirmed: the battery reports %s (user %s)",
                      key, f"{value:g}", self._unit(), now, user or "none")


def _same(reported: object, raw: int) -> bool:
    try:
        return float(reported) == float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


# ── Controller settings ──────────────────────────────────────────────────
# These do not exist on the device. They configure the operation-mode
# controller and are persisted in the config entry options.

SETTING_NUMBERS: tuple[tuple[ZendureNumberDescription, str], ...] = (
    (ZendureNumberDescription(
        key="manual_power",
        name="Manual power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=-POWER_CEILING,
        native_max_value=POWER_CEILING,
        native_step=100,
        mode=NumberMode.BOX,
        icon="mdi:hand-back-right-outline",
    ), OPT_MANUAL_POWER),
    (ZendureNumberDescription(
        key="max_charge_power",
        name="Max charge power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=POWER_CEILING,
        native_step=100,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-arrow-up-outline",
    ), OPT_MAX_CHARGE_POWER),
    (ZendureNumberDescription(
        key="max_discharge_power",
        name="Max discharge power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=POWER_CEILING,
        native_step=100,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-arrow-down-outline",
    ), OPT_MAX_DISCHARGE_POWER),
    (ZendureNumberDescription(
        key="start_discharge_at",
        name="Start discharging at import",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:transmission-tower-import",
    ), OPT_START_DISCHARGE_AT),
    (ZendureNumberDescription(
        key="start_charge_at",
        name="Start charging at export",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:transmission-tower-export",
    ), OPT_START_CHARGE_AT),
    (ZendureNumberDescription(
        key="direction_change_delay",
        name="Direction change delay",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0,
        native_max_value=60,
        native_step=1,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:swap-horizontal-circle-outline",
    ), OPT_DIRECTION_DELAY),
    (ZendureNumberDescription(
        key="min_charge_power",
        name="Min charge power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:arrow-collapse-up",
    ), OPT_MIN_CHARGE_POWER),
    (ZendureNumberDescription(
        key="min_discharge_power",
        name="Min discharge power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:arrow-collapse-down",
    ), OPT_MIN_DISCHARGE_POWER),
    (ZendureNumberDescription(
        key="charge_buffer",
        name="Charge buffer",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:target",
    ), OPT_CHARGE_BUFFER),
    (ZendureNumberDescription(
        key="discharge_buffer",
        name="Discharge buffer",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:target",
    ), OPT_DISCHARGE_BUFFER),
    (ZendureNumberDescription(
        key="trim_factor",
        name="Trim strength",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=30,
        native_max_value=100,   # 100 is offered but rings at two samples of delay
        native_step=5,
        mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:tune-variant",
    ), OPT_TRIM_FACTOR),
    (ZendureNumberDescription(
        key="mode_threshold",
        name="Mode threshold",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:ruler",
    ), OPT_MODE_THRESHOLD),
    (ZendureNumberDescription(
        key="trim_threshold",
        name="Trim threshold",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=200,
        native_step=5,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:ruler",
    ), OPT_TRIM_THRESHOLD),
)


class ZendureSettingNumber(ZendureEntity, NumberEntity):
    """A controller setting, stored in the config entry rather than on the device."""

    entity_description: ZendureNumberDescription

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: ZendureNumberDescription,
        device_id: str,
        model: str,
        settings: ZendureSettings,
        option_key: str,
    ) -> None:
        super().__init__(coordinator, description, device_id, model)
        self._settings = settings
        self._option_key = option_key

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_max_value(self) -> float:
        return self.entity_description.native_max_value

    @property
    def native_value(self) -> float | None:
        return float(self._settings.get_int(self._option_key))

    async def async_set_native_value(self, value: float) -> None:
        self._settings.set(self._option_key, int(value))
        self.async_write_ha_state()
