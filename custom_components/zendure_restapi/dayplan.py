"""The day plan: when to charge and when to discharge, from the prices ahead.

Pure arithmetic, no Home Assistant objects, so it can be tested and replayed
on its own. Price control feeds it the price list of a price sensor (Nord
Pool, EPEX Spot, Zonneplan and others: see read_prices), the tariff and the
battery, and acts on the action for the current period.

**Prices.** The sensor value is turned back into the bare spot price first,
because the sensor may already include VAT or a whole tariff. From the spot
price come the import price (what a kWh from the grid costs) and the export
value (what a kWh sent to the grid brings in). With net metering a kWh sent
to the grid is worth the import price.

**Margin per kWh.** Everything is counted per kWh in the cells. Charging
buys import / charge efficiency per kWh stored; discharging brings in value ×
discharge efficiency and wears the battery by the wear cost. A discharge has
to clear the minimum margin on top of that, so a cycle is made only when

    value(j) × η_discharge − import(i) / η_charge − wear ≥ minimum margin

A fixed amount per kWh, not a percentage: a percentage of a price near zero or
below means nothing.

**Dynamic programming.** Each period can charge at full power (the cheap
price mode), discharge (the expensive price mode: the expected house load
with smart_discharge_only, full power with quick_discharge) or do neither
(the in-between mode). Working back from the last known period, the plan
finds for every level of energy in the cells the best of the three, with
the energy still stored at the end worth the median import price ahead. The
first step of the best path is what price control does now; every quarter
of an hour it plans again with the actual state of charge.

Replayed on 30 consecutive days of a real installation (quarter
prices, measured house load, net metering), selling at full power earned
EUR 1.58 a day, the same as the home-energy planner that ran there; the two
fixed prices earned EUR 0.21. Under the 2027 rules it earned EUR 0.83 a day.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median
from typing import Any, Iterable

CONTAINS_SPOT = "spot"
CONTAINS_SPOT_VAT = "spot_vat"
CONTAINS_IMPORT = "import_price"
PRICE_CONTAINS = (CONTAINS_SPOT_VAT, CONTAINS_SPOT, CONTAINS_IMPORT)

ACTION_CHARGE = "charge"
ACTION_DISCHARGE = "discharge"
ACTION_IDLE = "idle"



@dataclass(frozen=True)
class Tariff:
    """Import price and export value as linear functions of the spot price."""

    import_factor: float = 1.21
    import_surcharge: float = 0.13505
    export_factor: float = 1.10
    export_surcharge: float = 0.02
    net_metering: bool = False
    contains: str = CONTAINS_SPOT_VAT

    def spot(self, value: float) -> float:
        """The bare spot price behind a sensor value."""
        if self.contains == CONTAINS_SPOT:
            return value
        factor = self.import_factor or 1.0
        if self.contains == CONTAINS_IMPORT:
            return (value - self.import_surcharge) / factor
        # Spot including VAT: the import factor is taken as the VAT factor.
        return value / factor

    def import_price(self, spot: float) -> float:
        return spot * self.import_factor + self.import_surcharge

    def export_value(self, spot: float) -> float:
        if self.net_metering:
            return self.import_price(spot)
        return spot * self.export_factor + self.export_surcharge


@dataclass(frozen=True)
class Battery:
    """What the plan needs to know of the battery, in kWh and kW."""

    usable_kwh: float           # between the lower and upper SOC limit, in the cells
    stored_kwh: float           # above the lower limit, now
    charge_kw: float            # AC, from the grid
    discharge_kw: float         # AC, to the house and the grid
    eta_charge: float = 0.95
    eta_discharge: float = 0.96
    house_kw: float = 0.4       # what the house draws while the battery covers it
    grid_charge: bool = True    # the cheap mode charges from the grid
    sell: bool = False          # the expensive mode discharges at full power


@dataclass(frozen=True)
class Period:
    start: datetime
    end: datetime
    value: float                # as the sensor gives it

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600


@dataclass
class Plan:
    """The outcome: an action and the energy per period, and what it earns."""

    periods: list[Period]
    actions: list[str]
    charge_kwh: list[float]     # AC from the grid
    discharge_kwh: list[float]  # AC out of the battery
    import_price: list[float]
    export_value: list[float]
    margin: float               # in currency, over the whole plan
    stored_kwh: float
    usable_kwh: float
    charge_w: int = 0           # the power the cheap mode charges at from the grid
    discharge_w: int = 0        # full power with quick_discharge, the house load otherwise

    @property
    def action(self) -> str:
        return self.actions[0] if self.actions else ACTION_IDLE

    def ranges(self, action: str) -> list[dict[str, Any]]:
        """Consecutive periods with one action, merged into start and end.

        `power` is the power setting the mode runs at, in W: negative for
        charging from the grid, positive for discharging. With
        smart_discharge_only the battery delivers what the house draws, up
        to that.
        """
        out: list[dict[str, Any]] = []
        last_end: datetime | None = None
        for period, act in zip(self.periods, self.actions):
            if act != action:
                last_end = None
                continue
            if last_end is not None and period.start == last_end and out:
                out[-1]["end"] = period.end.isoformat()
            else:
                power = -self.charge_w if action == ACTION_CHARGE else self.discharge_w
                out.append(
                    {"start": period.start.isoformat(), "end": period.end.isoformat(), "power": power}
                )
            last_end = period.end
        return out

    def next_change(self) -> datetime | None:
        """When the action changes from the current one."""
        for period, act in zip(self.periods, self.actions):
            if act != self.action:
                return period.start
        return None


# ── Prices ───────────────────────────────────────────────────────────────


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def price_scale(attributes: dict[str, Any]) -> float:
    """Factor from the sensor's figures to an amount per kWh.

    The Nord Pool sensor can give its prices in cents and per MWh or Wh
    instead of per kWh; its attributes say which. For other sensors a unit
    of measurement per MWh or Wh is converted.
    """
    scale = 0.01 if attributes.get("price_in_cents") is True else 1.0
    unit = attributes.get("unit")
    if unit is None:
        # Other sensors: only the unit of measurement tells, as in €/MWh.
        unit = str(attributes.get("unit_of_measurement") or "").rpartition("/")[2]
    per = {"MWh": 0.001, "Wh": 1000.0}.get(str(unit), 1.0)
    return scale * per


# Price lists, recognised by where they are kept. Each family is a group of
# attributes read together (today and tomorrow); the first family that gives
# prices is used, so two lists of different meaning are never mixed.
#   Nord Pool (HACS)       raw_today, raw_tomorrow: start, end, value
#   Energi Data Service    raw_today, raw_tomorrow: hour, price
#   EPEX Spot (HACS)       data: start_time, end_time, price_per_kwh
#   Zonneplan              forecast: start_date, end_date,
#                          price_tax_included.amount (× 10^-7)
#   Zonneplan (older)      forecast: datetime, electricity_price (× 10^-7)
#   ENTSO-e (HACS)         prices_today, prices_tomorrow, prices: time, price
#   Frank Energie (HACS)   prices: from, till, price
#   Tibber                 prices: start_time, price
PRICE_FAMILIES: tuple[tuple[str, ...], ...] = (
    ("raw_today", "raw_tomorrow"),
    ("data",),
    ("forecast",),
    ("prices_today", "prices_tomorrow", "prices"),
)
START_KEYS = ("start", "start_time", "start_date", "from", "time", "datetime", "hour")
END_KEYS = ("end", "end_time", "end_date", "till", "to")
# Value field and its factor to an amount per kWh; None: the sensor's own
# scale (price_scale), for the fields that carry no unit in their name.
VALUE_KEYS: tuple[tuple[str, float | None], ...] = (
    ("value", None),
    ("price_per_kwh", 1.0),
    ("price_tax_included.amount", 1e-7),
    ("electricity_price", 1e-7),
    ("price_ct_per_kwh", 0.01),
    ("price_eur_per_mwh", 0.001),
    ("price", None),
)
# Which integration a list comes from, by its family, start field and value
# field: shown in the status attributes, so a wrong sensor is quick to spot.
FORMAT_NAMES = {
    ("raw_today", "start", "value"): "nord_pool",
    ("raw_today", "hour", "price"): "energi_data_service",
    ("data", "start_time", "price_per_kwh"): "epex_spot",
    ("data", "start_time", "price_ct_per_kwh"): "epex_spot",
    ("data", "start_time", "price_eur_per_mwh"): "epex_spot",
    ("forecast", "start_date", "price_tax_included.amount"): "zonneplan",
    ("forecast", "datetime", "electricity_price"): "zonneplan",
    ("prices_today", "time", "price"): "entso_e",
    ("prices_today", "from", "price"): "frank_energie",
    ("prices_today", "start_time", "price"): "tibber",
}
# The length of a period whose end is not given and cannot be taken from the
# next one: the most common step in the list, else an hour.
DEFAULT_STEP = timedelta(hours=1)


def _field(item: dict[str, Any], path: str) -> Any:
    value: Any = item
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _first(item: dict[str, Any], keys: Iterable[str]) -> tuple[str | None, Any]:
    for key in keys:
        value = _field(item, key)
        if value is not None:
            return key, value
    return None, None


def _family(attributes: dict[str, Any], family: tuple[str, ...]) -> tuple[list[Period], tuple[str, str]]:
    """The prices in one family of attributes, and their start and value fields."""
    unit_scale = price_scale(attributes)
    rows: list[tuple[datetime, datetime | None, float]] = []
    fields: tuple[str, str] | None = None
    for name in family:
        if name == "raw_tomorrow" and not attributes.get("tomorrow_valid", True):
            continue
        items = attributes.get(name)
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            start_key, start_raw = _first(item, START_KEYS)
            start = _as_datetime(start_raw)
            end = _as_datetime(_first(item, END_KEYS)[1])
            key, raw = _first(item, (k for k, _ in VALUE_KEYS))
            if start is None or key is None:
                continue
            try:
                factor = dict(VALUE_KEYS)[key]
                value = float(raw) * (unit_scale if factor is None else factor)
            except (TypeError, ValueError):
                continue
            fields = fields or (str(start_key), key)
            rows.append((start, end, value))
    if not rows or fields is None:
        return [], ("", "")
    rows.sort(key=lambda r: r[0])
    starts = [r[0] for r in rows]
    steps = [b - a for a, b in zip(starts, starts[1:]) if b > a]
    step = max(set(steps), key=steps.count) if steps else DEFAULT_STEP
    seen: dict[datetime, Period] = {}
    for i, (start, end, value) in enumerate(rows):
        if end is None:
            nxt = next((s for s in starts[i + 1:] if s > start), None)
            end = nxt if nxt is not None and nxt - start <= step else start + step
        if end <= start:
            continue
        seen[start] = Period(start, end, value)
    return [seen[k] for k in sorted(seen)], fields


def _read(attributes: dict[str, Any]) -> tuple[list[Period], str | None]:
    for family in PRICE_FAMILIES:
        periods, fields = _family(attributes, family)
        if periods:
            return periods, FORMAT_NAMES.get((family[0], *fields), "other")
    return [], None


def read_prices(attributes: dict[str, Any]) -> list[Period]:
    """The price list of a price sensor: today, and tomorrow when known.

    Recognises the lists of Nord Pool (HACS), EPEX Spot, Zonneplan, ENTSO-e,
    Frank Energie, Tibber (a template sensor) and Energi Data Service
    (PRICE_FAMILIES). Values per kWh in
    the currency's main unit, whatever the sensor's own scale. Entries without
    a usable start or value are left out; a missing end is the next start.
    Sorted by start, duplicates dropped.
    """
    return _read(attributes)[0]


def price_format(attributes: dict[str, Any]) -> str | None:
    """Which integration the price list comes from, or None without a list."""
    return _read(attributes)[1]


def ahead(periods: list[Period], now: datetime) -> list[Period]:
    """The periods not yet over, the current one cut to what is left of it."""
    out = []
    for period in periods:
        if period.end <= now:
            continue
        if period.start < now:
            period = Period(now, period.end, period.value)
        out.append(period)
    return out


# ── The plan ─────────────────────────────────────────────────────────────

# The value of the energy in the cells is tabled in steps of 1 % of the
# usable capacity, and read between the steps.
LEVELS = 100
# A move that earns as much as doing nothing is still made now rather than
# later: the same price later may not come.
TIE = 1e-9


def _at(table: list[float], x: float) -> float:
    """`table` read at fractional level `x`, linear between the steps."""
    if x <= 0:
        return table[0]
    if x >= LEVELS:
        return table[LEVELS]
    low = int(x)
    frac = x - low
    return table[low] + (table[low + 1] - table[low]) * frac if frac else table[low]


def make_plan(
    periods: list[Period],
    tariff: Tariff,
    battery: Battery,
    wear: float,
    min_margin: float,
    keep_value: float | None = None,
) -> Plan:
    """The best action per period, by dynamic programming over the energy stored.

    `periods` are the periods ahead, the first one being the current one.
    `keep_value` is what a kWh still in the cells at the end of the plan is
    worth; None: the median import price ahead after discharge loss and wear.
    """
    n = len(periods)
    spot = [tariff.spot(p.value) for p in periods]
    imp = [tariff.import_price(s) for s in spot]
    exp = [tariff.export_value(s) for s in spot]
    eta_c = max(0.01, min(1.0, battery.eta_charge))
    eta_d = max(0.01, min(1.0, battery.eta_discharge))
    e_max = max(0.0, battery.usable_kwh)
    if n == 0 or e_max <= 0:
        return Plan(periods, [ACTION_IDLE] * n, [0.0] * n, [0.0] * n, imp, exp, 0.0, 0.0, e_max)
    step = e_max / LEVELS

    # What one period can move, in steps of the cells.
    house = max(0.0, min(battery.discharge_kw, battery.house_kw))
    out_kw = battery.discharge_kw if battery.sell else house
    up = [
        battery.charge_kw * p.hours * eta_c / step if battery.grid_charge else 0.0
        for p in periods
    ]
    down = [out_kw * p.hours / eta_d / step for p in periods]
    # Value of a kWh leaving the cells: the house share avoids import, the
    # rest is sold.
    share = house / out_kw if out_kw > 0 else 0.0
    value = [eta_d * (share * imp[k] + (1 - share) * exp[k]) for k in range(n)]
    if keep_value is None:
        keep_value = eta_d * median(imp) - wear
    # Every kWh discharged must also clear the minimum margin.
    hurdle = wear + min_margin

    def options(k: int, x: float, after: list[float]) -> list[tuple[float, int, float]]:
        """(value, action, level after) for doing nothing, charging, discharging."""
        out = [(_at(after, x), 0, x)]
        if up[k] > 0 and x < LEVELS:
            to = min(float(LEVELS), x + up[k])
            out.append((_at(after, to) - (to - x) * step / eta_c * imp[k], 1, to))
        if down[k] > 0 and x > 0:
            to = max(0.0, x - down[k])
            out.append((_at(after, to) + (x - to) * step * (value[k] - hurdle), 2, to))
        return out

    def pick(choices: list[tuple[float, int, float]]) -> tuple[float, int, float]:
        top = max(c[0] for c in choices)
        moving = [c for c in choices if c[1] and c[0] >= top - TIE]
        return moving[0] if moving else choices[0]

    # tables[k][l]: the most the periods from k on can earn from level l.
    tables: list[list[float]] = [[]] * (n + 1)
    tables[n] = [keep_value * level * step for level in range(LEVELS + 1)]
    for k in range(n - 1, -1, -1):
        after = tables[k + 1]
        tables[k] = [max(c[0] for c in options(k, float(level), after)) for level in range(LEVELS + 1)]

    # Follow the best path from the energy stored now.
    x = start = min(float(LEVELS), max(0.0, battery.stored_kwh / step))
    actions: list[str] = []
    charge_kwh: list[float] = []
    discharge_kwh: list[float] = []
    margin = 0.0
    for k in range(n):
        _, act, to = pick(options(k, x, tables[k + 1]))
        if act == 1:
            ac = (to - x) * step / eta_c
            actions.append(ACTION_CHARGE)
            charge_kwh.append(ac)
            discharge_kwh.append(0.0)
            margin -= ac * imp[k]
        elif act == 2:
            cells = (x - to) * step
            actions.append(ACTION_DISCHARGE)
            charge_kwh.append(0.0)
            discharge_kwh.append(cells * eta_d)
            margin += cells * (value[k] - wear)
        else:
            actions.append(ACTION_IDLE)
            charge_kwh.append(0.0)
            discharge_kwh.append(0.0)
        x = to
    # What is left over counts at what it is worth to keep.
    margin += (x - start) * step * keep_value
    return Plan(
        periods=periods,
        actions=actions,
        charge_kwh=charge_kwh,
        discharge_kwh=discharge_kwh,
        import_price=imp,
        export_value=exp,
        margin=margin,
        stored_kwh=start * step,
        usable_kwh=e_max,
        charge_w=round(battery.charge_kw * 1000) if battery.grid_charge else 0,
        discharge_w=round(out_kw * 1000),
    )
