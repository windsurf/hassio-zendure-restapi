"""Binary sensor platform for the Zendure RestAPI integration."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_MODEL, DOMAIN, PACK_PREFIX
from .coordinator import ZendureCoordinator
from .entity import ZendureEntity

DEVICE_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="gridState",
        name="Grid connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
    ),
    BinarySensorEntityDescription(
        key="is_error",
        name="Error",
        device_class=BinarySensorDeviceClass.PROBLEM,
    ),
    BinarySensorEntityDescription(
        key="dataReady",
        name="Data ready",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="pass",
        name="Pass-through",
        icon="mdi:transmission-tower-import",
    ),
    BinarySensorEntityDescription(
        key="reverseState",
        name="Reverse flow",
        icon="mdi:transmission-tower-export",
    ),
    BinarySensorEntityDescription(
        key="heatState",
        name="Heating",
        device_class=BinarySensorDeviceClass.HEAT,
    ),
    BinarySensorEntityDescription(
        key="fanSwitch",
        name="Fan",
        icon="mdi:fan",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="lampSwitch",
        name="Lamp",
        icon="mdi:lightbulb",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    BinarySensorEntityDescription(
        key="pvacSwitch",
        name="PV-AC coupling",
        icon="mdi:solar-power-variant",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key="socCompSwitch",
        name="SOC compensation",
        icon="mdi:battery-sync-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key="hvBatCtrlSwitch",
        name="HV battery control",
        icon="mdi:car-battery",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    BinarySensorEntityDescription(
        key="dryNodeState",
        name="Dry contact",
        icon="mdi:electric-switch",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

PACK_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="heatState",
        name="Heating",
        device_class=BinarySensorDeviceClass.HEAT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create binary sensors for every reported key."""
    coordinator: ZendureCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = coordinator.device_id or entry.unique_id or entry.entry_id
    model = entry.data.get(CONF_MODEL) or "SolarFlow"
    data = coordinator.data or {}

    coordinator.register_known_keys({d.key for d in DEVICE_BINARY_SENSORS})

    entities: list[BinarySensorEntity] = [
        ZendureBinarySensor(coordinator, description, device_id, model)
        for description in DEVICE_BINARY_SENSORS
        if description.key in data
    ]

    for index in range(1, coordinator.pack_count + 1):
        for description in PACK_BINARY_SENSORS:
            data_key = f"{PACK_PREFIX}{index}.{description.key}"
            if data_key not in data:
                continue
            entity = ZendureBinarySensor(coordinator, description, device_id, model, data_key)
            entity.override_identity(index)
            entities.append(entity)

    async_add_entities(entities)


class ZendureBinarySensor(ZendureEntity, BinarySensorEntity):
    """Boolean state derived from a 0/1 property."""

    entity_description: BinarySensorEntityDescription

    def override_identity(self, index: int) -> None:
        """Rename and re-key this entity as belonging to a specific pack."""
        self._attr_unique_id = f"{self._device_id}_{PACK_PREFIX}{index}_{self.entity_description.key}"
        self._attr_name = f"Battery {index} {self.entity_description.name}"

    @property
    def is_on(self) -> bool | None:
        value = self.raw_value
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        try:
            return int(value) != 0
        except (TypeError, ValueError):
            return None
