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

from homeassistant.const import PERCENTAGE, UnitOfPower

from .const import (
    CONF_DEVICE_NAME,
    DOMAIN,
    OPT_CHARGE_EFFICIENCY,
    OPT_DISCHARGE_EFFICIENCY,
    OPT_EXPORT_FACTOR,
    OPT_EXPORT_SURCHARGE,
    OPT_HOUSE_LOAD,
    OPT_IMPORT_FACTOR,
    OPT_IMPORT_SURCHARGE,
    OPT_MIN_MARGIN,
    OPT_NET_METERING,
    OPT_PRICE_BETWEEN_MODE,
    OPT_PRICE_CHEAP,
    OPT_PRICE_CHEAP_MODE,
    OPT_PRICE_CONTROL,
    OPT_PRICE_DECISION,
    OPT_PRICE_EXPENSIVE,
    OPT_PRICE_EXPENSIVE_MODE,
    OPT_WEAR_COST,
    PLAN_ACTIONS,
    PRICE_DECISIONS,
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
    # Net metering: a kWh sent to the grid is worth the import price. Dutch
    # rule (salderen) until the end of 2026; elsewhere in Europe exports earn
    # their own price (net billing), so off there and in the Netherlands from
    # 2027. The entity id stays switch.<device>_net_metering (OBJECT_ID_NAMES).
    (SwitchEntityDescription(
        key="net_metering", name="Net metering (export = import price)", icon="mdi:swap-horizontal",
    ), OPT_NET_METERING),
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
    SensorEntityDescription(
        key="import_price",
        name="Import price",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-import",
    ),
    SensorEntityDescription(
        key="export_price",
        name="Export price",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:transmission-tower-export",
    ),
    SensorEntityDescription(
        key="plan",
        translation_key="plan",
        name="Plan",
        device_class=SensorDeviceClass.ENUM,
        options=list(PLAN_ACTIONS),
        icon="mdi:calendar-clock",
    ),
    SensorEntityDescription(
        key="expected_margin",
        name="Expected margin",
        device_class=SensorDeviceClass.MONETARY,
        suggested_display_precision=2,
        icon="mdi:cash-check",
    ),
)

PRICE_NUMBERS: tuple[tuple[NumberEntityDescription, str], ...] = (
    (NumberEntityDescription(
        key="cheap_price", name="Cheap price", native_min_value=-0.5, native_max_value=1,
        native_step=0.005, mode=NumberMode.BOX, icon="mdi:cash-minus",
    ), OPT_PRICE_CHEAP),
    (NumberEntityDescription(
        key="expensive_price", name="Expensive price", native_min_value=-0.5, native_max_value=1,
        native_step=0.005, mode=NumberMode.BOX, icon="mdi:cash-plus",
    ), OPT_PRICE_EXPENSIVE),
    # The day plan: tariff, losses, wear and the margin a cycle must make.
    (NumberEntityDescription(
        key="import_factor", name="Import factor", native_min_value=0, native_max_value=3,
        native_step=0.001, mode=NumberMode.BOX, icon="mdi:multiplication",
    ), OPT_IMPORT_FACTOR),
    (NumberEntityDescription(
        key="import_surcharge", name="Import surcharge", native_min_value=-1, native_max_value=1,
        native_step=0.00001, mode=NumberMode.BOX, icon="mdi:cash-plus",
    ), OPT_IMPORT_SURCHARGE),
    (NumberEntityDescription(
        key="export_factor", name="Export factor", native_min_value=0, native_max_value=3,
        native_step=0.001, mode=NumberMode.BOX, icon="mdi:multiplication",
    ), OPT_EXPORT_FACTOR),
    (NumberEntityDescription(
        key="export_surcharge", name="Export surcharge", native_min_value=-1, native_max_value=1,
        native_step=0.00001, mode=NumberMode.BOX, icon="mdi:cash-minus",
    ), OPT_EXPORT_SURCHARGE),
    (NumberEntityDescription(
        key="wear_cost", name="Wear cost", native_min_value=0, native_max_value=0.2,
        native_step=0.001, mode=NumberMode.BOX, icon="mdi:battery-heart-variant",
    ), OPT_WEAR_COST),
    (NumberEntityDescription(
        key="minimum_margin", name="Minimum margin", native_min_value=0, native_max_value=0.2,
        native_step=0.001, mode=NumberMode.BOX, icon="mdi:cash-check",
    ), OPT_MIN_MARGIN),
    (NumberEntityDescription(
        key="expected_house_load", name="Expected house load", native_min_value=0,
        native_max_value=3000, native_step=10, mode=NumberMode.BOX,
        native_unit_of_measurement=UnitOfPower.WATT, icon="mdi:home-lightning-bolt",
    ), OPT_HOUSE_LOAD),
    (NumberEntityDescription(
        key="charge_efficiency", name="Charge efficiency", native_min_value=70,
        native_max_value=100, native_step=0.1, mode=NumberMode.BOX,
        native_unit_of_measurement=PERCENTAGE, icon="mdi:battery-arrow-up-outline",
    ), OPT_CHARGE_EFFICIENCY),
    (NumberEntityDescription(
        key="discharge_efficiency", name="Discharge efficiency", native_min_value=70,
        native_max_value=100, native_step=0.1, mode=NumberMode.BOX,
        native_unit_of_measurement=PERCENTAGE, icon="mdi:battery-arrow-down-outline",
    ), OPT_DISCHARGE_EFFICIENCY),
)
# Plain factors carry no unit; the others without a unit of their own are an
# amount per kWh in the currency Home Assistant is set to.
UNITLESS = {"import_factor", "export_factor"}
# Entity ids without the remark in the name.
OBJECT_ID_NAMES = {"net_metering": "Net metering"}

