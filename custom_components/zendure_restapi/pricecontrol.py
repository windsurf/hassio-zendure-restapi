"""Price control: the Operation mode of a battery set from the electricity price.

An entry and a device of its own, linked to one battery. Its own settings —
price sensor, how to decide, prices, tariff, modes, on/off — live in its own
entry; the Operation mode it writes lives in the battery's entry.

Three ways to decide, chosen with the Decision select:

- **Price bands** (since v1.6.0; called Fixed prices, `fixed_prices`,
  until v1.8.1). At or below the cheap price the battery charges, at or above
  the expensive price it discharges, and in between it follows the meter or
  rests. Works with any sensor that gives the current price per kWh.
- **Day plan** (v1.6.0). A plan over the prices of today and tomorrow from a
  price sensor with a price list (Nord Pool; since v1.6.5 also EPEX Spot,
  Zonneplan and others, see dayplan.read_prices), with the import price and the export
  value each from a formula of their own, the conversion losses and a
  minimum margin per kWh; see dayplan.py. A charge period runs the cheap
  price mode, a discharge period the expensive price mode, every other
  period the in-between mode.
- **Fixed tariff** (v1.8.2). A fixed-price contract: no price sensor, a fixed
  import price and export price, and an Operation mode for the day and one
  for the night. The day runs from sunrise plus its offset to sunset plus its
  offset, from the sun at Home Assistant's own location; checked every minute.
  The prices give the import and export price of the status and the realized
  margin, and the advice for Stop charging below.

The three modes per band are shared by both ways of deciding (v1.6.1). The
day plan also plans with them: quick_charge charges from the grid,
smart_charge_only does not; quick_discharge sells at full power,
smart_discharge_only covers the house only. Switching Decision or Net
metering fills the three in once with the presets of const.price_presets;
after that they are free to change until the next switch.

Both end in an Operation mode, so everything after the decision is shared. The Operation mode is written only when the target changes, or
when price control is switched on. A mode set by hand therefore holds until
the decision changes; it is not taken back every quarter of an hour. Off by
default: an automation or planner that already steers the Operation mode
stays the only one that does.

Since v1.8.0 the entry may steer a battery cluster or a Virtual cluster
instead of one battery. The day plan then plans for the group: the
usable and stored energy of every battery taking part added up, and the
charge and discharge power each may run (its own ceiling and Max share)
added up. The Operation mode it writes is the cluster's; the cluster divides
it over its batteries as it does a mode chosen by hand. The conversion
losses, the house load and the tariff are the entry's own, as for a battery.
A Virtual cluster leaves Follow source the moment Price Control writes a mode,
and returns to it when Price Control is switched off: off, the copies follow
the real battery again.

Without a usable price, or a day plan without a price list or battery data,
the target is the in-between mode. A price list once read is held when the
sensor drops out or shrinks (v1.6.9): the plan and the current price carry on
from it until its last period has passed. The status keeps showing the band;
the attributes prices_held and prices_held_until say that it runs on the held
list. Nothing is stored: after a restart there is nothing to hold. A
cheap price at or above the expensive one is treated the same way: the two
limits contradict each other, so neither applies.
"""

from __future__ import annotations

import logging
from typing import Any

