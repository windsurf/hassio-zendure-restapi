"""Entities of the battery cluster entry: one device, the group (v1.7.0).

Like Price Control the device is a service of this integration, not
hardware. Its settings are the ones that steer a single battery — Operation
mode, buffers, start thresholds, meter — now for the group, plus the two of
the cluster itself. Entity ids read select.battery_cluster_operation_mode,
sensor.battery_cluster_status and so on.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, Platform, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_DEVICE_NAME,
    CLUSTER_DEVICE_NAME,
    CLUSTER_MODEL,
    CLUSTER_MODES,
    CLUSTER_STATUSES,
    DOMAIN,
    METER_SOURCES,
    OPT_CHARGE_BUFFER,
    OPT_CONCENTRATE_BELOW,
    OPT_DISCHARGE_BUFFER,
    OPT_METER_SOURCE,
    OPT_OPERATION_MODE,
    OPT_REDISTRIBUTE,
    OPT_START_CHARGE_AT,
    OPT_START_DISCHARGE_AT,
)
from .energy import WriteGate
from .entity import TranslatedName
from .cluster.runtime import ClusterCoordinator
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)

# At most this long between two writes of a cluster figure that keeps moving.
WRITE_INTERVAL = 60.0


def _watts(key: str, name: str, low: int, high: int, step: int, icon: str, category: bool = True) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=key, name=name, native_unit_of_measurement=UnitOfPower.WATT,
        device_class=NumberDeviceClass.POWER, native_min_value=low, native_max_value=high,
        native_step=step, mode=NumberMode.BOX, icon=icon,
        entity_category=EntityCategory.CONFIG if category else None,
    )


CLUSTER_NUMBERS: tuple[tuple[NumberEntityDescription, str], ...] = (
    (_watts("charge_buffer", "Charge buffer", 0, 200, 5, "mdi:target"), OPT_CHARGE_BUFFER),
    (_watts("discharge_buffer", "Discharge buffer", 0, 200, 5, "mdi:target"), OPT_DISCHARGE_BUFFER),
    (_watts("start_discharge_at", "Start discharging at import", 0, 200, 5, "mdi:transmission-tower-import"),
     OPT_START_DISCHARGE_AT),
    (_watts("start_charge_at", "Start charging at export", 0, 200, 5, "mdi:transmission-tower-export"),
     OPT_START_CHARGE_AT),
    (_watts("concentrate_below", "Concentrate below", 0, 1000, 50, "mdi:arrow-collapse-horizontal", category=False),
     OPT_CONCENTRATE_BELOW),
    (NumberEntityDescription(
        key="redistribute_every", name="Redistribute every", native_unit_of_measurement=UnitOfTime.SECONDS,
        native_min_value=10, native_max_value=120, native_step=5, mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG, icon="mdi:timer-sync-outline",
    ), OPT_REDISTRIBUTE),
)

CLUSTER_SELECTS: tuple[tuple[SelectEntityDescription, str], ...] = (
    # The mode names stay untranslated, like the Operation mode of a battery.
    (SelectEntityDescription(
        key="operation_mode", name="Operation mode", options=list(CLUSTER_MODES), icon="mdi:state-machine",
    ), OPT_OPERATION_MODE),
    (SelectEntityDescription(
        key="meter_source", translation_key="meter_source", name="Meter source",
        options=list(METER_SOURCES), icon="mdi:meter-electric-outline", entity_category=EntityCategory.CONFIG,
    ), OPT_METER_SOURCE),
)

STATUS_DESCRIPTION = SensorEntityDescription(
    key="cluster_status", translation_key="cluster_status", name="Status",
    device_class=SensorDeviceClass.ENUM, options=list(CLUSTER_STATUSES), icon="mdi:battery-sync",
)
POWER_DESCRIPTION = SensorEntityDescription(
    key="group_power", name="Group power", native_unit_of_measurement=UnitOfPower.WATT,
    device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, icon="mdi:home-battery",
)
SOC_DESCRIPTION = SensorEntityDescription(
    key="group_state_of_charge", name="Group state of charge", native_unit_of_measurement=PERCENTAGE,
    device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT,
)
MEMBERS_DESCRIPTION = SensorEntityDescription(
    key="members", name="Batteries taking part", state_class=SensorStateClass.MEASUREMENT,
    icon="mdi:battery-sync-outline",
)


def cluster_entities(platform: Platform, hass: HomeAssistant, entry: ConfigEntry) -> list[Any]:
    """The entities of one platform for the cluster entry."""
    cluster: ClusterCoordinator = hass.data[DOMAIN][entry.entry_id]
    settings = ZendureSettings(hass, entry)
    if platform == Platform.SELECT:
        return [ClusterSelect(entry, cluster, d, settings, key) for d, key in CLUSTER_SELECTS]
    if platform == Platform.NUMBER:
        return [ClusterNumber(entry, cluster, d, settings, key) for d, key in CLUSTER_NUMBERS]
    if platform == Platform.SENSOR:
        return [
            ClusterStatus(entry, cluster, STATUS_DESCRIPTION),
            ClusterValue(entry, cluster, POWER_DESCRIPTION, lambda f: f.group_power, step=50),
            ClusterValue(entry, cluster, SOC_DESCRIPTION, lambda f: f.group_soc, {"spread": lambda f: f.soc_spread}, step=0.5),
            ClusterValue(
                entry, cluster, MEMBERS_DESCRIPTION,
                lambda f: sum(1 for r in f.readings if r.available),
                {
                    "configured": lambda f: len(f.members),
                    "batteries": lambda f: [
                        {"name": r.name, "phase": f"L{r.phase}", "available": r.available}
                        for r in f.readings
                    ],
                },
            ),
        ]
    return []


class ClusterEntity(TranslatedName):
    """Shared: the device, and an update after every decision."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, cluster: ClusterCoordinator, description: Any) -> None:
        self.entity_description = description
        self._cluster = cluster
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._set_name(description)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"cluster_{entry.entry_id}")},
            name=entry.data.get(CONF_DEVICE_NAME, CLUSTER_DEVICE_NAME),
            model=CLUSTER_MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._cluster.signal, self._on_decision)
        )

    @callback
    def _on_decision(self) -> None:
        if self._due():
            self.async_write_ha_state()

    def _due(self) -> bool:
        """Whether this decision is worth a state write. Settings: always (no change, no row)."""
        return True


