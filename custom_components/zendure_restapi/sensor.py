"""Sensor platform for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
    INTEGRATION_VERSION,
    CONF_MODEL,
    DEVICE_TYPE_METER,
    DOMAIN,
    EXTERNAL_DEVICE_NAME,
    EXTERNAL_STATUS_LIVE,
    EXTERNAL_STATUS_STALE,
    EXTERNAL_STATUS_UNREACHABLE,
    EXTERNAL_KIND_LABELS,
    CONF_DEVICE_NAME,
    PACK_PREFIX,
)
from .p1meter import ExternalMeter
from .registry import pack_model
from .energy import EnergyAccumulator, WriteGate, split_signed
from .coordinator import ZendureCoordinator
from .entity import align_entity_ids, device_prefix, TranslatedName, ZendureEntity
from .properties import (
    AC_STATUS_MAP,
    DC_STATUS_MAP,
    FAN_SPEED_MAP,
    PACK_STATE_MAP,
    PV_STATUS_MAP,
    SOC_LIMIT_MAP,
    SOC_STATUS_MAP,
    convert,
    decode_ac_coupling,
    map_enum,
)

from homeassistant.const import Platform
from .const import CONF_DEVICE_TYPE, DEVICE_TYPE_PRICE
from .price_entities import price_entities
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ZendureSensorDescription(SensorEntityDescription):
    """Sensor description with a named value converter."""

    converter: str = "identity"
    enum_map: dict[int, str] | None = None


def _power(key: str, name: str, **kwargs: Any) -> ZendureSensorDescription:
    return ZendureSensorDescription(
        key=key,
        name=name,
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        **kwargs,
    )


def _diag(key: str, name: str, enabled: bool = False) -> ZendureSensorDescription:
    return ZendureSensorDescription(
        key=key,
        name=name,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=enabled,
    )


DEVICE_SENSORS: tuple[ZendureSensorDescription, ...] = (
    # ── Energy flow ──────────────────────────────────────────────────────
    ZendureSensorDescription(
        key="electricLevel",
        name="State of charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _power("packInputPower", "Battery discharge power"),
    _power("outputPackPower", "Battery charge power"),
    _power("outputHomePower", "Home output power"),
    _power("gridInputPower", "Grid input power"),
    _power("solarInputPower", "PV input power"),
    _power("gridOffPower", "Backup output power"),
    _power("gridOffPower2", "Backup output power 2"),
    _power("solarPower1", "PV string 1"),
    _power("solarPower2", "PV string 2"),
    _power("solarPower3", "PV string 3"),
    _power("solarPower4", "PV string 4"),
    _power("solarPower5", "PV string 5"),
    _power("solarPower6", "PV string 6"),
    ZendureSensorDescription(
        key="remainOutTime",
        name="Remaining discharge time",
        converter="remain_minutes",
        native_unit_of_measurement=UnitOfTime.MINUTES,
        device_class=SensorDeviceClass.DURATION,
        icon="mdi:battery-clock",
    ),
    # ── Electrical ───────────────────────────────────────────────────────
    ZendureSensorDescription(
        key="BatVolt",
        name="DC bus voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        converter="centi",
    ),
    ZendureSensorDescription(
        key="hvBatVolt",
        name="HV battery voltage (raw)",
        icon="mdi:flash",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    ZendureSensorDescription(
        key="FMVolt",
        name="Activation voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    # ── Thermal and RF ───────────────────────────────────────────────────
    ZendureSensorDescription(
        key="hyperTmp",
        name="Enclosure temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        converter="decikelvin",
    ),
    ZendureSensorDescription(
        key="rssi",
        name="Signal strength",
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="Fanspeed",
        translation_key="fan_speed_step",
        name="Fan speed step",
        device_class=SensorDeviceClass.ENUM,
        options=list(FAN_SPEED_MAP.values()),
        enum_map=FAN_SPEED_MAP,
        icon="mdi:fan",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="fanSpeed",
        name="Fan level",
        icon="mdi:fan",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # ── State enumerations ───────────────────────────────────────────────
    ZendureSensorDescription(
        key="packState",
        translation_key="battery_state",
        name="Battery state",
        device_class=SensorDeviceClass.ENUM,
        options=list(PACK_STATE_MAP.values()),
        enum_map=PACK_STATE_MAP,
        icon="mdi:battery-sync",
    ),
    ZendureSensorDescription(
        key="dcStatus",
        translation_key="dc_state",
        name="DC state",
        device_class=SensorDeviceClass.ENUM,
        options=list(DC_STATUS_MAP.values()),
        enum_map=DC_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="acStatus",
        translation_key="ac_state",
        name="AC state",
        device_class=SensorDeviceClass.ENUM,
        options=list(AC_STATUS_MAP.values()),
        enum_map=AC_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="pvStatus",
        translation_key="pv_state",
        name="PV state",
        device_class=SensorDeviceClass.ENUM,
        options=list(PV_STATUS_MAP.values()),
        enum_map=PV_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="socLimit",
        translation_key="soc_limit_status",
        name="SOC limit status",
        device_class=SensorDeviceClass.ENUM,
        options=list(SOC_LIMIT_MAP.values()),
        enum_map=SOC_LIMIT_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="socStatus",
        translation_key="soc_calibration_status",
        name="SOC calibration status",
        device_class=SensorDeviceClass.ENUM,
        options=list(SOC_STATUS_MAP.values()),
        enum_map=SOC_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="packNum",
        name="Pack count",              # "Battery module count" until v1.6.5
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="acCouplingState",
        name="AC coupling status",
        icon="mdi:transmission-tower",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="faultLevel",
        name="Fault level",
        icon="mdi:alert-circle-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    # ── Diagnostics, undocumented or low value ───────────────────────────
    _diag("IOTState", "IoT connection state"),
    _diag("OTAState", "OTA state"),
    _diag("LCNState", "LCN state"),
    _diag("bindstate", "Bind state"),
    _diag("factoryModeState", "Factory mode state"),
    _diag("VoltWakeup", "Voltage wake-up"),
    _diag("oldMode", "Legacy mode"),
    _diag("phaseSwitch", "Phase switch"),
    _diag("gridHdStatus", "Grid HD status"),
    _diag("offGridState", "Off-grid state"),
    _diag("powerhubStatus", "Powerhub status"),
    _diag("slaveAddr", "Slave address"),
    _diag("writeRsp", "Write response"),
    _diag("tsZone", "Timezone offset"),
    _diag("ts", "Device timestamp"),
    _diag("timestamp", "Report timestamp"),
    _diag("messageId", "Message id"),
    _diag("timeZone", "Timezone"),
)


METER_SENSORS: tuple[ZendureSensorDescription, ...] = (
    ZendureSensorDescription(
        key="total_power",
        name="Grid power total",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
    ),
    ZendureSensorDescription(
        key="a_aprt_power",
        name="Phase A apparent power",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ZendureSensorDescription(
        key="b_aprt_power",
        name="Phase B apparent power",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    ZendureSensorDescription(
        key="c_aprt_power",
        name="Phase C apparent power",
        native_unit_of_measurement=UnitOfApparentPower.VOLT_AMPERE,
        device_class=SensorDeviceClass.APPARENT_POWER,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _diag("meterType", "Meter type", enabled=True),
    _diag("protocolType", "Protocol type"),
)


PACK_SENSORS: tuple[ZendureSensorDescription, ...] = (
    ZendureSensorDescription(
        key="socLevel",
        name="State of charge",
        native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    _power("power", "Power"),
    ZendureSensorDescription(
        key="state",
        translation_key="pack_state",
        name="State",
        device_class=SensorDeviceClass.ENUM,
        options=list(PACK_STATE_MAP.values()),
        enum_map=PACK_STATE_MAP,
    ),
    ZendureSensorDescription(
        key="maxTemp",
        name="Temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        converter="decikelvin",
    ),
    ZendureSensorDescription(
        key="totalVol",
        name="Voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        converter="centi",
    ),
    ZendureSensorDescription(
        key="batcur",
        name="Current",
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        converter="signed_deci_amp",
    ),
    ZendureSensorDescription(
        key="maxVol",
        name="Max cell voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        suggested_display_precision=2,
        converter="centi",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="minVol",
        name="Min cell voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        suggested_display_precision=2,
        converter="centi",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _diag("sn", "Serial number"),
    _diag("softVersion", "Firmware version"),
    _diag("packType", "Type"),          # "Pack type" until v1.6.5: Pack N Type
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors for every reported key."""
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_PRICE:
        async_add_entities(price_entities(Platform.SENSOR, hass, entry))
        return
    stored = hass.data[DOMAIN][entry.entry_id]
    if isinstance(stored, ExternalMeter):
        async_add_entities(_external_sensors(stored, entry))
        return
    coordinator: ZendureCoordinator = stored
    device_id = coordinator.device_id or entry.unique_id or entry.entry_id
    model = entry.data.get(CONF_MODEL) or "SolarFlow"
    data = coordinator.data or {}

    all_descriptions = DEVICE_SENSORS + METER_SENSORS
    coordinator.register_known_keys({d.key for d in all_descriptions})
    coordinator.register_known_keys({d.key for d in PACK_SENSORS})

    entities: list[SensorEntity] = [
        ZendureSensor(coordinator, description, device_id, model)
        for description in all_descriptions
        if description.key in data
    ]

    for index in range(1, coordinator.pack_count + 1):
        for description in PACK_SENSORS:
            data_key = f"{PACK_PREFIX}{index}.{description.key}"
            if data_key not in data:
                continue
            entities.append(
                ZendurePackSensor(coordinator, description, device_id, model, index, data_key)
            )

        # Model and capacity are derived from the serial, so the entity exists
        # wherever a pack reports one.
        if f"{PACK_PREFIX}{index}.sn" in data:
            entities.append(
                ZendurePackModelSensor(coordinator, PACK_MODEL_DESCRIPTION, device_id, model, index)
            )

    runtime = hass.data[DOMAIN].get(f"{entry.entry_id}_runtime")
    if runtime is not None:
        entities.append(
            ZendureControllerSensor(
                coordinator, CONTROLLER_DESCRIPTION, device_id, model,
                runtime["controller"],
            )
        )

    if "packInputPower" in data and "outputPackPower" in data:
        entities.append(
            ZendureBatteryPowerSensor(
                coordinator, BATTERY_POWER_DESCRIPTION, device_id, model
            )
        )

    entities += _energy_sensors(coordinator, device_id, model)
    entities += _efficiency_sensors(coordinator, device_id, model)
    if runtime is not None:
        entities += _measure_sensors(coordinator, device_id, model, runtime["controller"])

    _LOGGER.debug("Adding %d sensors (%d packs)", len(entities), coordinator.pack_count)
    align_entity_ids(hass, "sensor", entities, device_prefix(hass, device_id))
    async_add_entities(entities)


