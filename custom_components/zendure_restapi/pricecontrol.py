"""Price control: the Operation mode of a battery set from the electricity price.

An entry and a device of its own, linked to one battery. Its own settings —
price sensor, the two prices, the mode per band, on/off — live in its own
entry; the Operation mode it writes lives in the battery's entry.

Dynamic trading in its simplest form. At or below the cheap price the battery
charges, at or above the expensive price it discharges, and in between it
follows the meter or rests — each of the three in the mode chosen for it.
Works with any sensor that gives the current price per kWh.

The Operation mode is written only when the target changes, or when price
control is switched on. A mode set by hand therefore holds until the price
crosses one of the two limits; it is not taken back every quarter of an hour.
Off by default: an automation or planner that already steers the Operation
mode stays the only one that does.

Without a usable price — no sensor chosen, or the sensor unavailable — the
target is the in-between mode, which buys and sells nothing on purpose. A
cheap price at or above the expensive one is treated the same way: the two
limits contradict each other, so neither applies.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_interval,
)

from .const import (
    OPT_OPERATION_MODE,
    OPT_PRICE_BETWEEN_MODE,
    OPT_PRICE_CHEAP,
    OPT_PRICE_CHEAP_MODE,
    OPT_PRICE_CONTROL,
    OPT_PRICE_ENTITY,
    OPT_PRICE_EXPENSIVE,
    OPT_PRICE_EXPENSIVE_MODE,
    PRICE_STATUS_BETWEEN,
    PRICE_STATUS_CHEAP,
    PRICE_STATUS_EXPENSIVE,
    PRICE_STATUS_NO_PRICE,
    PRICE_STATUS_OFF,
    SIGNAL_PRICE_CHANGED,
)
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)

# Dynamic tariffs change per quarter or per hour; checking every quarter also
# catches a price sensor that changes its attributes but not its state.
INTERVAL = timedelta(minutes=15)


def decide(
    price: float | None, cheap: float, expensive: float
) -> str:
    """Which band a price falls in."""
    if price is None or cheap >= expensive:
        return PRICE_STATUS_NO_PRICE if price is None else PRICE_STATUS_BETWEEN
    if price <= cheap:
        return PRICE_STATUS_CHEAP
    if price >= expensive:
        return PRICE_STATUS_EXPENSIVE
    return PRICE_STATUS_BETWEEN


class PriceControl:
    """Sets the Operation mode of one battery from a price sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        settings: ZendureSettings,
        battery: ZendureSettings,
        battery_entry_id: str,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._settings = settings
        self._battery = battery
        self._battery_entry_id = battery_entry_id
        self._signal = SIGNAL_PRICE_CHANGED.format(entry.entry_id)
        self._unsub_price: Any = None
        self._unsub_timer: Any = None
        self._entity: str = ""
        self._enabled = False
        self._last_target: str | None = None
        self.price: float | None = None
        self.unit: str | None = None
        self.status: str = PRICE_STATUS_OFF
        self.target: str | None = None

    # ── Life cycle ───────────────────────────────────────────────────────

    @callback
    def async_start(self) -> None:
        self._unsub_timer = async_track_time_interval(self.hass, self._on_timer, INTERVAL)
        self.async_update()

    @callback
    def async_stop(self) -> None:
        for unsub in (self._unsub_price, self._unsub_timer):
            if unsub is not None:
                unsub()
        self._unsub_price = self._unsub_timer = None

    @callback
    def async_update(self) -> None:
        """Settings may have changed: follow the price sensor, then decide."""
        entity = str(self._settings.get(OPT_PRICE_ENTITY) or "")
        if entity != self._entity:
            if self._unsub_price is not None:
                self._unsub_price()
                self._unsub_price = None
            self._entity = entity
            if entity:
                self._unsub_price = async_track_state_change_event(
                    self.hass, [entity], self._on_price
                )
        enabled = self._settings.get_bool(OPT_PRICE_CONTROL)
        switched_on = enabled and not self._enabled
        self._enabled = enabled
        if switched_on:
            # Apply the current target now, even if it equals the last one.
            self._last_target = None
        self.evaluate()

    @callback
    def _on_price(self, _event: Event) -> None:
        self.evaluate()

    @callback
    def _on_timer(self, _now: Any) -> None:
        self.evaluate()

    # ── Decision ─────────────────────────────────────────────────────────

    def _read_price(self) -> float | None:
        if not self._entity:
            return None
        state = self.hass.states.get(self._entity)
        if state is None:
            return None
        self.unit = state.attributes.get("unit_of_measurement")
        try:
            return float(state.state)
        except (TypeError, ValueError):
            return None

    @callback
    def evaluate(self) -> None:
        """Work out the band and, when price control is on, the mode."""
        self.price = self._read_price()
        band = decide(
            self.price,
            self._settings.get_float(OPT_PRICE_CHEAP),
            self._settings.get_float(OPT_PRICE_EXPENSIVE),
        )
        mode_key = {
            PRICE_STATUS_CHEAP: OPT_PRICE_CHEAP_MODE,
            PRICE_STATUS_EXPENSIVE: OPT_PRICE_EXPENSIVE_MODE,
        }.get(band, OPT_PRICE_BETWEEN_MODE)
        self.target = str(self._settings.get(mode_key))

        if not self._enabled:
            self.status = PRICE_STATUS_OFF
            self._last_target = None
        else:
            self.status = band
            if self.target != self._last_target and self._battery_present():
                self._last_target = self.target
                if self._battery.get(OPT_OPERATION_MODE) != self.target:
                    _LOGGER.debug(
                        "Price control: price %s, %s -> Operation mode %s",
                        self.price, band, self.target,
                    )
                    self._battery.set(OPT_OPERATION_MODE, self.target)
        async_dispatcher_send(self.hass, self._signal)

    def _battery_present(self) -> bool:
        # The battery entry may have been removed while this one stays.
        return self.hass.config_entries.async_get_entry(self._battery_entry_id) is not None

    @property
    def attributes(self) -> dict[str, Any]:
        return {
            "price": self.price,
            "price_entity": self._entity or None,
            "target_mode": self.target,
            "operation_mode": self._battery.get(OPT_OPERATION_MODE),
            "cheap_price": self._settings.get_float(OPT_PRICE_CHEAP),
            "expensive_price": self._settings.get_float(OPT_PRICE_EXPENSIVE),
        }

    @property
    def signal(self) -> str:
        return self._signal
