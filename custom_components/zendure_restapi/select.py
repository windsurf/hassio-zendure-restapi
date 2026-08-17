"""Select platform for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ZendureApiError
from .const import (
    CONF_MODEL,
    DOMAIN,
    OPERATION_MODES,
    OPT_OPERATION_MODE,
)
from .coordinator import ZendureCoordinator
from .entity import ZendureEntity
from .settings import ZendureSettings
from .properties import (
    AC_MODE_MAP,
    FAN_SPEED_MAP,
    GRID_OFF_MODE_MAP,
    GRID_REVERSE_MAP,
    GRID_STANDARD_MAP,
    map_enum,
    unmap_enum,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ZendureSelectDescription(SelectEntityDescription):
    """Select description carrying the numeric value map."""

    value_map: dict[int, str] = field(default_factory=dict)


SELECTS: tuple[ZendureSelectDescription, ...] = (
    ZendureSelectDescription(
        key="acMode",
        name="Converter mode",
        options=list(AC_MODE_MAP.values()),
        value_map=AC_MODE_MAP,
        icon="mdi:swap-vertical-bold",
    ),
    ZendureSelectDescription(
        key="gridOffMode",
        name="Backup mode",
        options=list(GRID_OFF_MODE_MAP.values()),
        value_map=GRID_OFF_MODE_MAP,
        icon="mdi:power-plug-off",
        entity_category=EntityCategory.CONFIG,
    ),
    ZendureSelectDescription(
        key="gridReverse",
        name="PV export",
        options=list(GRID_REVERSE_MAP.values()),
        value_map=GRID_REVERSE_MAP,
        icon="mdi:transmission-tower-export",
        entity_category=EntityCategory.CONFIG,
    ),
    ZendureSelectDescription(
        key="Fanspeed",
        name="Fan speed mode",
        options=list(FAN_SPEED_MAP.values()),
        value_map=FAN_SPEED_MAP,
        icon="mdi:fan-speed-1",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
    ZendureSelectDescription(
        key="gridStandard",
        name="Grid standard",
        options=list(GRID_STANDARD_MAP.values()),
        value_map=GRID_STANDARD_MAP,
        icon="mdi:earth",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create select entities for every reported enumerated key."""
    coordinator: ZendureCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = coordinator.device_id or entry.unique_id or entry.entry_id
    model = entry.data.get(CONF_MODEL) or "SolarFlow"
    data = coordinator.data or {}

    coordinator.register_known_keys({d.key for d in SELECTS})

    entities: list[SelectEntity] = [
        ZendureSelect(coordinator, description, device_id, model)
        for description in SELECTS
        if description.key in data
    ]

    # The strategy layer only exists on a battery, never on a meter.
    runtime = hass.data[DOMAIN].get(f"{entry.entry_id}_runtime")
    if runtime is not None:
        entities.append(
            ZendureOperationMode(
                coordinator, OPERATION_MODE_DESCRIPTION, device_id, model,
                runtime["settings"],
            )
        )

    async_add_entities(entities)


class ZendureSelect(ZendureEntity, SelectEntity):
    """Writable enumerated property."""

    entity_description: ZendureSelectDescription

    @property
    def current_option(self) -> str | None:
        return map_enum(self.entity_description.value_map, self.raw_value)

    async def async_select_option(self, option: str) -> None:
        numeric = unmap_enum(self.entity_description.value_map, option)
        if numeric is None:
            _LOGGER.error("Unknown option %s for %s", option, self.entity_description.key)
            return
        try:
            await self.coordinator.async_write(self.entity_description.key, numeric)
        except ZendureApiError as err:
            _LOGGER.error(
                "Failed to write %s=%s: %s", self.entity_description.key, numeric, err
            )
            raise


OPERATION_MODE_DESCRIPTION = SelectEntityDescription(
    key="operation_mode",
    name="Operation mode",
    options=list(OPERATION_MODES),
    icon="mdi:state-machine",
)


class ZendureOperationMode(ZendureEntity, SelectEntity):
    """The strategy the controller should follow.

    Unlike every other entity here this one does not mirror a device property.
    It is integration state, persisted in the config entry options so a restart
    resumes the same strategy.
    """

    entity_description: SelectEntityDescription

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: SelectEntityDescription,
        device_id: str,
        model: str,
        settings: ZendureSettings,
    ) -> None:
        super().__init__(coordinator, description, device_id, model)
        self._settings = settings

    @property
    def available(self) -> bool:
        # Not backed by a coordinator key, so the base check does not apply.
        return self.coordinator.last_update_success

    @property
    def current_option(self) -> str | None:
        return self._settings.get(OPT_OPERATION_MODE)

    async def async_select_option(self, option: str) -> None:
        if option not in OPERATION_MODES:
            _LOGGER.error("Unknown operation mode %s", option)
            return
        self._settings.set(OPT_OPERATION_MODE, option)
        self.async_write_ha_state()