def _energy_sensors(
    coordinator: ZendureCoordinator, device_id: str, model: str
) -> list[SensorEntity]:
    """Energy counters, so the device can feed the Energy dashboard.

    The device publishes no cumulative counters of its own, so these are
    integrated from power. Sources are AC-side fields: that is the side the
    meter shares a connection point with, and the side the Energy dashboard
    reasons about. Conversion loss is therefore visible as the gap between the
    grid figures and the battery figures, rather than absorbed into them.
    """
    data = coordinator.data or {}
    descriptions = (
        METER_ENERGY_SENSORS
        if coordinator.device_type == DEVICE_TYPE_METER
        else BATTERY_ENERGY_SENSORS
    )
    return [
        ZendureEnergySensor(coordinator, d, device_id, model)
        for d in descriptions
        if d.source in data
    ]


class ZendureSensor(ZendureEntity, SensorEntity):
    """A single device-level sensor."""

    entity_description: ZendureSensorDescription

    @property
    def native_value(self) -> Any:
        value = self.raw_value
        if value is None:
            return None

        description = self.entity_description
        if description.enum_map is not None:
            return map_enum(description.enum_map, value)
        if description.key == "acCouplingState":
            flags = decode_ac_coupling(value)
            return ", ".join(flags) if flags else "none"
        return convert(description.converter, value)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self.entity_description.key != "acCouplingState":
            return None
        return {"raw": self.raw_value, "flags": decode_ac_coupling(self.raw_value)}