from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE, SUN_EVENT_SUNSET
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import (
    PRICE_MODE_OPTIONS,
    price_presets,
    DECISION_BANDS,
    DECISION_BANDS_OLD,
    DECISION_PLAN,
    DECISION_TARIFF,
    decision_choice,
    DOMAIN,
    MODE_FOLLOW_SOURCE,
    MODE_QUICK_CHARGE,
    MODE_QUICK_DISCHARGE,
    MODE_STANDBY,
    OPT_CHARGE_EFFICIENCY,
    OPT_DISCHARGE_EFFICIENCY,
    OPT_EXPORT_FACTOR,
    OPT_EXPORT_SURCHARGE,
    OPT_FIXED_EXPORT_PRICE,
    OPT_FIXED_IMPORT_PRICE,
    OPT_HOUSE_LOAD,
    OPT_IMPORT_FACTOR,
    OPT_IMPORT_SURCHARGE,
    OPT_MAX_CHARGE_POWER,
    OPT_MAX_DISCHARGE_POWER,
    OPT_MIN_MARGIN,
    OPT_NET_METERING,
    OPT_OPERATION_MODE,
    OPT_PRICE_BETWEEN_MODE,
    OPT_PRICE_CHEAP,
    OPT_PRICE_CHEAP_MODE,
    OPT_PRICE_CONTAINS,
    OPT_PRICE_CONTROL,
    OPT_PRICE_DECISION,
    OPT_PRICE_ENTITY,
    OPT_PRICE_EXPENSIVE,
    OPT_PRICE_EXPENSIVE_MODE,
    OPT_SUNRISE_MODE,
    OPT_SUNRISE_OFFSET,
    OPT_SUNSET_MODE,
    OPT_SUNSET_OFFSET,
    OPT_WEAR_COST,
    PACK_PREFIX,
    PRICE_STATUS_BETWEEN,
    PRICE_STATUS_CHEAP,
    PRICE_STATUS_EXPENSIVE,
    PRICE_STATUS_NO_PLAN,
    PRICE_STATUS_NO_PRICE,
    PRICE_STATUS_OFF,
    PRICE_STATUS_PAUSED,
    PRICE_STATUS_SUN_DOWN,
    PRICE_STATUS_SUN_UP,
    SIGNAL_PRICE_CHANGED,
    stop_charge_advice,
)
from .dayplan import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    Battery,
    Period,
    Plan,
    Tariff,
    ahead,
    make_plan,
    price_format,
    price_scale,
    read_prices,
)
from .measure import BatteryMeasure, Prices
from .registry import pack_model
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)

# The setting behind the net metering mode select of v1.6.0, removed in v1.6.1,
# and the mark that the band modes have been carried over from v1.6.0.
OLD_NET_METERING_MODE = "price_net_metering_mode"
MIGRATED_V161 = "price_modes_v161"

# Dynamic tariffs change per quarter or per hour. Deciding just after every
# quarter also catches a price sensor that changes its attributes but not its
# state, and plans again with the state of charge of that moment.
QUARTERS = [0, 15, 30, 45]
SECOND = 5


def sun_band(
    now: datetime,
    sunrise: datetime | None,
    sunset: datetime | None,
    rise_offset_min: float,
    set_offset_min: float,
    above_horizon: bool = False,
) -> tuple[str, datetime | None, datetime | None]:
    """Day or night for the fixed tariff, with the window of the day (v1.8.2).

    The day runs from sunrise plus its offset to sunset plus its offset. A
    window that closes before it opens (large offsets on a short winter day)
    is no day at all. Without a sunrise or sunset (midnight sun, polar night)
    the sun itself decides.
    """
    if sunrise is None or sunset is None:
        return (PRICE_STATUS_SUN_UP if above_horizon else PRICE_STATUS_SUN_DOWN), None, None
    start = sunrise + timedelta(minutes=rise_offset_min)
    end = sunset + timedelta(minutes=set_offset_min)
    band = PRICE_STATUS_SUN_UP if start <= now < end else PRICE_STATUS_SUN_DOWN
    return band, start, end


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


def battery_state(
    data: dict[str, Any], pack_count: int, battery: ZendureSettings
) -> tuple[float, float, float, float] | None:
    """Usable and stored kWh, charge and discharge kW, from the battery's last poll.

    Capacity is the nominal capacity of the packs; usable is the part between
    the lower and the upper SOC limit, stored the part above the lower limit.
    The powers are the lower of the controller setting and the device
    ceiling, as the controller itself uses them. None when something is
    missing.
    """
    capacity = 0.0
    for index in range(1, pack_count + 1):
        prefix = f"{PACK_PREFIX}{index}."
        _, kwh = pack_model(data.get(f"{prefix}sn"), data.get(f"{prefix}packType"))
        if kwh:
            capacity += kwh
    try:
        soc = float(data["electricLevel"])
        floor = float(data.get("minSoc", 0)) / 10
        ceiling = float(data.get("socSet", 1000)) / 10
    except (KeyError, TypeError, ValueError):
        return None
    if capacity <= 0 or ceiling <= floor:
        return None

    def power(key: str, device_key: str) -> float:
        setting = battery.get_int(key)
        try:
            device = int(data.get(device_key) or 0)
        except (TypeError, ValueError):
            device = 0
        return (min(setting, device) if device > 0 else setting) / 1000

    usable = capacity * (ceiling - floor) / 100
    stored = capacity * max(0.0, min(soc, ceiling) - floor) / 100
    return (
        usable,
        stored,
        power(OPT_MAX_CHARGE_POWER, "chargeMaxLimit"),
        power(OPT_MAX_DISCHARGE_POWER, "inverseMaxPower"),
    )


