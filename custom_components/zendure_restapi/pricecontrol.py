"""Price control: the Operation mode of a battery set from the electricity price.

An entry and a device of its own, linked to one battery. Its own settings —
price sensor, how to decide, prices, tariff, modes, on/off — live in its own
entry; the Operation mode it writes lives in the battery's entry.

Two ways to decide, chosen with the Decision select:

- **Fixed prices** (since v1.6.0). At or below the cheap price the battery
  charges, at or above the expensive price it discharges, and in between it
  follows the meter or rests. Works with any sensor that gives the current
  price per kWh.
- **Day plan** (v1.6.0). A plan over the prices of today and tomorrow from a
  Nord Pool sensor (HACS integration), with the import price and the export
  value each from a formula of their own, the conversion losses and a
  minimum margin per kWh; see dayplan.py. A charge period runs the cheap
  price mode, a discharge period the expensive price mode, every other
  period the in-between mode.

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

Without a usable price, or a day plan without a price list or battery data,
the target is the in-between mode. A
cheap price at or above the expensive one is treated the same way: the two
limits contradict each other, so neither applies.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.util import dt as dt_util

from .const import (
    PRICE_MODE_OPTIONS,
    price_presets,
    DECISION_PLAN,
    DOMAIN,
    MODE_QUICK_CHARGE,
    MODE_QUICK_DISCHARGE,
    MODE_STANDBY,
    OPT_CHARGE_EFFICIENCY,
    OPT_DISCHARGE_EFFICIENCY,
    OPT_EXPORT_FACTOR,
    OPT_EXPORT_SURCHARGE,
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
    OPT_WEAR_COST,
    PACK_PREFIX,
    PRICE_STATUS_BETWEEN,
    PRICE_STATUS_CHEAP,
    PRICE_STATUS_EXPENSIVE,
    PRICE_STATUS_NO_PLAN,
    PRICE_STATUS_NO_PRICE,
    PRICE_STATUS_OFF,
    SIGNAL_PRICE_CHANGED,
)
from .dayplan import (
    ACTION_CHARGE,
    ACTION_DISCHARGE,
    Battery,
    Plan,
    Tariff,
    ahead,
    make_plan,
    price_scale,
    read_prices,
)
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
        self.import_price: float | None = None
        self.export_value: float | None = None
        self.plan: Plan | None = None
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
        if self._stand is not None and stand != self._stand:
            # Decision or Net metering switched: the presets of the new
            # stand, once. The write calls this method again through the
            # update listener, with the same stand, so it does not repeat;
            # it carries on here regardless, so nothing depends on that call.
            self._settings.set_many(price_presets(*stand))
        self._stand = stand
        enabled = self._settings.get_bool(OPT_PRICE_CONTROL)
        switched_on = enabled and not self._enabled
        self._enabled = enabled
        if switched_on:
            # Apply the current target now, even if it equals the last one.
            self._last_target = None
        self.evaluate()

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

    def _plan_inputs(self, tariff: Tariff, attributes: dict[str, Any]) -> tuple | str:
        """What the day plan needs, or the status when it cannot plan."""
        periods = ahead(read_prices(attributes), dt_util.now())
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
        self.price, attributes = self._read_price()
        if self.price is not None:
            # Per kWh in the currency's main unit, whatever the sensor's scale.
            self.price = round(self.price * price_scale(attributes), 6)
        tariff = self.tariff()
        if self.price is None:
            self.import_price = self.export_value = None
        else:
            spot = tariff.spot(self.price)
            self.import_price = round(tariff.import_price(spot), 5)
            self.export_value = round(tariff.export_value(spot), 5)

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
        inputs = self._plan_inputs(tariff, attributes)
        if isinstance(inputs, str):
            self.plan = None
            self._apply(inputs)
            return
        self.hass.async_create_task(self._async_plan(self._generation, inputs))

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
            "decision": self._settings.get(OPT_PRICE_DECISION),
            "target_mode": self.target,
            "operation_mode": self._battery.get(OPT_OPERATION_MODE),
            "cheap_price": self._settings.get_float(OPT_PRICE_CHEAP),
            "expensive_price": self._settings.get_float(OPT_PRICE_EXPENSIVE),
            "import_price": self.import_price,
            "export_value": self.export_value,
        }

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
