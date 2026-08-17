"""Switch platform for the Zendure RestAPI integration."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import ZendureApiError
from .const import CONF_MODEL, DOMAIN, OPT_METER_INVERT, OPT_SOC_PROTECTION
from .coordinator import ZendureCoordinator
from .entity import ZendureEntity
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)

SWITCHES: tuple[SwitchEntityDescription, ...] = (
    SwitchEntityDescription(
        key="smartMode",
        name="Skip flash write",
        icon="mdi:flash-off",
        entity_category=EntityCategory.CONFIG,
    ),
    SwitchEntityDescription(
        key="Fanmode",
        name="Fan forced on",
        icon="mdi:fan",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create switches for every reported writable boolean key."""
    coordinator: ZendureCoordinator = hass.data[DOMAIN][entry.entry_id]
    device_id = coordinator.device_id or entry.unique_id or entry.entry_id
    model = entry.data.get(CONF_MODEL) or "SolarFlow"
    data = coordinator.data or {}

    coordinator.register_known_keys({d.key for d in SWITCHES})

    entities: list[SwitchEntity] = [
        ZendureSwitch(coordinator, description, device_id, model)
        for description in SWITCHES
        if description.key in data
    ]

    runtime = hass.data[DOMAIN].get(f"{entry.entry_id}_runtime")
    if runtime is not None:
        entities += [
            ZendureSettingSwitch(coordinator, d, device_id, model, runtime["settings"], key)
            for d, key in SETTING_SWITCHES
        ]

    async_add_entities(entities)


class ZendureSwitch(ZendureEntity, SwitchEntity):
    """Writable 0/1 property."""

    entity_description: SwitchEntityDescription

    @property
    def is_on(self) -> bool | None:
        value = self.raw_value
        if value is None:
            return None
        try:
            return int(value) != 0
        except (TypeError, ValueError):
            return None

    async def async_turn_on(self, **kwargs) -> None:
        await self._write(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._write(0)

    async def _write(self, value: int) -> None:
        try:
            await self.coordinator.async_write(self.entity_description.key, value)
        except ZendureApiError as err:
            _LOGGER.error(
                "Failed to write %s=%s: %s", self.entity_description.key, value, err
            )
            raise


# ── Controller settings ──────────────────────────────────────────────────

SETTING_SWITCHES: tuple[tuple[SwitchEntityDescription, str], ...] = (
    (SwitchEntityDescription(
        key="soc_protection",
        name="SOC protection",
        icon="mdi:shield-battery-outline",
        entity_category=EntityCategory.CONFIG,
    ), OPT_SOC_PROTECTION),
    (SwitchEntityDescription(
        key="meter_invert",
        name="Invert meter sign",
        icon="mdi:plus-minus-variant",
        entity_category=EntityCategory.CONFIG,
    ), OPT_METER_INVERT),
)


class ZendureSettingSwitch(ZendureEntity, SwitchEntity):
    """A controller setting, stored in the config entry rather than on the device."""

    entity_description: SwitchEntityDescription

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: SwitchEntityDescription,
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
    def is_on(self) -> bool | None:
        return self._settings.get_bool(self._option_key)

    async def async_turn_on(self, **kwargs) -> None:
        self._settings.set(self._option_key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._settings.set(self._option_key, False)
        self.async_write_ha_state()
