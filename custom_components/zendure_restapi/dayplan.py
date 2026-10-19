"""The day plan: when to charge and when to discharge, from the prices ahead.

Pure arithmetic, no Home Assistant objects, so it can be tested and replayed
on its own. Price control feeds it the price list of a Nord Pool sensor (the
HACS integration: attributes raw_today and raw_tomorrow), the tariff and the
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
from datetime import datetime
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


def read_prices(attributes: dict[str, Any]) -> list[Period]:
    """The price list of a Nord Pool sensor (HACS): today, and tomorrow when known.

    Values per kWh in the currency's main unit, whatever the sensor's own
    scale. Entries without a usable start, end or value are left out; a list
    that is not there gives nothing. Sorted by start, duplicates dropped.
    """
    scale = price_scale(attributes)
    lists: list[Iterable[Any]] = [attributes.get("raw_today") or []]
    if attributes.get("tomorrow_valid", True):
        lists.append(attributes.get("raw_tomorrow") or [])
    seen: dict[datetime, Period] = {}
    for items in lists:
        if not isinstance(items, (list, tuple)):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            start = _as_datetime(item.get("start"))
            end = _as_datetime(item.get("end"))
            try:
                value = float(item.get("value")) * scale
            except (TypeError, ValueError):
                continue
            if start is None or end is None or end <= start:
                continue
            seen[start] = Period(start, end, value)
    return [seen[k] for k in sorted(seen)]


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
