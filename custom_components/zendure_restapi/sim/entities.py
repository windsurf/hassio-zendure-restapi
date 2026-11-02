"""Entities of a Virtual cluster entry (v1.7.1).

The cluster device carries the group's settings and figures, as the battery
cluster does, plus the settings of the test: Clones, Load factor, the SoC
offsets of clones 2 and 3, and Reset. Each clone is a device of its own with
only what the dashboard shows: state of charge, battery power, controller
status, the two limits and its response time. No energy counters, so the
Energy dashboard never sees a battery that does not exist.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.components.number import NumberEntityDescription, NumberMode
from homeassistant.components.select import SelectEntityDescription
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, Platform, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo

from ..const import (
    DOMAIN,
    OPT_CLONES,
    OPT_LOAD_FACTOR,
    OPT_OPERATION_MODE,
    OPT_SOC_OFFSET_2,
    OPT_SOC_OFFSET_3,
    OPT_RESPONSE_OFFSET_2,
    OPT_RESPONSE_OFFSET_3,
    VIRTUAL_MODEL,
    VIRTUAL_MODES,
)
from ..cluster_entities import (
    CLUSTER_NUMBERS,
    REBUILD_DESCRIPTION,
    RebuildButton,
    MEMBERS_DESCRIPTION,
    POWER_DESCRIPTION,
    SOC_DESCRIPTION,
    STATUS_DESCRIPTION,
    ClusterEntity,
    ClusterNumber,
    ClusterSelect,
    ClusterStatus,
    ClusterValue,
)
from .cluster import Clone, VirtualCluster

MODE_DESCRIPTION = SelectEntityDescription(
    key="operation_mode", name="Operation mode", options=list(VIRTUAL_MODES), icon="mdi:state-machine",
)

TEST_NUMBERS: tuple[tuple[NumberEntityDescription, str], ...] = (
    (NumberEntityDescription(
        key="clones", name="Clones", native_min_value=1, native_max_value=3, native_step=1,
        mode=NumberMode.BOX, icon="mdi:content-copy",
    ), OPT_CLONES),
    (NumberEntityDescription(
        key="load_factor", name="Load factor", native_min_value=1, native_max_value=3, native_step=0.5,
        mode=NumberMode.BOX, icon="mdi:multiplication",
    ), OPT_LOAD_FACTOR),
    (NumberEntityDescription(
        key="soc_offset_2", name="SoC offset clone 2", native_unit_of_measurement=PERCENTAGE,
        native_min_value=-50, native_max_value=50, native_step=1, mode=NumberMode.BOX,
        icon="mdi:battery-arrow-up-outline",
    ), OPT_SOC_OFFSET_2),
    (NumberEntityDescription(
        key="soc_offset_3", name="SoC offset clone 3", native_unit_of_measurement=PERCENTAGE,
        native_min_value=-50, native_max_value=50, native_step=1, mode=NumberMode.BOX,
        icon="mdi:battery-arrow-down-outline",
    ), OPT_SOC_OFFSET_3),
    (NumberEntityDescription(
        key="response_offset_2", name="Response offset clone 2", native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0, native_max_value=10, native_step=0.5, mode=NumberMode.BOX,
        icon="mdi:timer-sand",
    ), OPT_RESPONSE_OFFSET_2),
    (NumberEntityDescription(
        key="response_offset_3", name="Response offset clone 3", native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=0, native_max_value=10, native_step=0.5, mode=NumberMode.BOX,
        icon="mdi:timer-sand-complete",
    ), OPT_RESPONSE_OFFSET_3),
)

# The virtual P1 meter, a device of its own like the real P1 (v1.7.1).
VIRTUAL_P1_NAME = "Virtual P1 meter"
_W = dict(native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER,
          state_class=SensorStateClass.MEASUREMENT)
VIRTUAL_P1_DESCRIPTIONS = (
    (SensorEntityDescription(key="virtual_p1_power", name="Power", icon="mdi:transmission-tower", **_W), "power"),
    (SensorEntityDescription(key="virtual_p1_house_load", name="House load", icon="mdi:home-lightning-bolt", **_W), "house"),
    (SensorEntityDescription(key="virtual_p1_real_grid", name="Real grid", icon="mdi:transmission-tower-import", **_W), "real_grid"),
    (SensorEntityDescription(key="virtual_p1_load_factor", name="Load factor", icon="mdi:multiplication"), "load_factor"),
)
RESET_DESCRIPTION = ButtonEntityDescription(key="reset", name="Reset", icon="mdi:restart")

LIMIT_DESCRIPTIONS = (
    SensorEntityDescription(
        key="inputLimit", name="Charge limit", native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-up",
    ),
    SensorEntityDescription(
        key="outputLimit", name="Discharge limit", native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:battery-arrow-down",
    ),
)


def _virtual_device(entity: Any) -> None:
    info = entity._attr_device_info
    if info is not None:
        info["model"] = VIRTUAL_MODEL


def _clone_device(entity: Any, clone: Clone) -> None:
    """A clone's device: its own name, marked as simulated."""
    info = entity._attr_device_info
    if info is not None:
        info["name"] = clone.name
        info["model"] = f"{clone.model} (SIMULATED)"
        info["configuration_url"] = None