class ZendurePackSensor(ZendureSensor):
    """A sensor scoped to one battery pack.

    Terms (v1.6.5): *battery* is the SolarFlow itself, with its inverter; a
    *pack* is one entry of packData in the zenSDK report, numbered 1 to N.
    """

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: ZendureSensorDescription,
        device_id: str,
        model: str,
        index: int,
        data_key: str,
    ) -> None:
        super().__init__(coordinator, description, device_id, model, data_key)
        self._attr_unique_id = f"{device_id}_{PACK_PREFIX}{index}_{description.key}"
        # "Pack {index} …" (v1.6.5): shown through the translations, and in
        # English as suggested_object_id, so the entity ids read pack_N_ in
        # every language. Ids from before v1.6.5 (battery_N_) are moved over
        # once by migrate_pack_entity_ids.
        self._set_name(
            description, "pack_", {"index": str(index)}, f"Pack {index} {description.name}"
        )


PACK_MODEL_DESCRIPTION = ZendureSensorDescription(
    key="model",
    name="Model",
    icon="mdi:battery-heart-variant",
    entity_category=EntityCategory.DIAGNOSTIC,
)


class ZendurePackModelSensor(ZendureSensor):
    """Which battery pack this is, derived from its serial and packType.

    The pack reports no model name of its own. The derivation lives in
    registry.pack_model; the nominal capacity travels as an attribute so the
    state stays a short label.
    """

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: ZendureSensorDescription,
        device_id: str,
        model: str,
        index: int,
    ) -> None:
        self._prefix = f"{PACK_PREFIX}{index}."
        super().__init__(coordinator, description, device_id, model, f"{self._prefix}sn")
        self._attr_unique_id = f"{device_id}_{PACK_PREFIX}{index}_model"
        # "Pack {index} …" (v1.6.5): shown through the translations, and in
        # English as suggested_object_id, so the entity ids read pack_N_ in
        # every language. Ids from before v1.6.5 (battery_N_) are moved over
        # once by migrate_pack_entity_ids.
        self._set_name(description, "pack_", {"index": str(index)}, f"Pack {index} Model")

    def _derived(self) -> tuple[str, float | None]:
        data = self.coordinator.data or {}
        return pack_model(data.get(f"{self._prefix}sn"), data.get(f"{self._prefix}packType"))

    @property
    def native_value(self) -> str:
        return self._derived()[0]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        name, kwh = self._derived()
        return {"capacity_kwh": kwh}


