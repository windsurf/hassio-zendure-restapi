"""Sensor platform for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass
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
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODEL, DOMAIN, PACK_PREFIX
from .coordinator import ZendureCoordinator
from .entity import ZendureEntity
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
    _power("chargeMaxLimit", "Charge power limit (device)", entity_category=EntityCategory.DIAGNOSTIC),
    ZendureSensorDescription(
        key="remainOutTime",
        name="Remaining discharge time",
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
        name="Battery state",
        device_class=SensorDeviceClass.ENUM,
        options=list(PACK_STATE_MAP.values()),
        enum_map=PACK_STATE_MAP,
        icon="mdi:battery-sync",
    ),
    ZendureSensorDescription(
        key="dcStatus",
        name="DC state",
        device_class=SensorDeviceClass.ENUM,
        options=list(DC_STATUS_MAP.values()),
        enum_map=DC_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="acStatus",
        name="AC state",
        device_class=SensorDeviceClass.ENUM,
        options=list(AC_STATUS_MAP.values()),
        enum_map=AC_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="pvStatus",
        name="PV state",
        device_class=SensorDeviceClass.ENUM,
        options=list(PV_STATUS_MAP.values()),
        enum_map=PV_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="socLimit",
        name="SOC limit status",
        device_class=SensorDeviceClass.ENUM,
        options=list(SOC_LIMIT_MAP.values()),
        enum_map=SOC_LIMIT_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="socStatus",
        name="SOC calibration status",
        device_class=SensorDeviceClass.ENUM,
        options=list(SOC_STATUS_MAP.values()),
        enum_map=SOC_STATUS_MAP,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="packNum",
        name="Battery module count",
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
        converter="centi",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    ZendureSensorDescription(
        key="minVol",
        name="Min cell voltage",
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        device_class=SensorDeviceClass.VOLTAGE,
        converter="centi",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _diag("sn", "Serial number"),
    _diag("softVersion", "Firmware version"),
    _diag("packType", "Pack type"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create sensors for every reported key."""
    coordinator: ZendureCoordinator = hass.data[DOMAIN][entry.entry_id]
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

    runtime = hass.data[DOMAIN].get(f"{entry.entry_id}_runtime")
    if runtime is not None:
        entities.append(
            ZendureControllerSensor(
                coordinator, CONTROLLER_DESCRIPTION, device_id, model,
                runtime["controller"],
            )
        )

    _LOGGER.debug("Adding %d sensors (%d packs)", len(entities), coordinator.pack_count)
    async_add_entities(entities)


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
    """A sensor scoped to one battery pack."""

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
        self._attr_name = f"Battery {index} {description.name}"


# ── Controller status ────────────────────────────────────────────────────

CONTROLLER_DESCRIPTION = ZendureSensorDescription(
    key="controller_status",
    name="Controller status",
    icon="mdi:cog-sync-outline",
)


class ZendureControllerSensor(ZendureSensor):
    """What the controller last decided, with the reasoning as attributes.

    This is the entity to look at when the battery does something unexpected:
    the state says what happened, the attributes say why.
    """

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
        return self._controller.state.attributes
