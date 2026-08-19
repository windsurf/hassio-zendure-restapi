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

**Ceilings are read from the device, not hard-coded.** ``chargeMaxLimit`` bounds
the charge side and ``inverseMaxPower`` the discharge side. Both are writable,
so the app is not needed to change them. Their values differ per device and per
configuration — on one installation they were observed at 800 W and later at
3000 and 2400 W — which is exactly why a fixed figure would be wrong.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
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
    OPT_MAX_CHARGE_POWER,
    OPT_MAX_DISCHARGE_POWER,
    OPT_START_CHARGE_AT,
    OPT_START_DISCHARGE_AT,
)
from .coordinator import ZendureCoordinator
from .entity import ZendureEntity
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)


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
        native_max_value=3600,
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
        native_max_value=3600,
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
        native_max_value=3600,
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
        native_max_value=3600,
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
        """Ceiling published by the device, falling back to the static value."""
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
        raw = int(round(value / self.entity_description.scale))
        try:
            await self.coordinator.async_write(self.entity_description.key, raw)
        except ZendureApiError as err:
            _LOGGER.error(
                "Failed to write %s=%s (raw %s): %s",
                self.entity_description.key, value, raw, err
            )
            raise


# ── Controller settings ──────────────────────────────────────────────────
# These do not exist on the device. They configure the operation-mode
# controller and are persisted in the config entry options.

SETTING_NUMBERS: tuple[tuple[ZendureNumberDescription, str], ...] = (
    (ZendureNumberDescription(
        key="manual_power",
        name="Manual power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=-3600,
        native_max_value=3600,
        native_step=10,
        mode=NumberMode.BOX,
        icon="mdi:hand-back-right-outline",
    ), OPT_MANUAL_POWER),
    (ZendureNumberDescription(
        key="max_charge_power",
        name="Max charge power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER,
        native_min_value=0,
        native_max_value=3600,
        native_step=10,
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
        native_max_value=3600,
        native_step=10,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-arrow-down-outline",
    ), OPT_MAX_DISCHARGE_POWER),
    (ZendureNumberDescription(
        key="start_discharge_at",
        name="Start discharging at import",
        native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=0,
        native_max_value=1000,
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
        native_max_value=1000,
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
        native_max_value=1000,
        native_step=10,
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
        native_max_value=1000,
        native_step=10,
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