# ── Controller status ────────────────────────────────────────────────────

CONTROLLER_DESCRIPTION = ZendureSensorDescription(
    key="controller_status",
    translation_key="controller_status",
    name="Controller status",
    icon="mdi:cog-sync-outline",
)


class ZendureControllerSensor(ZendureSensor):
    """What the controller last decided, with the reasoning as attributes.

    This is the entity to look at when the battery does something unexpected:
    the state says what happened, the attributes say why. It also carries the
    version of the integration that runs (v1.6.9): HACS shows the version it
    last downloaded, which differs after a manual install.
    """

    _unrecorded_attributes = frozenset({"integration_version"})

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: ZendureSensorDescription,
        device_id: str,
        model: str,
        controller,
    ) -> None:
        super().__init__(coordinator, description, device_id, model)
        self._controller = controller

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self):
        return self._controller.state.status

    @property
    def extra_state_attributes(self):
        return {**self._controller.state.attributes, "integration_version": INTEGRATION_VERSION}


# ── Derived power ────────────────────────────────────────────────────────


BATTERY_POWER_DESCRIPTION = SensorEntityDescription(
    key="battery_power",
    name="Battery power",
    native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER,
    state_class=SensorStateClass.MEASUREMENT,
    icon="mdi:battery-charging-50",
)


class ZendureBatteryPowerSensor(ZendureEntity, SensorEntity):
    """Battery power as one signed figure: discharge minus charge.

    The device reports the two directions as separate positive readings, which
    the Energy dashboard accepts, but a single signed sensor is what its power
    flow needs. Selecting "two sensors" there makes Home Assistant derive the
    same figure into a helper with a 118-character entity id; this is that
    calculation, done once, under a readable name.

    Positive is discharging, matching the battery_power attribute on the
    controller status sensor. Two sensors of the same name with opposite signs
    is a trap worth not setting.
    """

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: SensorEntityDescription,
        device_id: str,
        model: str,
    ) -> None:
        super().__init__(coordinator, description, device_id, model)

    @property
    def available(self) -> bool:
        # Not backed by a single coordinator key, so the base check does not
        # apply. Without this override the base class looks for a key named
        # "battery_power" in the device payload, which cannot exist: the value
        # is computed from packInputPower and outputPackPower. The entity was
        # therefore created and then reported unavailable for its whole life,
        # which hides it from the entity pickers rather than showing an error.
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        discharge = data.get("packInputPower")
        charge = data.get("outputPackPower")
        if discharge is None or charge is None:
            return None
        try:
            return float(discharge) - float(charge)
        except (TypeError, ValueError):
            return None