def group_price_periods(hass: HomeAssistant, cluster_id: str) -> tuple:
    """The import prices of the cluster's own Price Control, for SOC protection (v1.8.1).

    (start, end, import price, start as HH:MM local) per period not yet
    over, start and end as epoch seconds: what the cluster package takes,
    which knows nothing of Home Assistant. Empty without a Price Control for
    this cluster, or without a price list; it need not steer.
    """
    for item in hass.data.get(DOMAIN, {}).values():
        if isinstance(item, PriceControl) and item.group and item.battery_entry_id == cluster_id:
            try:
                return tuple(
                    (start.timestamp(), end.timestamp(), price, dt_util.as_local(start).strftime("%H:%M"))
                    for start, end, price in item.import_periods()
                )
            except Exception:  # noqa: BLE001 - a price list must not stop protection
                _LOGGER.debug("No import periods from the cluster's price control", exc_info=True)
                return ()
    return ()


def _window_tuples(windows: list[tuple[datetime, datetime]]) -> tuple:
    """(start, end, start HH:MM, end HH:MM local), start and end as epoch seconds."""
    return tuple(
        (start.timestamp(), end.timestamp(),
         dt_util.as_local(start).strftime("%H:%M"), dt_util.as_local(end).strftime("%H:%M"))
        for start, end in windows
    )


def solar_windows_for(hass: HomeAssistant, entry_id: str, group: bool) -> tuple | None:
    """The day windows of the Price Control of a battery or cluster under the
    fixed tariff, for SOC protection (v1.8.2); None for the other decisions or
    without a Price Control."""
    for item in hass.data.get(DOMAIN, {}).values():
        if isinstance(item, PriceControl) and bool(item.group) == group and item.battery_entry_id == entry_id:
            try:
                windows = item.solar_windows()
            except Exception:  # noqa: BLE001 - the sun must not stop protection
                _LOGGER.debug("No day windows from price control", exc_info=True)
                return None
            return None if windows is None else _window_tuples(windows)
    return None


def group_solar_windows(hass: HomeAssistant, cluster_id: str) -> tuple | None:
    """The day windows of the cluster's own Price Control (v1.8.2)."""
    return solar_windows_for(hass, cluster_id, True)


def group_state(members: list[Any]) -> tuple[float, float, float, float] | None:
    """Usable and stored kWh, charge and discharge kW of a cluster (v1.8.0).

    The sum over the batteries that take part and report: energy as
    battery_state reads it from each battery's last poll, power as the cluster
    itself may ask of each, the lower of its own ceiling and its Max share. A
    battery that does not take part, or has not reported, adds nothing; with
    none left there is no plan.
    """
    usable = stored = charge_kw = discharge_kw = 0.0
    counted = 0
    for member in members:
        try:
            reading = member.reading()
        except Exception:  # noqa: BLE001 - one battery must not stop the plan
            _LOGGER.debug("Price Control: no reading of %s", getattr(member, "name", "?"), exc_info=True)
            continue
        if not reading.available:
            continue
        controller = getattr(member, "controller", None)
        coordinator = getattr(controller, "coordinator", None)
        data = getattr(coordinator, "data", None)
        if not data:
            continue
        state = battery_state(data, getattr(coordinator, "pack_count", 0), member.own)
        if state is None:
            continue
        usable += state[0]
        stored += state[1]
        charge_kw += max(0, reading.charge_cap) / 1000
        discharge_kw += max(0, reading.discharge_cap) / 1000
        counted += 1
    if not counted or usable <= 0:
        return None
    return usable, stored, charge_kw, discharge_kw


