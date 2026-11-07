"""Entities of a Virtual cluster entry (v1.7.1).

The cluster device carries the group's settings and figures, as the battery
cluster does, plus the settings of the test: Copies, Load factor and Reset.
Each copy is a device of its own (Virtual copy n, since v1.7.3 whatever the
model; until v1.7.2 Virtual Mix n) with what the dashboard
shows: state of charge, battery power, controller status, the two limits and
its response time, and since v1.7.3 its own settings: Max share, SoC
offset and Extra response time. No energy counters, so the Energy dashboard
never sees a battery that does not exist.

Names since v1.7.3: *Copies* (was Clones), the settings of a copy on the
copy's device without "clone n" in the name, *Grid power* and *Real grid
power* on the Virtual P1 meter (were Power and Real grid), and the Load factor
of the meter as an attribute of Grid power instead of a sensor of its own.
migrate_entity_ids() moves the entity ids of an existing installation along.
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
    OPT_MAX_SHARE_CLONES,
    MAX_SHARE_LIMIT,
    OPT_OPERATION_MODE,
    OPT_SOC_OFFSET_1,
    OPT_SOC_OFFSET_2,
    OPT_SOC_OFFSET_3,
    OPT_RESPONSE_OFFSET_1,
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
        key="clones", name="Copies", native_min_value=1, native_max_value=3, native_step=1,
        mode=NumberMode.BOX, icon="mdi:content-copy",
    ), OPT_CLONES),
    (NumberEntityDescription(
        key="load_factor", name="Load factor", native_min_value=1, native_max_value=3, native_step=0.5,
        mode=NumberMode.BOX, icon="mdi:multiplication",
    ), OPT_LOAD_FACTOR),
)

SOC_OFFSET_KEYS = (OPT_SOC_OFFSET_1, OPT_SOC_OFFSET_2, OPT_SOC_OFFSET_3)
RESPONSE_OFFSET_KEYS = (OPT_RESPONSE_OFFSET_1, OPT_RESPONSE_OFFSET_2, OPT_RESPONSE_OFFSET_3)


def copy_numbers(index: int) -> tuple[tuple[NumberEntityDescription, str, str], ...]:
    """The settings of copy `index`, on the copy's own device (v1.7.3).

    The keys, and so the unique ids, stay those of v1.7.2; the name drops
    "clone n", because the device says which copy it is. One translation per
    setting, for every copy. The third field tells how it is applied: Max
    share at once, the offsets at the next Reset.
    """
    return (
        # Max share (v1.7.2): 0 = its own maximum. A low Max share on the
        # primary leaves the rest of the demand to the others, also at a low load.
        (NumberEntityDescription(
            key=OPT_MAX_SHARE_CLONES[index - 1], translation_key="copy_max_share", name="Max share",
            native_unit_of_measurement=UnitOfPower.WATT, native_min_value=0, native_max_value=MAX_SHARE_LIMIT,
            native_step=50, mode=NumberMode.BOX, icon="mdi:arrow-collapse-vertical",
        ), OPT_MAX_SHARE_CLONES[index - 1], "share"),
        (NumberEntityDescription(
            key=SOC_OFFSET_KEYS[index - 1], translation_key="copy_soc_offset", name="SoC offset",
            native_unit_of_measurement=PERCENTAGE, native_min_value=-50, native_max_value=50, native_step=1,
            mode=NumberMode.BOX, icon="mdi:battery-arrow-up-outline",
        ), SOC_OFFSET_KEYS[index - 1], "int"),
        (NumberEntityDescription(
            key=RESPONSE_OFFSET_KEYS[index - 1], translation_key="copy_response_offset", name="Extra response time",
            native_unit_of_measurement=UnitOfTime.SECONDS, native_min_value=0, native_max_value=10,
            native_step=0.5, mode=NumberMode.BOX, icon="mdi:timer-sand",
        ), RESPONSE_OFFSET_KEYS[index - 1], "float"),
    )

# The virtual P1 meter, a device of its own like the real P1 (v1.7.1).
VIRTUAL_P1_NAME = "Virtual P1 meter"
_W = dict(native_unit_of_measurement=UnitOfPower.WATT, device_class=SensorDeviceClass.POWER,
          state_class=SensorStateClass.MEASUREMENT)
VIRTUAL_P1_DESCRIPTIONS = (
    (SensorEntityDescription(key="virtual_p1_power", name="Grid power", icon="mdi:transmission-tower", **_W), "power"),
    (SensorEntityDescription(key="virtual_p1_house_load", name="House load", icon="mdi:home-lightning-bolt", **_W), "house"),
    (SensorEntityDescription(
        key="virtual_p1_real_grid", name="Real grid power", icon="mdi:transmission-tower-import", **_W,
    ), "real_grid"),
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
        info["model"] = f"{clone.model} (simulated)"
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
            cls = FloatNumber if key == OPT_LOAD_FACTOR else ClusterNumber
            entities.append(cls(entry, vc, description, vc.settings, key))
        copies: list[Any] = []
        for clone in vc.clones:
            for description, key, kind in copy_numbers(clone.index):
                if kind == "share":
                    copies.append(MaxShareNumber(entry, vc, description, vc.settings, key, clone.index))
                else:
                    cls = FloatNumber if kind == "float" else ClusterNumber
                    copies.append(cls(entry, vc, description, vc.settings, key))
                _on_copy(copies[-1], clone)
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
    if platform == Platform.NUMBER:
        entities += copies
    return entities


def _on_copy(entity: Any, clone: Clone) -> None:
    """A setting of a copy goes on the copy's device, the one its sensors are on (v1.7.3)."""
    entity._attr_device_info = DeviceInfo(
        identifiers={(DOMAIN, clone.device_id)},
        name=clone.name,
        model=f"{clone.model} (simulated)",
    )


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

    class CloneSocSensor(ZendureSensor):
        """State of charge of a copy, with where it started against the real battery (v1.7.2)."""

        def __init__(self, clone: Any, *args: Any) -> None:
            super().__init__(*args)
            self._clone = clone

        @property
        def extra_state_attributes(self) -> dict[str, Any]:
            return {"start_offset_vs_real": self._clone.start_offset}

    soc = next(d for d in DEVICE_SENSORS if d.key == "electricLevel")
    response = tuple(d for d in MEASURE_SENSORS if d.key == "response_time")
    entities: list[Any] = []
    for clone in vc.clones:
        coordinator, device_id, model = clone.coordinator, clone.device_id, clone.model
        made: list[Any] = [
            CloneSocSensor(clone, coordinator, soc, device_id, model),
            ZendureBatteryPowerSensor(coordinator, BATTERY_POWER_DESCRIPTION, device_id, model),
            ZendureControllerSensor(coordinator, CONTROLLER_DESCRIPTION, device_id, model, clone.controller),
            *(CloneLimitSensor(coordinator, d, device_id, model) for d in LIMIT_DESCRIPTIONS),
            *(ZendureMeasureSensor(coordinator, d, device_id, model, clone.controller) for d in response),
        ]
        for entity in made:
            _clone_device(entity, clone)
        entities += made
    return entities