# ── Energy counters ──────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class ZendureEnergyDescription(SensorEntityDescription):
    """An energy counter integrated from a power reading.

    ``source`` is the coordinator key holding the watts; ``positive`` picks
    which side of a signed reading this counter accumulates, and is ignored
    for sources that are already one-directional.
    """

    source: str
    positive: bool = True


def _energy(key: str, name: str, source: str, positive: bool, icon: str) -> ZendureEnergyDescription:
    return ZendureEnergyDescription(
        key=key,
        name=name,
        source=source,
        positive=positive,
        icon=icon,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
    )


BATTERY_ENERGY_SENSORS: tuple[ZendureEnergyDescription, ...] = (
    _energy("energy_charged", "Energy charged", "outputPackPower", True, "mdi:battery-plus-variant"),
    _energy("energy_discharged", "Energy discharged", "packInputPower", True, "mdi:battery-minus-variant"),
    _energy("energy_pv", "PV energy", "solarInputPower", True, "mdi:solar-power-variant"),
)

METER_ENERGY_SENSORS: tuple[ZendureEnergyDescription, ...] = (
    _energy("energy_imported", "Energy imported", "total_power", True, "mdi:transmission-tower-import"),
    _energy("energy_exported", "Energy exported", "total_power", False, "mdi:transmission-tower-export"),
)


