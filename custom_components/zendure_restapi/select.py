"""Select platform for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ZendureApiError
from .const import (
    CONF_MODEL,
    DOMAIN,
    EXTERNAL_NONE,
    METER_SOURCES,
    OPT_EXTERNAL_METER,
    SIGNAL_EXTERNALS_CHANGED,
    OPERATION_MODES,
    OPT_METER_SOURCE,
    OPT_OPERATION_MODE,
    MODE_COORDINATED,
)
from homeassistant.exceptions import HomeAssistantError
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

from homeassistant.const import Platform
from .const import CONF_DEVICE_TYPE, DEVICE_TYPE_CLUSTER, DEVICE_TYPE_PRICE
from .cluster_entities import cluster_entities
from .price_entities import price_entities
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class ZendureSelectDescription(SelectEntityDescription):
    """Select description carrying the numeric value map."""

    value_map: dict[int, str] = field(default_factory=dict)


SELECTS: tuple[ZendureSelectDescription, ...] = (
    ZendureSelectDescription(
        key="acMode",
        translation_key="converter_mode",
        name="Converter mode",
        options=list(AC_MODE_MAP.values()),
        value_map=AC_MODE_MAP,
        icon="mdi:swap-vertical-bold",
    ),
    ZendureSelectDescription(
        key="gridOffMode",
        translation_key="backup_mode",
        name="Backup mode",
        options=list(GRID_OFF_MODE_MAP.values()),
        value_map=GRID_OFF_MODE_MAP,
        icon="mdi:power-plug-off",
        entity_category=EntityCategory.CONFIG,
    ),
    ZendureSelectDescription(
        key="gridReverse",
        translation_key="pv_export",
        name="PV export",
        options=list(GRID_REVERSE_MAP.values()),
        value_map=GRID_REVERSE_MAP,
        icon="mdi:transmission-tower-export",
        entity_category=EntityCategory.CONFIG,
    ),
    ZendureSelectDescription(
        key="Fanspeed",
        translation_key="fan_speed_mode",
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
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_PRICE:
        async_add_entities(price_entities(Platform.SELECT, hass, entry))
        return
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_CLUSTER:
        async_add_entities(cluster_entities(Platform.SELECT, hass, entry))
        return
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
        operation_mode = ZendureOperationMode(
            coordinator, OPERATION_MODE_DESCRIPTION, device_id, model,
            runtime["settings"],
        )
        operation_mode.view = runtime.get("view")
        entities.append(operation_mode)
        entities.append(
            ZendureMeterSource(
                coordinator, METER_SOURCE_DESCRIPTION, device_id, model,
                runtime["settings"],
            )
        )
        entities.append(
            ZendureExternalMeterSelect(
                coordinator, EXTERNAL_METER_DESCRIPTION, device_id, model,
                runtime["settings"], runtime["controller"],
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
    # The battery's settings view (v1.7.0): set on the Operation mode only.
    view = None

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

    def _coordinated(self) -> bool:
        return self.view is not None and self.view.coordinated

    @property
    def available(self) -> bool:
        # Not backed by a coordinator key, so the base check does not apply.
        return self.coordinator.last_update_success

    @property
    def options(self) -> list[str]:
        # Under the battery cluster the mode is set there (v1.7.0).
        if self._coordinated():
            return [MODE_COORDINATED]
        return list(self.entity_description.options or ())

    @property
    def current_option(self) -> str | None:
        if self._coordinated():
            return MODE_COORDINATED
        return self._settings.get(OPT_OPERATION_MODE)

    async def async_select_option(self, option: str) -> None:
        if self._coordinated():
            raise HomeAssistantError(
                f"{self.view.group_name} steers this battery; set the Operation mode there"
            )
        if option not in OPERATION_MODES:
            _LOGGER.error("Unknown operation mode %s", option)
            return
        if option == self._settings.get(OPT_OPERATION_MODE):
            # The same mode again: send its command again (v1.6.8). After a
            # device restart this is how standby stops a limit from flash.
            entry = getattr(self.coordinator, "config_entry", None)
            entry_id = self.coordinator.entry_id or (entry.entry_id if entry else None)
            runtime = self.hass.data.get(DOMAIN, {}).get(f"{entry_id}_runtime")
            if runtime is not None:
                runtime["controller"].reapply_mode()
                await self.coordinator.async_request_refresh()
            return
        self._settings.set(OPT_OPERATION_MODE, option)
        self.async_write_ha_state()


METER_SOURCE_DESCRIPTION = SelectEntityDescription(
    key="meter_source",
    translation_key="meter_source",
    name="Meter source",
    options=list(METER_SOURCES),
    icon="mdi:meter-electric-outline",
    entity_category=EntityCategory.CONFIG,
)


class ZendureMeterSource(ZendureOperationMode):
    """Which meter feeds both control loops.

    `zendure` is the Zendure P1 meter, `external` the reader configured under
    Configure, `auto` the external one with the Zendure meter as fallback. The
    controller status carries `meter_active`, which says which one is in use
    at this moment — under `auto` that is the part worth watching.
    """

    @property
    def current_option(self) -> str | None:
        return self._settings.get(OPT_METER_SOURCE)

    async def async_select_option(self, option: str) -> None:
        if option not in METER_SOURCES:
            _LOGGER.error("Unknown meter source %s", option)
            return
        self._settings.set(OPT_METER_SOURCE, option)
        self.async_write_ha_state()


EXTERNAL_METER_DESCRIPTION = SelectEntityDescription(
    key="external_meter",
    translation_key="external_meter",
    name="External meter",
    options=[EXTERNAL_NONE],
    icon="mdi:meter-electric",
    entity_category=EntityCategory.CONFIG,
)


class ZendureExternalMeterSelect(ZendureOperationMode):
    """Which external meter this battery uses, when there are several.

    The options are the external meter entries that are set up, named by type
    and address, plus `none`. The current option is the meter the controller
    is actually linked to, so a choice that cannot be honoured — the meter was
    removed, or several exist and none was chosen — shows as `none` rather
    than as a wish. With exactly one external meter and no choice made, that
    one is used, as in v1.5.2.
    """

    def __init__(self, coordinator, description, device_id, model, settings, controller) -> None:
        super().__init__(coordinator, description, device_id, model, settings)
        self._controller = controller

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()

        @callback
        def _changed() -> None:
            self.async_write_ha_state()

        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_EXTERNALS_CHANGED, _changed)
        )

    def _externals(self) -> list:
        from . import external_meters  # the package imports this module
        return external_meters(self.hass)

    @property
    def options(self) -> list[str]:
        return [m.label for m in self._externals()] + [EXTERNAL_NONE]

    @property
    def current_option(self) -> str | None:
        meter = getattr(self._controller, "external", None)
        return meter.label if meter is not None else EXTERNAL_NONE

    async def async_select_option(self, option: str) -> None:
        if option == EXTERNAL_NONE:
            choice = EXTERNAL_NONE
        else:
            match = [m for m in self._externals() if m.label == option]
            if not match:
                _LOGGER.error("Unknown external meter %s", option)
                return
            choice = match[0].entry_id
        self._settings.set(OPT_EXTERNAL_METER, choice)
        from . import _link_meters
        _link_meters(self.hass)
        self.async_write_ha_state()