class PriceControl:
    """Sets the Operation mode of one battery, or of a cluster, from a price sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        settings: ZendureSettings,
        battery: ZendureSettings,
        battery_entry_id: str,
        group: bool = False,
        virtual: bool = False,
    ) -> None:
        self.hass = hass
        self._entry = entry
        self._settings = settings
        # The settings that hold the Operation mode it writes: the battery's,
        # or since v1.8.0 the cluster's.
        self._battery = battery
        self._battery_entry_id = battery_entry_id
        self.group = group
        # A Virtual cluster goes back to Follow source when switched off (v1.8.0).
        self.virtual = virtual
        self._signal = SIGNAL_PRICE_CHANGED.format(entry.entry_id)
        self._unsub_price: Any = None
        self._unsub_timer: Any = None
        self._unsub_minute: Any = None
        # Fixed tariff (v1.8.2): the window of the day, for the status.
        self.sun_window: tuple[datetime | None, datetime | None] = (None, None)
        self._entity: str = ""
        self._enabled = False
        self._last_target: str | None = None
        self.price: float | None = None
        self.unit: str | None = None
        # The integration the price list comes from (v1.6.5), or None.
        self.price_format: str | None = None
        self.status: str = PRICE_STATUS_OFF
        self.target: str | None = None
        self.import_price: float | None = None
        self.export_value: float | None = None
        # Import price and export value per period of the price list, today
        # and tomorrow (v1.6.6): [(start, import, export)].
        self.price_curve: list[tuple[Any, float, float]] = []
        self.plan: Plan | None = None
        # The last usable price list and its format (v1.6.9): held while the
        # sensor gives none, or a shorter one, until it has run out.
        self._held: list[Period] = []
        self._held_format: str | None = None
        self.prices_held = False
        # The price list of the last evaluation, for SOC protection (v1.7.6).
        self._periods: list[Period] = []
        self._generation = 0
        # Decision and Net metering as last seen; None until the first update,
        # so a restart never overwrites the modes.
        self._stand: tuple[str, bool] | None = None

    # ── Life cycle ───────────────────────────────────────────────────────

    @callback
    def async_start(self) -> None:
        self._unsub_timer = async_track_time_change(
            self.hass, self._on_timer, minute=QUARTERS, second=SECOND
        )
        # The fixed tariff follows the sun, to the minute (v1.8.2).
        self._unsub_minute = async_track_time_change(self.hass, self._on_minute, second=SECOND)
        self.async_update()

    @callback
    def async_stop(self) -> None:
        for unsub in (self._unsub_price, self._unsub_timer, self._unsub_minute):
            if unsub is not None:
                unsub()
        self._unsub_price = self._unsub_timer = self._unsub_minute = None

    @property
    def battery_controller(self) -> Any:
        """The battery's controller as it is now: a reload of the battery
        replaces it, without reloading price control. None for a cluster."""
        if self.group:
            return None
        runtime = self.hass.data.get(DOMAIN, {}).get(f"{self._battery_entry_id}_runtime")
        return runtime["controller"] if runtime else None

    def group_members(self) -> list[Any]:
        """The batteries of the cluster it steers, as they are now (v1.8.0).

        Looked up on every call: a reload of the cluster replaces its runtime
        without reloading price control. A battery cluster keeps its members
        itself; a Virtual cluster keeps them in its cluster.
        """
        runtime = self.hass.data.get(DOMAIN, {}).get(self._battery_entry_id)
        cluster = getattr(runtime, "cluster", runtime)
        members = getattr(cluster, "members", None)
        return list(members.values()) if isinstance(members, dict) else []

    @property
    def measure(self) -> BatteryMeasure | None:
        controller = self.battery_controller
        return controller.measure if controller is not None else None

    @property
    def battery_entry_id(self) -> str:
        return self._battery_entry_id

    def prices(self) -> Prices | None:
        """The prices of this moment, for the realized margin; None without.

        Whether or not price control steers: the margin measures the battery,
        whoever drives it.
        """
        if self.import_price is None or self.export_value is None:
            return None
        s = self._settings
        return Prices(
            import_price=self.import_price,
            export_value=self.export_value,
            eta_discharge=s.get_float(OPT_DISCHARGE_EFFICIENCY) / 100,
            wear=s.get_float(OPT_WEAR_COST),
        )

    @callback
    def async_update(self) -> None:
        """Settings may have changed: follow the price sensor, then decide."""
        entity = str(self._settings.get(OPT_PRICE_ENTITY) or "")
        if entity != self._entity:
            if self._unsub_price is not None:
                self._unsub_price()
                self._unsub_price = None
            self._entity = entity
            # Another sensor: what was held belonged to the old one.
            self._held = []
            self._held_format = None
            if entity:
                self._unsub_price = async_track_state_change_event(
                    self.hass, [entity], self._on_price
                )
        stand = (
            str(self._settings.get(OPT_PRICE_DECISION)),
            self._settings.get_bool(OPT_NET_METERING),
        )
        if self._stand is None:
            # Carries on with the migrated settings in the same call: when the
            # integration is reloaded rather than Home Assistant restarted,
            # this runs before the update listener exists, so no second call
            # would follow (v1.6.2).
            self._migrate_net_metering_mode()
            self._migrate_decision()
        if self._stand is not None and stand != self._stand:
            # Decision or Net metering switched: the presets of the new
            # stand, once. The write calls this method again through the
            # update listener, with the same stand, so it does not repeat;
            # it carries on here regardless, so nothing depends on that call.
            self._settings.set_many(price_presets(*stand))
        self._stand = stand
        enabled = self._settings.get_bool(OPT_PRICE_CONTROL)
        switched_on = enabled and not self._enabled
        switched_off = self._enabled and not enabled
        self._enabled = enabled
        if switched_off:
            self._release()
        if switched_on:
            # Apply the current target now, even if it equals the last one.
            self._last_target = None
        self.evaluate()

    def _migrate_decision(self) -> bool:
        """Store Price bands in place of "fixed_prices", once (v1.8.2).

        Reading already maps the old value (settings.decision_choice); this
        puts the new one in the options, so nothing older stays behind.
        """
        if self._entry.options.get(OPT_PRICE_DECISION) != DECISION_BANDS_OLD:
            return False
        options = dict(self._entry.options)
        options[OPT_PRICE_DECISION] = DECISION_BANDS
        self.hass.config_entries.async_update_entry(self._entry, options=options)
        return True

    def _migrate_net_metering_mode(self) -> bool:
        """Carry the behaviour of v1.6.0 over to the shared band modes, once.

        In v1.6.0 the day plan always charged with quick_charge and
        discharged with quick_discharge, whatever the band modes said, and a
        fourth select, the net metering mode (standby unless changed),
        replaced the in-between mode while Net metering was on. From v1.6.1
        the band modes are what both ways of deciding use, so they are set
        to what v1.6.0 did: the battery keeps doing the same. Runs once per
        entry, marked in the options.
        """
        options = dict(self._entry.options)
        if options.get(MIGRATED_V161):
            return False
        old = options.pop(OLD_NET_METERING_MODE, MODE_STANDBY)
        if options.get(OPT_PRICE_DECISION) == DECISION_PLAN:
            options[OPT_PRICE_CHEAP_MODE] = MODE_QUICK_CHARGE
            options[OPT_PRICE_EXPENSIVE_MODE] = MODE_QUICK_DISCHARGE
        if options.get(OPT_NET_METERING) and old in PRICE_MODE_OPTIONS[OPT_PRICE_BETWEEN_MODE]:
            options[OPT_PRICE_BETWEEN_MODE] = old
        options[MIGRATED_V161] = True
        self.hass.config_entries.async_update_entry(self._entry, options=options)
        return True

    @callback
    def _on_price(self, _event: Event) -> None:
        self.evaluate()

    @callback
    def _on_timer(self, _now: Any) -> None:
        self.evaluate()

    @callback
    def _on_minute(self, _now: Any) -> None:
        # Only the fixed tariff needs the minute; the others decide per quarter.
        if self._settings.get(OPT_PRICE_DECISION) == DECISION_TARIFF:
            self.evaluate()

    # ── Inputs ───────────────────────────────────────────────────────────

    def _read_price(self) -> tuple[float | None, dict[str, Any]]:
        if not self._entity:
            return None, {}
        state = self.hass.states.get(self._entity)
        if state is None:
            return None, {}
        self.unit = state.attributes.get("unit_of_measurement")
        try:
            return float(state.state), dict(state.attributes)
        except (TypeError, ValueError):
            return None, dict(state.attributes)

    def tariff(self) -> Tariff:
        s = self._settings
        return Tariff(
            import_factor=s.get_float(OPT_IMPORT_FACTOR),
            import_surcharge=s.get_float(OPT_IMPORT_SURCHARGE),
            export_factor=s.get_float(OPT_EXPORT_FACTOR),
            export_surcharge=s.get_float(OPT_EXPORT_SURCHARGE),
            net_metering=s.get_bool(OPT_NET_METERING),
            contains=str(s.get(OPT_PRICE_CONTAINS)),
        )

    def _battery_input(self) -> Battery | None:
        if self.group:
            state = group_state(self.group_members())
        else:
            coordinator = self.hass.data.get(DOMAIN, {}).get(self._battery_entry_id)
            data = getattr(coordinator, "data", None)
            if not data:
                return None
            state = battery_state(data, getattr(coordinator, "pack_count", 0), self._battery)
        if state is None:
            return None
        usable, stored, charge_kw, discharge_kw = state
        s = self._settings
        return Battery(
            usable_kwh=usable,
            stored_kwh=stored,
            charge_kw=charge_kw,
            discharge_kw=discharge_kw,
            eta_charge=s.get_float(OPT_CHARGE_EFFICIENCY) / 100,
            eta_discharge=s.get_float(OPT_DISCHARGE_EFFICIENCY) / 100,
            house_kw=s.get_float(OPT_HOUSE_LOAD) / 1000,
            # The plan can only do what the band modes do.
            grid_charge=s.get(OPT_PRICE_CHEAP_MODE) == MODE_QUICK_CHARGE,
            sell=s.get(OPT_PRICE_EXPENSIVE_MODE) == MODE_QUICK_DISCHARGE,
        )

    # ── Decision ─────────────────────────────────────────────────────────

    def _price_list(self, attributes: dict[str, Any], now: Any) -> list[Period]:
        """The price list to work with: the sensor's, or the one held (v1.6.9).

        A fresh list replaces the held one when it reaches at least as far
        ahead, so a corrected list or the new day at midnight is taken over,
        and a source that drops tomorrow again is not. An empty or shorter
        list leaves the held one in use while it still has periods ahead.
        """
        fresh = read_prices(attributes)
        if fresh and (not self._held or fresh[-1].end >= self._held[-1].end):
            self._held = fresh
            self._held_format = price_format(attributes)
            self.prices_held = False
            return fresh
        if not ahead(self._held, now):
            self._held = []
            self._held_format = None
            self.prices_held = False
            return fresh
        self.prices_held = True
        return self._held

    def _plan_inputs(self, tariff: Tariff, periods: list[Period]) -> tuple | str:
        """What the day plan needs, or the status when it cannot plan."""
        periods = ahead(periods, dt_util.now())
        if not periods:
            return PRICE_STATUS_NO_PRICE
        battery = self._battery_input()
        if battery is None:
            return PRICE_STATUS_NO_PLAN
        return (
            periods,
            tariff,
            battery,
            self._settings.get_float(OPT_WEAR_COST),
            self._settings.get_float(OPT_MIN_MARGIN),
        )

    @callback
    def evaluate(self) -> None:
        """Work out the band and, when price control is on, the mode.

        The day plan is worked out in the executor: two days of quarters take
        tens of milliseconds, too long for the event loop on a small machine.
        """
        if self._settings.get(OPT_PRICE_DECISION) == DECISION_TARIFF:
            self._evaluate_tariff()
            return
        self.sun_window = (None, None)
        now = dt_util.now()
        self.price, attributes = self._read_price()
        periods = self._price_list(attributes, now)
        self.price_format = self._held_format if self.prices_held else price_format(attributes)
        if self.price is not None:
            # Per kWh in the currency's main unit, whatever the sensor's scale.
            self.price = round(self.price * price_scale(attributes), 6)
        elif self.prices_held:
            # The sensor gives no price: the held period of this moment.
            current = next((p for p in self._held if p.start <= now < p.end), None)
            if current is not None:
                self.price = round(current.value, 6)
        self._periods = periods
        tariff = self.tariff()
        if self.price is None:
            self.import_price = self.export_value = None
        else:
            spot = tariff.spot(self.price)
            self.import_price = round(tariff.import_price(spot), 5)
            self.export_value = round(tariff.export_value(spot), 5)
        self.price_curve = [
            (
                period.start,
                round(tariff.import_price(tariff.spot(period.value)), 5),
                round(tariff.export_value(tariff.spot(period.value)), 5),
            )
            for period in periods
        ]

        self._generation += 1
        if self._settings.get(OPT_PRICE_DECISION) != DECISION_PLAN:
            self.plan = None
            self._apply(
                decide(
                    self.price,
                    self._settings.get_float(OPT_PRICE_CHEAP),
                    self._settings.get_float(OPT_PRICE_EXPENSIVE),
                )
            )
            return
        inputs = self._plan_inputs(tariff, periods)
        if isinstance(inputs, str):
            self.plan = None
            self._apply(inputs)
            return
        self.hass.async_create_task(self._async_plan(self._generation, inputs))

    def _sun_times(self, now: datetime) -> tuple[datetime | None, datetime | None]:
        """Today's sunrise and sunset at Home Assistant's location; None
        where the sun does not rise or set today."""
        try:
            day = dt_util.as_local(now).date()
            return (
                get_astral_event_date(self.hass, SUN_EVENT_SUNRISE, day),
                get_astral_event_date(self.hass, SUN_EVENT_SUNSET, day),
            )
        except Exception:  # noqa: BLE001 - no location: let the sun entity decide
            return None, None

    def _evaluate_tariff(self) -> None:
        """Fixed tariff (v1.8.2): the mode of the day or of the night.

        No price sensor and no plan. The import price is the fixed one; the
        export price is the fixed one, or the import price under net metering.
        A price sensor that is still set only shows its price.
        """
        now = dt_util.now()
        s = self._settings
        self.price, _attributes = self._read_price()
        self.price_format = None
        self._held = []
        self._held_format = None
        self.prices_held = False
        self._periods = []
        self.price_curve = []
        self.plan = None
        self.import_price = round(s.get_float(OPT_FIXED_IMPORT_PRICE), 7)
        self.export_value = (
            self.import_price if s.get_bool(OPT_NET_METERING)
            else round(s.get_float(OPT_FIXED_EXPORT_PRICE), 7)
        )
        sun = self.hass.states.get("sun.sun")
        above = sun is not None and sun.state == "above_horizon"
        sunrise, sunset = self._sun_times(now)
        rise_offset, set_offset = s.get_float(OPT_SUNRISE_OFFSET), s.get_float(OPT_SUNSET_OFFSET)
        band, start, end = sun_band(now, sunrise, sunset, rise_offset, set_offset, above)
        if end is not None and now >= end:
            # After the evening switch the heading shows the next day (v1.8.2):
            # never a window that is over. The band itself stays today's.
            sunrise, sunset = self._sun_times(now + timedelta(days=1))
            _next, next_start, next_end = sun_band(now, sunrise, sunset, rise_offset, set_offset, above)
            if next_start is not None:
                start, end = next_start, next_end
        self.sun_window = (start, end)
        self._generation += 1
        self._apply(band)

    async def _async_plan(self, generation: int, inputs: tuple) -> None:
        plan = await self.hass.async_add_executor_job(make_plan, *inputs)
        if generation != self._generation:
            return  # overtaken by a newer evaluation
        self.plan = plan
        self._apply(
            {
                ACTION_CHARGE: PRICE_STATUS_CHEAP,
                ACTION_DISCHARGE: PRICE_STATUS_EXPENSIVE,
            }.get(plan.action, PRICE_STATUS_BETWEEN),
        )

    def _release(self) -> None:
        """Switched off: a Virtual cluster follows the real battery again (v1.8.0).

        Only on the switch going off, not at a restart or a reload, and only
        for a Virtual cluster: a battery or a battery cluster keeps the mode it
        has, as before, since nothing else would steer it.
        """
        if self.virtual and self._battery_present():
            _LOGGER.debug("Price control off: the Virtual cluster follows its source again")
            self._battery.set(OPT_OPERATION_MODE, MODE_FOLLOW_SOURCE)

    def solar_windows(self) -> list[tuple[datetime, datetime]] | None:
        """The windows of the day, today and tomorrow, under the fixed tariff (v1.8.2).

        For SOC protection: every quarter costs the same, so it waits for the
        sun. Sunrise plus its offset to sunset plus its offset, as the
        decision itself; a window that closes before it opens is left out.
        None for the other decisions, which have a price list.
        """
        s = self._settings
        if decision_choice(s.get(OPT_PRICE_DECISION)) != DECISION_TARIFF:
            return None
        now = dt_util.now()
        windows = []
        for days in (0, 1):
            sunrise, sunset = self._sun_times(now + timedelta(days=days))
            if sunrise is None or sunset is None:
                continue
            start = sunrise + timedelta(minutes=s.get_float(OPT_SUNRISE_OFFSET))
            end = sunset + timedelta(minutes=s.get_float(OPT_SUNSET_OFFSET))
            if end > start:
                windows.append((start, end))
        return windows

    def import_periods(self) -> list[tuple[Any, Any, float]]:
        """(start, end, import price) of the periods not yet over (v1.7.6).

        For SOC protection with Price Control on the battery, and since v1.8.1
        on the cluster: whether or not Price Control steers, as long as it has
        a price list.
        """
        tariff = self.tariff()
        return [
            (p.start, p.end, round(tariff.import_price(tariff.spot(p.value)), 5))
            for p in ahead(self._periods, dt_util.now())
        ]

    @callback
    def _apply(self, band: str) -> None:
        """The mode for a band, written when price control is on and it changed.

        Both ways of deciding take the mode chosen for each band: the cheap
        price mode, the expensive price mode, and the in-between mode for
        everything else, including no price or no plan.
        """
        mode_key = {
            PRICE_STATUS_CHEAP: OPT_PRICE_CHEAP_MODE,
            PRICE_STATUS_EXPENSIVE: OPT_PRICE_EXPENSIVE_MODE,
            PRICE_STATUS_SUN_UP: OPT_SUNRISE_MODE,
            PRICE_STATUS_SUN_DOWN: OPT_SUNSET_MODE,
        }.get(band, OPT_PRICE_BETWEEN_MODE)
        self.target = str(self._settings.get(mode_key))

        if not self._enabled:
            self.status = PRICE_STATUS_OFF
            self._last_target = None
        elif self._coordinated():
            # The battery cluster sets this battery's mode (v1.7.0); two
            # writers on one mode is what it exists to prevent.
            self.status = PRICE_STATUS_PAUSED
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

    def _coordinated(self) -> bool:
        """Whether the battery takes part in the battery cluster; never for a cluster."""
        if self.group:
            return False
        runtime = self.hass.data.get(DOMAIN, {}).get(f"{self._battery_entry_id}_runtime")
        view = runtime.get("view") if isinstance(runtime, dict) else None
        return view is not None and view.coordinated

    def _battery_present(self) -> bool:
        # The battery entry may have been removed while this one stays.
        return self.hass.config_entries.async_get_entry(self._battery_entry_id) is not None

    @property
    def attributes(self) -> dict[str, Any]:
        attributes = {
            "price": self.price,
            "price_entity": self._entity or None,
            "price_name": self._price_name(),
            "price_format": self.price_format,
            "decision": self._settings.get(OPT_PRICE_DECISION),
            # What it steers: one battery, or a cluster (v1.8.0).
            "steers": "cluster" if self.group else "battery",
            "target_mode": self.target,
            "operation_mode": self._battery.get(OPT_OPERATION_MODE),
            "cheap_price": self._settings.get_float(OPT_PRICE_CHEAP),
            "expensive_price": self._settings.get_float(OPT_PRICE_EXPENSIVE),
            "import_price": self.import_price,
            "export_value": self.export_value,
            "prices_held": self.prices_held,
            "prices_held_until": (
                self._held[-1].end.isoformat() if self.prices_held and self._held else None
            ),
        }
        if self._settings.get(OPT_PRICE_DECISION) == DECISION_TARIFF:
            # Fixed tariff (v1.8.2): the day window, and from the two prices
            # the lowest charging power at which storing beats exporting.
            # As text for the dashboard heading: the times of the day, and
            # the advice in W or that storing does not pay.
            start, end = self.sun_window
            attributes["day_from"] = dt_util.as_local(start).strftime("%H:%M") if start else None
            attributes["day_until"] = dt_util.as_local(end).strftime("%H:%M") if end else None
            if self.import_price is not None and self.export_value is not None:
                advice = stop_charge_advice(self.import_price, self.export_value)
                attributes["stop_charging_below_advice"] = (
                    f"{advice} W" if advice is not None else "storing does not pay"
                )
        return attributes

    def _price_name(self) -> str | None:
        """The name of the price sensor, for the dashboard heading (v1.6.5)."""
        if not self._entity:
            return None
        state = self.hass.states.get(self._entity)
        name = state.attributes.get("friendly_name") if state is not None else None
        return str(name) if name else self._entity

    @property
    def plan_attributes(self) -> dict[str, Any]:
        plan = self.plan
        if plan is None:
            return {}
        change = plan.next_change()
        return {
            "charge_periods": plan.ranges(ACTION_CHARGE),
            "discharge_periods": plan.ranges(ACTION_DISCHARGE),
            "next_change": change.isoformat() if change else None,
            "charge_kwh": round(sum(plan.charge_kwh), 2),
            "discharge_kwh": round(sum(plan.discharge_kwh), 2),
            "stored_kwh": round(plan.stored_kwh, 2),
            "usable_kwh": round(plan.usable_kwh, 2),
            "plan_until": plan.periods[-1].end.isoformat() if plan.periods else None,
        }

    @property
    def signal(self) -> str:
        return self._signal