class ZendureEnergySensor(ZendureEntity, SensorEntity, RestoreEntity):
    """A kWh counter built by integrating a power reading over time."""

    entity_description: ZendureEnergyDescription

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: ZendureEnergyDescription,
        device_id: str,
        model: str,
    ) -> None:
        super().__init__(coordinator, description, device_id, model)
        self._accumulator = EnergyAccumulator()
        self._gate = WriteGate()

    async def async_added_to_hass(self) -> None:
        """Pick up where the previous run left off."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state not in (None, "unknown", "unavailable"):
            try:
                self._accumulator.restore(float(last.state))
            except (TypeError, ValueError):
                _LOGGER.debug("Could not restore %s from %r", self.entity_id, last.state)
        # Fold in the reading that is already present, so the first poll after a
        # restart does not start from a blank previous value.
        self._accumulate()
        # The total between two spaced writes must not be lost on a stop.
        self.async_on_remove(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._flush)
        )

    @callback
    def _flush(self, _event: Event | None = None) -> None:
        self._gate.mark(self.native_value, self.available, time.monotonic())
        self.async_write_ha_state()

    async def async_internal_will_remove_from_hass(self) -> None:
        # A reload: the restore data is taken from the state written last.
        self._flush()
        await super().async_internal_will_remove_from_hass()

    @property
    def available(self) -> bool:
        # Not backed by a single coordinator key, so the base check does not apply.
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float:
        return round(self._accumulator.total_kwh, 6)

    @callback
    def _handle_coordinator_update(self) -> None:
        # Accumulated on every poll, written in steps (v1.6.7).
        self._accumulate()
        now = time.monotonic()
        if self._gate.due(self.native_value, self.available, now):
            self._gate.mark(self.native_value, self.available, now)
            self.async_write_ha_state()

    def _accumulate(self) -> None:
        watts = self._current_power()
        if watts is not None:
            self._accumulator.add(watts)

    def _current_power(self) -> float | None:
        """The magnitude, in watts, that this counter accumulates.

        Sources are AC-side properties, matching the side the meter is on and
        the side the Energy dashboard reasons about. The DC pack reading is
        deliberately not used here: it differs from the AC side by the
        converter's own consumption, so DC-based counters would quietly absorb
        the conversion loss instead of leaving it visible between the two.

        The loss is surfaced separately, as an efficiency percentage.
        """
        data = self.coordinator.data or {}
        description = self.entity_description

        raw = data.get(description.source)
        if raw is None:
            return None

        # A signed source (the meter) splits into two counters by direction;
        # an unsigned one (the device's own power fields) is taken as is.
        if description.source == "total_power":
            return split_signed(raw, description.positive)

        try:
            return max(0.0, float(raw))
        except (TypeError, ValueError):
            return None


# ── Conversion efficiency ────────────────────────────────────────────────
# The AC and DC readings differ by whatever the converter spends on itself and
# on conversion. Rather than let that vanish between two counters, it is shown
# as a percentage. Charging compares what reached the cells against what came
# off the grid; discharging compares what reached the house against what left
# the cells.


@dataclass(frozen=True, kw_only=True)
class ZendureEfficiencyDescription(SensorEntityDescription):
    """Ratio of two power readings, expressed as a percentage."""

    ac_key: str
    pack_state: int


EFFICIENCY_SENSORS: tuple[ZendureEfficiencyDescription, ...] = (
    ZendureEfficiencyDescription(
        key="charge_efficiency",
        name="Charge efficiency",
        ac_key="outputPackPower",
        pack_state=1,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:percent-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureEfficiencyDescription(
        key="discharge_efficiency",
        name="Discharge efficiency",
        ac_key="packInputPower",
        pack_state=2,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:percent-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


def _efficiency_sensors(
    coordinator: ZendureCoordinator, device_id: str, model: str
) -> list[SensorEntity]:
    """Efficiency needs both an AC field and pack data to compare."""
    data = coordinator.data or {}
    if coordinator.device_type == DEVICE_TYPE_METER or "pack1.power" not in data:
        return []
    return [
        ZendureEfficiencySensor(coordinator, d, device_id, model)
        for d in EFFICIENCY_SENSORS
        if d.ac_key in data
    ]


class ZendureEfficiencySensor(ZendureEntity, SensorEntity):
    """Conversion efficiency for one direction, in percent."""

    entity_description: ZendureEfficiencyDescription

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data or {}
        description = self.entity_description

        try:
            ac = float(data.get(description.ac_key) or 0)
        except (TypeError, ValueError):
            return None

        # Only packs in the matching state contribute, so a mixed bank does not
        # dilute the ratio with cells that are doing nothing.
        dc = 0.0
        for index in range(1, self.coordinator.pack_count + 1):
            if data.get(f"{PACK_PREFIX}{index}.state") != description.pack_state:
                continue
            try:
                dc += abs(float(data.get(f"{PACK_PREFIX}{index}.power") or 0))
            except (TypeError, ValueError):
                continue

        if ac <= 0 or dc <= 0:
            return None

        # Charging: the cells receive less than the grid delivered.
        # Discharging: the house receives less than the cells gave up.
        ratio = (dc / ac) if description.pack_state == 1 else (ac / dc)
        return round(min(100.0, max(0.0, ratio * 100.0)), 1)



# ── Measured diagnostics (v1.6.3) ────────────────────────────────────────
# What the battery actually does, measured while it runs: see measure.py. The
# two sensors above are a single moment of the converter; these are whole
# blocks, cycles and days, to check the settings of price control against.


@dataclass(frozen=True, kw_only=True)
class ZendureMeasureDescription(SensorEntityDescription):
    """A figure of the battery's BatteryMeasure, and its basis."""

    value: Any = None
    attributes: Any = None


def _blocks(direction: str):
    return lambda m: {"blocks": list(m.blocks[direction]), "basis": "AC/DC per block of at least 1 kWh, last 5"}