class MaxShareNumber(ClusterNumber):
    """Max share of one clone; counts at once, without a Reset (v1.7.2)."""

    def __init__(self, entry: ConfigEntry, cluster: VirtualCluster, description: NumberEntityDescription,
                 settings: Any, key: str, index: int) -> None:
        super().__init__(entry, cluster, description, settings, key)
        self._index = index

    async def async_set_native_value(self, value: float) -> None:
        self._settings.set(self._key, int(value))
        self._cluster.apply_max_share(self._index)
        self.async_write_ha_state()


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
            model=f"{VIRTUAL_P1_NAME} (simulated)",
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
        meter = self._cluster.meter
        value = (meter.data or {}).get(self._field) if meter is not None else None
        return None if value is None else round(float(value))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._field != "power":
            return None
        # The Load factor is an attribute here since v1.7.3: the
        # setting is number.…_load_factor, a sensor of the same was a copy.
        return {
            "source": self._cluster.source_name,
            "telegrams": getattr(self._cluster.meter, "telegrams", 0),
            "load_factor": self._cluster.load_factor(),
        }

    def _due(self) -> bool:
        return True


class ResetButton(ClusterEntity, ButtonEntity):
    """Every clone back to the start; the twin on the real state of charge."""

    _attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        await self._cluster.async_reset()


# ── Entity ids of v1.7.1/v1.7.2 ─────────────────────────────────────
# The renamed entities keep their unique id, so Home Assistant would keep the
# old entity id. They are moved once to the id a new installation gets, so the
# dashboard, the docs and every example match; the recorder carries the
# history to the new id. Only an id still in its old default form is moved;
# one renamed by hand, or a target that is taken, stays.