class _Setting(ClusterEntity):
    def __init__(self, entry, cluster, description, settings: ZendureSettings, key: str) -> None:
        super().__init__(entry, cluster, description)
        self._settings = settings
        self._key = key


class ClusterSelect(_Setting, SelectEntity):
    """The group's Operation mode, or the meter it steers on."""

    @property
    def current_option(self) -> str | None:
        return self._settings.get(self._key)

    async def async_select_option(self, option: str) -> None:
        if option not in (self.entity_description.options or ()):
            _LOGGER.error("Option %s not allowed for %s", option, self._key)
            return
        self._settings.set(self._key, option)
        self.async_write_ha_state()


class ClusterNumber(_Setting, NumberEntity):
    """A setting of the group."""

    @property
    def native_value(self) -> float:
        return float(self._settings.get_int(self._key))

    async def async_set_native_value(self, value: float) -> None:
        self._settings.set(self._key, int(value))
        self.async_write_ha_state()


class ClusterStatus(ClusterEntity, SensorEntity):
    """What the group does, with the role and limit per battery as attributes.

    The decision is taken every 2 s and its demand and grid figures move each
    time; written as they come that is a database row every 2 s. The state is
    written when the status, the primary or a battery's role or limit changes,
    and otherwise once a minute at most (v1.7.0, in line with v1.6.7).
    """

    _signature: Any = None
    _written_at = 0.0

    def _due(self) -> bool:
        attributes = self._cluster.attributes
        signature = (self._cluster.status, attributes.get("primary"), repr(attributes.get("batteries")))
        now = time.monotonic()
        if signature != self._signature or now - self._written_at >= WRITE_INTERVAL:
            self._signature, self._written_at = signature, now
            return True
        return False

    @property
    def native_value(self) -> str:
        return self._cluster.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._cluster.attributes


class ClusterValue(ClusterEntity, SensorEntity):
    """One number about the group."""

    def __init__(self, entry, cluster, description, value, attributes: dict[str, Any] | None = None, step: float = 1) -> None:
        super().__init__(entry, cluster, description)
        self._value = value
        self._extra = attributes or {}
        # Written per step or once a minute when it moved, like the energy counters (v1.6.7).
        self._gate = WriteGate(step, WRITE_INTERVAL)

    def _due(self) -> bool:
        value = self._value(self._cluster)
        available = value is not None
        now = time.monotonic()
        if self._gate.due(value, available, now):
            self._gate.mark(value, available, now)
            return True
        return False

    @property
    def available(self) -> bool:
        return self._value(self._cluster) is not None

    @property
    def native_value(self) -> Any:
        return self._value(self._cluster)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._extra:
            return None
        return {key: fn(self._cluster) for key, fn in self._extra.items()}