MEASURE_SENSORS: tuple[ZendureMeasureDescription, ...] = (
    ZendureMeasureDescription(
        key="measured_charge_efficiency",
        name="Measured charge efficiency",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-arrow-up-outline",
        value=lambda m: m.block_efficiency("charge"),
        attributes=_blocks("charge"),
    ),
    ZendureMeasureDescription(
        key="measured_discharge_efficiency",
        name="Measured discharge efficiency",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-arrow-down-outline",
        value=lambda m: m.block_efficiency("discharge"),
        attributes=_blocks("discharge"),
    ),
    ZendureMeasureDescription(
        key="round_trip_ac",
        name="Round trip efficiency AC",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-sync-outline",
        value=lambda m: m.round_trip("ac"),
        attributes=lambda m: {"cycles": list(m.cycles), "basis": "AC out / AC in between two moments at the same SOC bound, last 5"},
    ),
    ZendureMeasureDescription(
        key="round_trip_dc",
        name="Round trip efficiency DC",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:battery-sync",
        value=lambda m: m.round_trip("dc"),
        attributes=lambda m: {"cycles": len(m.cycles), "basis": "DC out / DC in over the same cycles: cells and battery management"},
    ),
    ZendureMeasureDescription(
        key="usable_capacity",
        name="Usable capacity",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY_STORAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        icon="mdi:battery-high",
        value=lambda m: m.usable_capacity,
        attributes=lambda m: {
            "measurements": list(m.capacities),
            "nominal_kwh": m.capacity_kwh,
            "basis": "DC out from the upper to the lower SOC bound in one discharge",
        },
    ),
    ZendureMeasureDescription(
        key="full_cycles",
        name="Full cycles",
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        icon="mdi:counter",
        value=lambda m: m.full_cycles,
        attributes=lambda m: {
            "dc_out_kwh": round(m.dc_out, 2),
            "nominal_kwh": m.capacity_kwh,
            "since": m.since,
        },
    ),
    ZendureMeasureDescription(
        key="standby_draw",
        name="Standby draw",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:sleep",
        value=lambda m: m.standby_draw,
        attributes=lambda m: {
            "rest_hours": round(m.standby.seconds() / 3600, 1),
            "p90_w": m.standby.quantile(0.9),
            "too_high": m.standby_alert,
            "basis": "median DC draw at rest (both limits 0), last 24 h, by time",
        },
    ),
    ZendureMeasureDescription(
        key="response_time",
        name="Response time",
        native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        icon="mdi:timer-outline",
        value=lambda m: m.response_time,
        attributes=lambda m: {
            "start_s": m.response_median("start"),
            "stop_s": m.response_median("stop"),
            "steps": len(m.responses),
            "timeouts": m.response_timeouts,
            "basis": "limit write of 200 W or more until the grid has moved 90 % of it, median of the last 20",
        },
    ),
    ZendureMeasureDescription(
        key="lost_writes",
        name="Lost writes",
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:email-remove-outline",
        value=lambda m: m.lost_writes,
        attributes=lambda m: {
            "checked": m.checked_writes,
            # A copy: Home Assistant sends the frontend only the attributes that
            # changed, and a dict changed in place compares equal to itself.
            "by_gap": {gap: dict(counts) for gap, counts in m.writes.items()},
            "recent": list(m.lost_recent),
            "basis": "a limit write the device's next report does not show, by the time since the command before it",
        },
    ),
    ZendureMeasureDescription(
        key="grid_tracking_error",
        name="Grid tracking error",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:target",
        value=lambda m: m.tracking_error,
        attributes=lambda m: {
            "p90_w": m.tracking.quantile(0.9),
            "hours": round(m.tracking.seconds() / 3600, 1),
            "basis": "median |grid - buffer| while a smart mode balances and the battery can follow, last 24 h, by time",
        },
    ),
)


def _measure_sensors(
    coordinator: ZendureCoordinator, device_id: str, model: str, controller
) -> list[SensorEntity]:
    """Measured diagnostics need pack data, like the efficiency sensors."""
    data = coordinator.data or {}
    if coordinator.device_type == DEVICE_TYPE_METER or "pack1.power" not in data:
        return []
    return [ZendureMeasureSensor(coordinator, d, device_id, model, controller) for d in MEASURE_SENSORS]


class ZendureMeasureSensor(ZendureEntity, SensorEntity):
    """One measured diagnostic: unknown until there is something to measure."""

    entity_description: ZendureMeasureDescription
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _unrecorded_attributes = frozenset({"blocks", "cycles", "measurements", "basis", "recent"})

    def __init__(self, coordinator, description, device_id, model, controller) -> None:
        super().__init__(coordinator, description, device_id, model)
        self._controller = controller

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self._controller.measure)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        attributes = self.entity_description.attributes
        return attributes(self._controller.measure) if attributes else None