def _renames(entry: ConfigEntry, clones: list[Clone]) -> list[tuple[str, str, str, str]]:
    """(platform, unique id, old object id, new object id) for each moved entity."""
    from homeassistant.util import slugify

    from ..const import CONF_DEVICE_NAME, VIRTUAL_DEVICE_NAME

    eid = entry.entry_id
    group = slugify(entry.data.get(CONF_DEVICE_NAME, VIRTUAL_DEVICE_NAME))
    p1 = slugify(VIRTUAL_P1_NAME)
    moves = [
        ("number", f"{eid}_clones", f"{group}_clones", f"{group}_copies"),
        ("sensor", f"{eid}_virtual_p1_power", f"{p1}_power", f"{p1}_grid_power"),
        ("sensor", f"{eid}_virtual_p1_real_grid", f"{p1}_real_grid", f"{p1}_real_grid_power"),
    ]
    for clone in clones:
        n, copy = clone.index, slugify(clone.name)
        moves += [
            ("number", f"{eid}_{OPT_MAX_SHARE_CLONES[n - 1]}", f"{group}_max_share_clone_{n}", f"{copy}_max_share"),
            ("number", f"{eid}_{SOC_OFFSET_KEYS[n - 1]}", f"{group}_soc_offset_clone_{n}", f"{copy}_soc_offset"),
            ("number", f"{eid}_{RESPONSE_OFFSET_KEYS[n - 1]}", f"{group}_response_offset_clone_{n}",
             f"{copy}_extra_response_time"),
        ]
    return moves


def migrate_entity_ids(hass: HomeAssistant, entry: ConfigEntry, clones: list[Clone]) -> int:
    """Move the entity ids of v1.7.2 to the names of v1.7.3; drop the Load factor sensor."""
    import logging

    from homeassistant.helpers import entity_registry as er

    log = logging.getLogger(__name__)
    registry = er.async_get(hass)
    old_factor = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_virtual_p1_load_factor")
    if old_factor is not None:
        registry.async_remove(old_factor)
        log.info("Removed %s: the Load factor is an attribute of the Virtual P1 Grid power (v1.7.3)", old_factor)
    moved = 0
    for platform, unique_id, old, new in _renames(entry, clones):
        current = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        wanted = f"{platform}.{new}"
        if current is None or current == wanted or current != f"{platform}.{old}":
            continue
        if registry.async_is_registered(wanted) or hass.states.get(wanted) is not None:
            log.warning("Not renaming %s: %s is already in use", current, wanted)
            continue
        registry.async_update_entity(current, new_entity_id=wanted)
        moved += 1
    moved += _move_copy_prefix(hass, entry, clones, registry, log)
    if moved:
        log.info(
            "Virtual cluster: moved %d entity ids to the names of v1.7.3; history is kept, "
            "dashboards that name the old ids need a Rebuild dashboard", moved,
        )
    return moved


def _move_copy_prefix(hass: HomeAssistant, entry: ConfigEntry, clones: list[Clone], registry: Any, log: Any) -> int:
    """virtual_mix_n_… / virtual_battery_n_… → virtual_copy_n_… for every entity of a copy (v1.7.3).

    The entities of copy n are those whose unique id starts with its serial,
    and its three settings. Only an id that still starts with the old device
    name is moved; one renamed by hand, or a target that is taken, stays.
    """
    from homeassistant.util import slugify

    from .cluster import OLD_COPY_PREFIXES

    moved = 0
    tail = entry.entry_id[-6:].upper()
    for clone in clones:
        n = clone.index
        new = f"{slugify(clone.name)}_"
        old = tuple(f"{p}{n}_" for p in OLD_COPY_PREFIXES)
        serial = f"SIMULATED{n}{tail}_"
        settings = set(copy_setting_unique_ids(entry, n))
        for item in list(registry.entities.values()):
            unique_id = str(item.unique_id)
            if item.platform != DOMAIN or not (unique_id.startswith(serial) or unique_id in settings):
                continue
            domain, object_id = item.entity_id.split(".", 1)
            prefix = next((p for p in old if object_id.startswith(p)), None)
            if prefix is None or prefix == new:
                continue
            wanted = f"{domain}.{new}{object_id[len(prefix):]}"
            if registry.async_is_registered(wanted) or hass.states.get(wanted) is not None:
                log.warning("Not renaming %s: %s is already in use", item.entity_id, wanted)
                continue
            registry.async_update_entity(item.entity_id, new_entity_id=wanted)
            moved += 1
    return moved


def copy_setting_unique_ids(entry: ConfigEntry, index: int) -> tuple[str, ...]:
    """Unique ids of the settings of copy `index`, to remove with the copy (v1.7.3)."""
    return tuple(f"{entry.entry_id}_{key}" for _d, key, _k in copy_numbers(index))