def virtual_entities(platform: Platform, hass: HomeAssistant, entry: ConfigEntry) -> list[Any]:
    """The entities of one platform for a Virtual cluster entry."""
    vc: VirtualCluster = hass.data[DOMAIN][entry.entry_id]
    entities: list[Any] = []
    if platform == Platform.SELECT:
        entities = [ClusterSelect(entry, vc, MODE_DESCRIPTION, vc.settings, OPT_OPERATION_MODE)]
    elif platform == Platform.NUMBER:
        entities = [ClusterNumber(entry, vc, d, vc.settings, key) for d, key in CLUSTER_NUMBERS]
        for description, key in TEST_NUMBERS:
            cls = FloatNumber if key in (OPT_LOAD_FACTOR, OPT_RESPONSE_OFFSET_2, OPT_RESPONSE_OFFSET_3) else ClusterNumber
            entities.append(cls(entry, vc, description, vc.settings, key))
    elif platform == Platform.BUTTON:
        entities = [ResetButton(entry, vc, RESET_DESCRIPTION), RebuildButton(entry, vc, REBUILD_DESCRIPTION)]
    elif platform == Platform.SENSOR:
        entities = [
            ClusterStatus(entry, vc, STATUS_DESCRIPTION),
            ClusterValue(entry, vc, POWER_DESCRIPTION, lambda f: f.group_power, step=50),
            ClusterValue(entry, vc, SOC_DESCRIPTION, lambda f: f.group_soc, {"spread": lambda f: f.soc_spread}, step=0.5),
            ClusterValue(
                entry, vc, MEMBERS_DESCRIPTION, lambda f: sum(1 for r in f.readings if r.available),
                {"configured": lambda f: len(f.members)},
            ),
        ]
        p1 = [VirtualP1Sensor(entry, vc, d, field) for d, field in VIRTUAL_P1_DESCRIPTIONS]
        entities += _clone_sensors(vc)
    for entity in entities:
        if isinstance(entity, ClusterEntity):
            _virtual_device(entity)
    if platform == Platform.SENSOR:
        entities += p1
    return entities


def _clone_sensors(vc: VirtualCluster) -> list[Any]:
    # The battery's own entity classes, on the clone's coordinator and controller.
    from ..sensor import (
        BATTERY_POWER_DESCRIPTION,
        CONTROLLER_DESCRIPTION,
        DEVICE_SENSORS,
        MEASURE_SENSORS,
        ZendureBatteryPowerSensor,
        ZendureControllerSensor,
        ZendureMeasureSensor,
        ZendureSensor,
    )

    class CloneLimitSensor(ZendureSensor):
        """The limit the clone's device holds, as its controller wrote it."""

        @property
        def native_value(self) -> Any:
            try:
                return int(self.raw_value)
            except (TypeError, ValueError):
                return None

    soc = next(d for d in DEVICE_SENSORS if d.key == "electricLevel")
    response = tuple(d for d in MEASURE_SENSORS if d.key == "response_time")
    entities: list[Any] = []
    for clone in vc.clones:
        coordinator, device_id, model = clone.coordinator, clone.device_id, clone.model
        made: list[Any] = [
            ZendureSensor(coordinator, soc, device_id, model),
            ZendureBatteryPowerSensor(coordinator, BATTERY_POWER_DESCRIPTION, device_id, model),
            ZendureControllerSensor(coordinator, CONTROLLER_DESCRIPTION, device_id, model, clone.controller),
            *(CloneLimitSensor(coordinator, d, device_id, model) for d in LIMIT_DESCRIPTIONS),
            *(ZendureMeasureSensor(coordinator, d, device_id, model, clone.controller) for d in response),
        ]
        for entity in made:
            _clone_device(entity, clone)
        entities += made
    return entities


class FloatNumber(ClusterNumber):
    """A setting with a decimal step."""

    @property
    def native_value(self) -> float:
        return float(self._settings.get_float(self._key))

    async def async_set_native_value(self, value: float) -> None:
        self._settings.set(self._key, round(float(value), 1))
        self.async_write_ha_state()


class VirtualP1Sensor(ClusterEntity, SensorEntity):
    """One reading of the virtual P1 meter: what the clones steer on, and where it comes from.

    power      = house load x Load factor - what the clones deliver
    house load = real grid + AC power of the real battery
    """

    def __init__(self, entry: ConfigEntry, cluster: VirtualCluster, description: SensorEntityDescription,
                 field: str) -> None:
        super().__init__(entry, cluster, description)
        self._field = field
        self._entry_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"virtual_p1_{entry.entry_id}")},
            name=VIRTUAL_P1_NAME,
            model=f"{VIRTUAL_P1_NAME} (SIMULATED)",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # Under the Virtual cluster, without DeviceInfo["via_device"] (deprecated in HA 2026.8).
        from homeassistant.helpers import device_registry as dr

        from ..compat import link_under

        entry_id = self._entry_id
        link_under(dr.async_get(self.hass), (DOMAIN, f"virtual_p1_{entry_id}"), (DOMAIN, f"cluster_{entry_id}"), entry_id)

    @property
    def available(self) -> bool:
        meter = self._cluster.meter
        return meter is not None and meter.recent_power(10) is not None

    @property
    def native_value(self) -> float | None:
        if self._field == "load_factor":
            return self._cluster.load_factor()
        meter = self._cluster.meter
        value = (meter.data or {}).get(self._field) if meter is not None else None
        return None if value is None else round(float(value))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._field != "power":
            return None
        return {"source": self._cluster.source_name, "telegrams": getattr(self._cluster.meter, "telegrams", 0)}

    def _due(self) -> bool:
        return True


class ResetButton(ClusterEntity, ButtonEntity):
    """Every clone back to the start; the twin on the real state of charge."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self._cluster.async_reset()