# ── External P1 meter ────────────────────────────────────────────────────
# One device per external meter entry, always named EXTERNAL_DEVICE_NAME, so
# the entity ids are sensor.external_p1_meter_* whatever the reader is and the
# dashboards need no editing per meter type.

EXTERNAL_TICK = timedelta(seconds=5)


@dataclass(frozen=True, kw_only=True)
class ExternalSensorDescription(SensorEntityDescription):
    """Description with the function that reads the value off the meter."""

    value: Any = None
    live_only: bool = False


EXTERNAL_SENSORS: tuple[ExternalSensorDescription, ...] = (
    ExternalSensorDescription(
        key="power",
        name="Power",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower",
        value=lambda m: m.recent_power(10**9),
        live_only=True,
    ),
    ExternalSensorDescription(
        key="energy_imported",
        name="Energy imported",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value=lambda m: m.energy()[0],
    ),
    ExternalSensorDescription(
        key="energy_exported",
        name="Energy exported",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=3,
        value=lambda m: m.energy()[1],
    ),
    ExternalSensorDescription(
        key="status",
        name="Status",
        translation_key="external_status",
        device_class=SensorDeviceClass.ENUM,
        options=[EXTERNAL_STATUS_LIVE, EXTERNAL_STATUS_STALE, EXTERNAL_STATUS_UNREACHABLE],
        icon="mdi:access-point-check",
        value=lambda m: m.status,
    ),
)

# Readers that report cumulative registers. The others get no energy sensors
# rather than two that are unavailable for ever.
_ENERGY_TYPES = {"youless", "homewizard_v1"}


def _external_sensors(meter: ExternalMeter, entry: ConfigEntry) -> list[SensorEntity]:
    return [
        ExternalMeterSensor(meter, entry, description)
        for description in EXTERNAL_SENSORS
        if not description.key.startswith("energy_") or meter.kind in _ENERGY_TYPES
    ]


class ExternalMeterSensor(TranslatedName, SensorEntity):
    """One reading of an external P1 meter.

    Updated on every new telegram, and every five seconds besides, so the
    status keeps moving while the meter is silent — which is when it matters.
    The energy registers are written in steps (v1.6.7); the age of the last
    telegram is in the diagnostics download, no longer a sensor.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    entity_description: ExternalSensorDescription
    # The phases change with every reading; the history needs only the total.
    _unrecorded_attributes = frozenset({"phase_1_w", "phase_2_w", "phase_3_w"})

    def __init__(
        self, meter: ExternalMeter, entry: ConfigEntry, description: ExternalSensorDescription
    ) -> None:
        self.entity_description = description
        self._meter = meter
        self._gate = WriteGate() if description.key.startswith("energy_") else None
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._set_name(description, "external_")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"external_{entry.entry_id}")},
            # Entries from v1.5.2 have no stored name; they were the only one.
            name=entry.data.get(CONF_DEVICE_NAME, EXTERNAL_DEVICE_NAME),
            manufacturer=meter.manufacturer,
            model=EXTERNAL_KIND_LABELS.get(meter.kind, meter.kind),
            configuration_url=meter.configuration_url,
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._meter.async_add_listener(self._refresh))
        self.async_on_remove(
            async_track_time_interval(self.hass, self._tick, EXTERNAL_TICK)
        )

    @callback
    def _refresh(self) -> None:
        self._write()

    @callback
    def _tick(self, _now: Any) -> None:
        self._write()

    @callback
    def _write(self) -> None:
        """Every reading for power and status; a meter register in steps (v1.6.7)."""
        if self._gate is not None:
            now = time.monotonic()
            value, available = self.native_value, self.available
            if not self._gate.due(value, available, now):
                return
            self._gate.mark(value, available, now)
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        if self.entity_description.live_only:
            return self._meter.status == EXTERNAL_STATUS_LIVE
        return self.native_value is not None

    @property
    def native_value(self) -> Any:
        return self.entity_description.value(self._meter)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Status: how often the meter really reports. Power: its phases (v1.6.5)."""
        key = self.entity_description.key
        if key == "status":
            interval = self._meter.update_interval
            return {"update_interval": round(interval, 1) if interval is not None else None}
        if key == "power":
            phases = {
                k: v for k, v in (self._meter.data or {}).items()
                if k.startswith("phase_") and k.endswith("_w")
            }
            return phases or None
        return None
