"""Entities of a Price Control entry: one device, linked to its battery.

The device is a service of this integration, not hardware, and carries no
manufacturer: price control is this integration's own steering. Its main
switch takes the device name, so the entity ids read switch.price_control,
sensor.price_control_status, number.price_control_cheap_price and so on.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import (
    CONF_DEVICE_NAME,
    DOMAIN,
    OPT_PRICE_BETWEEN_MODE,
    OPT_PRICE_CHEAP,
    OPT_PRICE_CHEAP_MODE,
    OPT_PRICE_CONTROL,
    OPT_PRICE_EXPENSIVE,
    OPT_PRICE_EXPENSIVE_MODE,
    PRICE_DEVICE_NAME,
    PRICE_MODE_OPTIONS,
    PRICE_MODEL,
    PRICE_STATUSES,
)
from .entity import TranslatedName
from .pricecontrol import PriceControl
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)

PRICE_SWITCHES: tuple[tuple[SwitchEntityDescription, str], ...] = (
    # No name: the main entity of the device takes the device name.
    (SwitchEntityDescription(key="price_control", name=None, icon="mdi:cash-clock"), OPT_PRICE_CONTROL),
)

PRICE_SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="price_control_status",
        translation_key="price_control_status",
        name="Status",
        device_class=SensorDeviceClass.ENUM,
        options=list(PRICE_STATUSES),
        icon="mdi:cash-sync",
    ),
    SensorEntityDescription(
        key="electricity_price",
        name="Electricity price",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cash",
    ),
)

PRICE_NUMBERS: tuple[tuple[NumberEntityDescription, str], ...] = (
    (NumberEntityDescription(
        key="cheap_price", name="Cheap price", native_min_value=-1, native_max_value=2,
        native_step=0.005, mode=NumberMode.BOX, icon="mdi:cash-minus",
    ), OPT_PRICE_CHEAP),
    (NumberEntityDescription(
        key="expensive_price", name="Expensive price", native_min_value=-1, native_max_value=2,
        native_step=0.005, mode=NumberMode.BOX, icon="mdi:cash-plus",
    ), OPT_PRICE_EXPENSIVE),
)

# The options are Operation mode names and stay untranslated, like the
# Operation mode itself.
PRICE_SELECTS: tuple[tuple[SelectEntityDescription, str], ...] = (
    (SelectEntityDescription(
        key="cheap_price_mode", name="Cheap price mode",
        options=list(PRICE_MODE_OPTIONS[OPT_PRICE_CHEAP_MODE]), icon="mdi:battery-arrow-up",
    ), OPT_PRICE_CHEAP_MODE),
    (SelectEntityDescription(
        key="expensive_price_mode", name="Expensive price mode",
        options=list(PRICE_MODE_OPTIONS[OPT_PRICE_EXPENSIVE_MODE]), icon="mdi:battery-arrow-down",
    ), OPT_PRICE_EXPENSIVE_MODE),
    (SelectEntityDescription(
        key="in_between_mode", name="In-between mode",
        options=list(PRICE_MODE_OPTIONS[OPT_PRICE_BETWEEN_MODE]), icon="mdi:scale-balance",
    ), OPT_PRICE_BETWEEN_MODE),
)


def price_entities(platform: Platform, hass: HomeAssistant, entry: ConfigEntry) -> list[Any]:
    """The entities of one platform for a Price Control entry."""
    price: PriceControl = hass.data[DOMAIN][entry.entry_id]
    settings = ZendureSettings(hass, entry)
    if platform == Platform.SWITCH:
        return [PriceSwitch(entry, price, d, settings, key) for d, key in PRICE_SWITCHES]
    if platform == Platform.SENSOR:
        return [
            PriceStatusSensor(entry, price, PRICE_SENSORS[0]),
            PriceValueSensor(entry, price, PRICE_SENSORS[1]),
        ]
    if platform == Platform.NUMBER:
        return [PriceNumber(entry, price, d, settings, key) for d, key in PRICE_NUMBERS]
    if platform == Platform.SELECT:
        return [PriceModeSelect(entry, price, d, settings, key) for d, key in PRICE_SELECTS]
    return []


class PriceEntity(TranslatedName):
    """Shared: the device, and an update after every price decision."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, price: PriceControl, description: Any) -> None:
        self.entity_description = description
        self._price = price
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._set_name(description)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"price_{entry.entry_id}")},
            name=entry.data.get(CONF_DEVICE_NAME, PRICE_DEVICE_NAME),
            model=PRICE_MODEL,
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._price.signal, self.async_write_ha_state)
        )


class _Setting(PriceEntity):
    def __init__(self, entry, price, description, settings: ZendureSettings, key: str) -> None:
        super().__init__(entry, price, description)
        self._settings = settings
        self._key = key


class PriceSwitch(_Setting, SwitchEntity):
    """Price control on or off."""

    @property
    def is_on(self) -> bool:
        return self._settings.get_bool(self._key)

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._settings.set(self._key, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._settings.set(self._key, False)
        self.async_write_ha_state()


class PriceNumber(_Setting, NumberEntity):
    """A price limit, in the currency Home Assistant is set to."""

    @property
    def native_unit_of_measurement(self) -> str | None:
        currency = getattr(getattr(self.hass, "config", None), "currency", None)
        return f"{currency}/kWh" if currency else None

    @property
    def native_value(self) -> float:
        return self._settings.get_float(self._key)

    async def async_set_native_value(self, value: float) -> None:
        self._settings.set(self._key, round(float(value), 4))
        self.async_write_ha_state()


class PriceModeSelect(_Setting, SelectEntity):
    """The Operation mode price control uses in one price band."""

    @property
    def current_option(self) -> str | None:
        return self._settings.get(self._key)

    async def async_select_option(self, option: str) -> None:
        if option not in PRICE_MODE_OPTIONS[self._key]:
            _LOGGER.error("Option %s not allowed for %s", option, self._key)
            return
        self._settings.set(self._key, option)
        self.async_write_ha_state()


class PriceStatusSensor(PriceEntity, SensorEntity):
    """Which price band applies, with the target mode as attribute."""

    @property
    def native_value(self) -> str:
        return self._price.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._price.attributes


class PriceValueSensor(PriceEntity, SensorEntity):
    """The price as price control reads it."""

    @property
    def available(self) -> bool:
        return self._price.price is not None

    @property
    def native_value(self) -> float | None:
        return self._price.price

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._price.unit