# The options are Operation mode names and stay untranslated, like the
# Operation mode itself.
PRICE_SELECTS: tuple[tuple[SelectEntityDescription, str], ...] = (
    # How to decide; its options are translated, unlike the mode names.
    (SelectEntityDescription(
        key="price_decision", translation_key="price_decision", name="Decision",
        options=list(PRICE_DECISIONS), icon="mdi:source-branch",
    ), OPT_PRICE_DECISION),
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
        status, value, imp, exp, plan, margin = PRICE_SENSORS
        return [
            PriceStatusSensor(entry, price, status),
            PriceValueSensor(entry, price, value),
            PriceFormulaSensor(entry, price, imp, "import_price"),
            PriceFormulaSensor(entry, price, exp, "export_value"),
            PlanSensor(entry, price, plan),
            MarginSensor(entry, price, margin),
        ]
    if platform == Platform.NUMBER:
        return [PriceNumber(entry, price, d, settings, key) for d, key in PRICE_NUMBERS]
    if platform == Platform.SELECT:
        return [PriceModeSelect(entry, price, d, settings, key) for d, key in PRICE_SELECTS]
    return []


def _currency(hass: HomeAssistant | None) -> str | None:
    return getattr(getattr(hass, "config", None), "currency", None)


def _per_kwh(hass: HomeAssistant | None) -> str | None:
    currency = _currency(hass)
    return f"{currency}/kWh" if currency else None


class PriceEntity(TranslatedName):
    """Shared: the device, and an update after every price decision."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, price: PriceControl, description: Any) -> None:
        self.entity_description = description
        self._price = price
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._set_name(description, english=OBJECT_ID_NAMES.get(description.key))
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
        if self.entity_description.native_unit_of_measurement:
            return self.entity_description.native_unit_of_measurement
        if self.entity_description.key in UNITLESS:
            return None
        return _per_kwh(self.hass)

    @property
    def native_value(self) -> float:
        return self._settings.get_float(self._key)

    async def async_set_native_value(self, value: float) -> None:
        self._settings.set(self._key, round(float(value), 5))
        self.async_write_ha_state()


class PriceModeSelect(_Setting, SelectEntity):
    """How to decide, or the Operation mode price control uses in one band."""

    @property
    def current_option(self) -> str | None:
        return self._settings.get(self._key)

    async def async_select_option(self, option: str) -> None:
        if option not in (self.entity_description.options or ()):
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
        # Always the currency of Home Assistant per kWh, so the statistics
        # keep one unit when the price sensor is changed.
        return _per_kwh(self.hass) or self._price.unit


class PriceFormulaSensor(PriceEntity, SensorEntity):
    """The import price or export value now, from the formulas."""

    def __init__(self, entry, price, description, attribute: str) -> None:
        super().__init__(entry, price, description)
        self._attribute = attribute

    @property
    def available(self) -> bool:
        return getattr(self._price, self._attribute) is not None

    @property
    def native_value(self) -> float | None:
        return getattr(self._price, self._attribute)

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _per_kwh(self.hass)


class PlanSensor(PriceEntity, SensorEntity):
    """What the day plan does now; the periods ahead as attributes."""

    # The periods change every quarter; the history needs only the action.
    _unrecorded_attributes = frozenset(
        {"charge_periods", "discharge_periods", "next_change", "plan_until"}
    )

    @property
    def available(self) -> bool:
        return self._price.plan is not None

    @property
    def native_value(self) -> str | None:
        return self._price.plan.action if self._price.plan else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._price.plan_attributes


class MarginSensor(PriceEntity, SensorEntity):
    """What the planned cycles earn over the prices known, after losses and wear."""

    @property
    def available(self) -> bool:
        return self._price.plan is not None

    @property
    def native_value(self) -> float | None:
        return round(self._price.plan.margin, 2) if self._price.plan else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _currency(self.hass)
