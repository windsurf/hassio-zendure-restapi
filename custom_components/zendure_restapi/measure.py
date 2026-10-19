"""Measured diagnostics: what the battery actually does, from its own readings.

The settings of price control and of the controller are figures somebody
typed in once: a charge and a discharge efficiency, a usable capacity, a wear
cost. This module measures them while the battery runs, so the settings can
be checked against the battery instead of against a data sheet. Nothing here
steers. The controller feeds it and the sensors read it.

Measured, per battery (v1.6.3):

* **Charge and discharge efficiency**, per block of charging or discharging of
  at least 1 kWh, weighted by energy: AC from the grid against DC into the
  cells, DC out of the cells against AC to the house. A single reading at
  one moment, as the older Charge and Discharge efficiency sensors give,
  swings with the power; a whole block does not.
* **Round trip (AC and DC)**, per cycle between two moments at the same SOC
  bound (full to full, or empty to empty) with at least 2 kWh each way. The
  AC figure is what a cycle really costs, idle draw included: on the
  reference installation 86.2 % against 90.0 % for the two block efficiencies
  multiplied. The DC figure is what the cells and the battery management
  keep; the gap between the two is the converter.
* **Usable capacity**, the DC energy from the upper to the lower bound in one
  discharge, without charging in between.
* **Full cycles**, the DC energy discharged divided by the nominal capacity.
* **Standby draw**, the median DC draw while both limits are zero and the
  battery rests, over the last 24 hours, weighted by time.
* **Response time**, from a limit write of at least 200 W until the grid has
  moved by 90 % of that step, on the meter. The battery's own reading is
  polled every ten seconds and would measure the poll; without a meter it is
  the fallback.
* **Grid tracking error**, the median distance between the grid and the
  buffer while a smart mode balances, over the last 24 hours, weighted by
  time. Moments the battery cannot do more (at its ceiling, or with the limit
  at zero and the grid asking for less) are left out: that is the house
  outrunning the battery, not the loop.
* **Realized margin**, per day: what discharging brought in minus what
  charging cost, at the prices of the moment, with the change in stored
  energy valued at the day's average import price after discharge loss. Wear
  is not deducted; it is an attribute. Only with a price control entry, which
  supplies the prices.

Everything is integrated from power with the trapezoid rule, skipping gaps
longer than a minute. Pure Python, so it can be replayed on a trace.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from statistics import median
from typing import Any

# ── Thresholds ───────────────────────────────────────────────────────────

GAP_SECONDS = 60            # a longer gap between readings is not bridged
BLOCK_POWER_W = 50          # charging or discharging, as opposed to resting
BLOCK_IDLE_END = 120        # seconds without either before a block closes
BLOCK_MIN_KWH = 1.0         # AC, for a block to count
CYCLE_MIN_KWH = 2.0         # AC each way, for a cycle to count
CYCLE_MAX_HOURS = 72
CAPACITY_MIN_KWH = 1.0      # DC out, for a capacity measurement to count
CAPACITY_MAX_CHARGE = 0.1   # DC in during that discharge, as a share of DC out
EFFICIENCY_RANGE = (0.5, 1.0)
KEEP = 5                    # blocks, cycles and capacities kept per kind

REST_AC_W = 10              # AC below this with both limits zero is resting
REST_SETTLE = 30            # seconds of rest before the draw counts (see README)
STANDBY_ALERT_W = 15        # median above this: closed backup mode is not in effect
STANDBY_CLEAR_W = 12
STANDBY_ALERT_SECONDS = 3600  # of rest in the window before the alert may fire

RESPONSE_MIN_STEP_W = 200
RESPONSE_SHARE = 0.9
RESPONSE_TIMEOUT = 60
RESPONSE_KEEP = 20

TRACKING_MAX_WEIGHT = 10    # seconds one meter sample may stand for
HISTOGRAM_HOURS = 24
HISTOGRAM_TOP = 2000        # W; larger values go in the top bin


def _kwh(power_w: float, seconds: float) -> float:
    return power_w * seconds / 3_600_000.0


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _parse(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


# ── Time-weighted histogram ──────────────────────────────────────────────


class TimeHistogram:
    """Seconds spent per whole watt, in hourly buckets, over a rolling window.

    Bounded in memory whatever the sample rate, and a median by time rather
    than by sample count, so a one-second meter and a ten-second one give the
    same answer.
    """

    def __init__(self, hours: int = HISTOGRAM_HOURS) -> None:
        self.hours = hours
        self.buckets: dict[int, dict[int, float]] = {}

    @staticmethod
    def _hour(now: datetime) -> int:
        return int(now.timestamp() // 3600)

    def add(self, now: datetime, value: float, seconds: float) -> None:
        if seconds <= 0:
            return
        key = min(HISTOGRAM_TOP, max(0, round(abs(value))))
        bucket = self.buckets.setdefault(self._hour(now), {})
        bucket[key] = bucket.get(key, 0.0) + seconds
        self.prune(now)

    def prune(self, now: datetime) -> None:
        oldest = self._hour(now) - self.hours + 1
        for hour in [h for h in self.buckets if h < oldest]:
            del self.buckets[hour]

    def merged(self) -> dict[int, float]:
        out: dict[int, float] = {}
        for bucket in self.buckets.values():
            for key, seconds in bucket.items():
                out[key] = out.get(key, 0.0) + seconds
        return out

    def seconds(self) -> float:
        return sum(self.merged().values())

    def quantile(self, share: float) -> int | None:
        merged = self.merged()
        total = sum(merged.values())
        if total <= 0:
            return None
        running = 0.0
        for key in sorted(merged):
            running += merged[key]
            if running >= share * total:
                return key
        return max(merged)

    def to_dict(self) -> dict[str, Any]:
        return {str(h): {str(k): round(v, 1) for k, v in b.items()} for h, b in self.buckets.items()}

    def load(self, data: Any) -> None:
        self.buckets = {}
        if not isinstance(data, dict):
            return
        for hour, bucket in data.items():
            try:
                self.buckets[int(hour)] = {int(k): float(v) for k, v in bucket.items()}
            except (TypeError, ValueError, AttributeError):
                continue


# ── Blocks ───────────────────────────────────────────────────────────────


@dataclass
class _Block:
    direction: str          # "charge" or "discharge"
    start: datetime
    last_active: datetime
    ac_kwh: float = 0.0
    dc_kwh: float = 0.0
    seconds: float = 0.0


@dataclass
class Prices:
    """What price control knows at this moment, per kWh in its currency."""

    import_price: float
    export_value: float
    eta_discharge: float = 0.95
    wear: float = 0.0


@dataclass
class MarginDay:
    """One day of the battery's money."""

    day: str
    cost: float = 0.0
    revenue: float = 0.0
    from_grid_kwh: float = 0.0
    from_surplus_kwh: float = 0.0
    to_house_kwh: float = 0.0
    to_grid_kwh: float = 0.0
    dc_in_kwh: float = 0.0
    dc_out_kwh: float = 0.0
    priced_seconds: float = 0.0
    import_seconds: float = 0.0     # import price × seconds, for the average
    eta_discharge: float = 0.95
    wear: float = 0.0
    unpriced_kwh: float = 0.0       # AC moved while no price was known

    @property
    def average_import(self) -> float | None:
        return self.import_seconds / self.priced_seconds if self.priced_seconds else None

    @property
    def stock_kwh(self) -> float:
        return self.dc_in_kwh - self.dc_out_kwh

    @property
    def stock_value(self) -> float | None:
        price = self.average_import
        return None if price is None else self.stock_kwh * self.eta_discharge * price

    @property
    def result(self) -> float | None:
        if self.priced_seconds <= 0:
            return None
        return self.revenue - self.cost + (self.stock_value or 0.0)

    def summary(self) -> dict[str, Any]:
        result = self.result
        return {
            "day": self.day,
            "result": None if result is None else round(result, 2),
            "revenue": round(self.revenue, 2),
            "cost": round(self.cost, 2),
            "stock_change_kwh": round(self.stock_kwh, 2),
            "stock_value": None if self.stock_value is None else round(self.stock_value, 2),
            "charged_from_grid_kwh": round(self.from_grid_kwh, 2),
            "charged_from_surplus_kwh": round(self.from_surplus_kwh, 2),
            "discharged_to_house_kwh": round(self.to_house_kwh, 2),
            "discharged_to_grid_kwh": round(self.to_grid_kwh, 2),
            "average_import_price": None if self.average_import is None else round(self.average_import, 5),
            "wear": round(self.dc_out_kwh * self.wear, 2),
            "unpriced_kwh": round(self.unpriced_kwh, 2),
        }

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    @classmethod
    def from_dict(cls, data: Any) -> MarginDay | None:
        if not isinstance(data, dict) or "day" not in data:
            return None
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        try:
            return cls(**known)
        except TypeError:
            return None


# ── The measurement ──────────────────────────────────────────────────────


@dataclass
class Sample:
    """One battery poll, as the controller reads it.

    `ac_w` and `dc_w` are positive while discharging. `grid_w` is positive
    while importing, None without a meter.
    """

    now: datetime
    today: str
    ac_w: float
    dc_w: float | None
    soc: int | None
    soc_limit: int | None
    input_limit: int | None
    output_limit: int | None
    grid_w: float | None = None
    capacity_kwh: float | None = None
    soc_floor: float | None = None      # minSoc, percent
    soc_ceiling: float | None = None    # socSet, percent


@dataclass
class BatteryMeasure:
    """All measured diagnostics of one battery. Fed by the controller."""

    # Cumulative, kWh, since this version started measuring.
    ac_in: float = 0.0
    ac_out: float = 0.0
    dc_in: float = 0.0
    dc_out: float = 0.0
    since: str | None = None

    blocks: dict[str, deque] = field(
        default_factory=lambda: {"charge": deque(maxlen=KEEP), "discharge": deque(maxlen=KEEP)}
    )
    cycles: deque = field(default_factory=lambda: deque(maxlen=KEEP))
    capacities: deque = field(default_factory=lambda: deque(maxlen=KEEP))
    anchors: dict[str, dict[str, Any]] = field(default_factory=dict)
    capacity_kwh: float | None = None

    standby: TimeHistogram = field(default_factory=TimeHistogram)
    standby_alert: bool = False
    tracking: TimeHistogram = field(default_factory=TimeHistogram)
    responses: deque = field(default_factory=lambda: deque(maxlen=RESPONSE_KEEP))
    response_timeouts: int = 0

    margin: MarginDay | None = None
    margin_yesterday: dict[str, Any] | None = None
    price_source: Callable[[], Prices | None] | None = None

    # Transient: not stored.
    _last: Sample | None = None
    _block: _Block | None = None
    _bound: int | None = None
    _rest_since: datetime | None = None
    _pending: dict[str, Any] | None = None
    _last_grid_at: datetime | None = None

    # ── Battery polls ────────────────────────────────────────────────

    def battery_sample(self, s: Sample) -> None:
        """Fold one battery poll into every measurement."""
        if self.since is None:
            self.since = s.today
        if s.capacity_kwh:
            self.capacity_kwh = s.capacity_kwh
        last = self._last
        self._last = s
        seconds = (s.now - last.now).total_seconds() if last is not None else 0.0
        if last is not None and 0 < seconds <= GAP_SECONDS:
            self._integrate(last, s, seconds)
        self._standby(s, seconds if 0 < seconds <= GAP_SECONDS else 0.0)
        self._bounds(s)
        self._response(s)

    def _integrate(self, a: Sample, b: Sample, seconds: float) -> None:
        ac = (a.ac_w + b.ac_w) / 2
        ac_in = _kwh(max(0.0, -ac), seconds)
        ac_out = _kwh(max(0.0, ac), seconds)
        dc_in = dc_out = 0.0
        if a.dc_w is not None and b.dc_w is not None:
            dc = (a.dc_w + b.dc_w) / 2
            dc_in = _kwh(max(0.0, -dc), seconds)
            dc_out = _kwh(max(0.0, dc), seconds)
        self.ac_in += ac_in
        self.ac_out += ac_out
        self.dc_in += dc_in
        self.dc_out += dc_out
        self._blocks(b, ac_in, ac_out, dc_in, dc_out, seconds)
        self._margin(a, b, seconds, ac_in, ac_out, dc_in, dc_out)

    # ── Block efficiency ─────────────────────────────────────────────

    def _blocks(self, s: Sample, ac_in, ac_out, dc_in, dc_out, seconds) -> None:
        direction = (
            "charge" if s.ac_w < -BLOCK_POWER_W
            else "discharge" if s.ac_w > BLOCK_POWER_W
            else None
        )
        block = self._block
        if block is not None and (
            (direction is not None and direction != block.direction)
            or (direction is None and (s.now - block.last_active).total_seconds() > BLOCK_IDLE_END)
            or s.dc_w is None
        ):
            self._close_block(s.now)
            block = None
        if direction is not None and s.dc_w is not None:
            if block is None:
                block = self._block = _Block(direction, s.now, s.now)
            block.last_active = s.now
        if block is not None:
            if block.direction == "charge":
                block.ac_kwh += ac_in
                block.dc_kwh += dc_in
            else:
                block.ac_kwh += ac_out
                block.dc_kwh += dc_out
            if direction == block.direction:
                block.seconds += seconds

    def _close_block(self, now: datetime) -> None:
        block, self._block = self._block, None
        if block is None or block.ac_kwh < BLOCK_MIN_KWH or block.dc_kwh <= 0:
            return
        if block.direction == "charge":
            eff = block.dc_kwh / block.ac_kwh
        else:
            eff = block.ac_kwh / block.dc_kwh
        if not EFFICIENCY_RANGE[0] <= eff <= EFFICIENCY_RANGE[1]:
            return
        self.blocks[block.direction].append({
            "end": _iso(block.last_active),
            "ac_kwh": round(block.ac_kwh, 3),
            "dc_kwh": round(block.dc_kwh, 3),
            "efficiency": round(eff * 100, 1),
            "average_w": round(block.ac_kwh * 3_600_000 / block.seconds) if block.seconds else None,
        })

    def block_efficiency(self, direction: str) -> float | None:
        blocks = self.blocks[direction]
        ac = sum(b["ac_kwh"] for b in blocks)
        dc = sum(b["dc_kwh"] for b in blocks)
        if not ac or not dc:
            return None
        return round((dc / ac if direction == "charge" else ac / dc) * 100, 1)

    # ── Cycles and capacity, at the SOC bounds ───────────────────────

    def _snapshot(self, s: Sample) -> dict[str, Any]:
        return {
            "at": _iso(s.now), "soc": s.soc,
            "ac_in": self.ac_in, "ac_out": self.ac_out,
            "dc_in": self.dc_in, "dc_out": self.dc_out,
        }

    @staticmethod
    def _bound_of(s: Sample) -> str | None:
        """At a SOC bound and stopped there: 'full', 'empty' or None.

        The device flags its own bounds (socLimit), but a battery steered to
        its lower limit and stopped there by the controller, as the day plan
        does, never raises the flag. Reaching the limit and resting there
        counts as well.
        """
        if s.soc_limit == 1:
            return "full"
        if s.soc_limit == 2:
            return "empty"
        if s.soc is None or abs(s.ac_w) > BLOCK_POWER_W:
            return None
        if s.soc_ceiling is not None and s.soc >= s.soc_ceiling:
            return "full"
        if s.soc_floor is not None and s.soc <= s.soc_floor:
            return "empty"
        return None

    def _bounds(self, s: Sample) -> None:
        kind = self._bound_of(s)
        arrived = kind is not None and kind != self._bound
        self._bound = kind
        if not arrived or s.soc is None:
            return
        snap = self._snapshot(s)
        previous = self.anchors.get(kind)
        if previous is not None:
            self._cycle(kind, previous, snap)
        if kind == "empty":
            full = self.anchors.get("full")
            empty = self.anchors.get("empty")
            if full is not None and (empty is None or full["at"] > empty["at"]):
                self._capacity(full, snap)
        self.anchors[kind] = snap

    def _cycle(self, kind: str, a: dict[str, Any], b: dict[str, Any]) -> None:
        start, end = _parse(a["at"]), _parse(b["at"])
        if start is None or end is None or a["soc"] != b["soc"]:
            return
        if end - start > timedelta(hours=CYCLE_MAX_HOURS):
            return
        d = {k: b[k] - a[k] for k in ("ac_in", "ac_out", "dc_in", "dc_out")}
        if d["ac_in"] < CYCLE_MIN_KWH or d["ac_out"] < CYCLE_MIN_KWH or d["dc_in"] <= 0:
            return
        rt_ac = d["ac_out"] / d["ac_in"]
        rt_dc = d["dc_out"] / d["dc_in"]
        if not EFFICIENCY_RANGE[0] <= rt_ac <= EFFICIENCY_RANGE[1]:
            return
        self.cycles.append({
            "start": a["at"], "end": b["at"], "bound": kind, "soc": b["soc"],
            "ac_in_kwh": round(d["ac_in"], 3), "ac_out_kwh": round(d["ac_out"], 3),
            "dc_in_kwh": round(d["dc_in"], 3), "dc_out_kwh": round(d["dc_out"], 3),
            "round_trip_ac": round(rt_ac * 100, 1), "round_trip_dc": round(rt_dc * 100, 1),
        })

    def round_trip(self, side: str) -> float | None:
        if not self.cycles:
            return None
        out = sum(c[f"{side}_out_kwh"] for c in self.cycles)
        into = sum(c[f"{side}_in_kwh"] for c in self.cycles)
        return round(out / into * 100, 1) if into else None

    def _capacity(self, full: dict[str, Any], empty: dict[str, Any]) -> None:
        out = empty["dc_out"] - full["dc_out"]
        into = empty["dc_in"] - full["dc_in"]
        if out < CAPACITY_MIN_KWH or into > CAPACITY_MAX_CHARGE * out:
            return
        self.capacities.append({
            "end": empty["at"], "kwh": round(out - into, 3),
            "from_soc": full["soc"], "to_soc": empty["soc"],
        })

    @property
    def usable_capacity(self) -> float | None:
        return self.capacities[-1]["kwh"] if self.capacities else None

    @property
    def full_cycles(self) -> float | None:
        if not self.capacity_kwh:
            return None
        return round(self.dc_out / self.capacity_kwh, 2)

    # ── Standby draw ─────────────────────────────────────────────────

    def _standby(self, s: Sample, seconds: float) -> None:
        resting = (
            s.input_limit == 0 and s.output_limit == 0
            and abs(s.ac_w) < REST_AC_W and s.dc_w is not None
        )
        if not resting:
            self._rest_since = None
            return
        if self._rest_since is None:
            self._rest_since = s.now
            return
        if (s.now - self._rest_since).total_seconds() >= REST_SETTLE and seconds:
            self.standby.add(s.now, s.dc_w, seconds)
        median_w = self.standby_draw
        if median_w is None or self.standby.seconds() < STANDBY_ALERT_SECONDS:
            self.standby_alert = False
        elif median_w > STANDBY_ALERT_W:
            self.standby_alert = True
        elif median_w < STANDBY_CLEAR_W:
            self.standby_alert = False

    @property
    def standby_draw(self) -> int | None:
        return self.standby.quantile(0.5)

    # ── Response time ────────────────────────────────────────────────

    def on_write(self, now: datetime, key: str, value: int, grid_w: float | None) -> None:
        """A limit write by the controller: start timing when it is a real step."""
        if key not in ("inputLimit", "outputLimit") or self._last is None:
            return
        target = value if key == "outputLimit" else -value
        if value == 0 and self._pending is not None and (
            (key == "inputLimit") == (self._pending["target"] > 0)
        ):
            # Clearing the opposite direction on the way to a new one.
            return
        start = self._last.ac_w
        if abs(target - start) >= RESPONSE_MIN_STEP_W:
            self._pending = {"at": now, "start": start, "target": target, "grid": grid_w}
        elif self._pending is not None:
            self._pending["target"] = target

    def _settle(self, now: datetime, share: float) -> None:
        pending = self._pending
        if pending is None:
            return
        elapsed = (now - pending["at"]).total_seconds()
        if share >= RESPONSE_SHARE:
            kind = "stop" if abs(pending["target"]) < abs(pending["start"]) else "start"
            self.responses.append({"s": round(elapsed, 1), "kind": kind})
            self._pending = None
        elif elapsed > RESPONSE_TIMEOUT:
            self.response_timeouts += 1
            self._pending = None

    def _response(self, s: Sample) -> None:
        """Battery poll: drop a step the device refuses; time it here without a meter."""
        pending = self._pending
        if pending is None:
            return
        step = pending["target"] - pending["start"]
        if (
            abs(step) < RESPONSE_MIN_STEP_W
            or (pending["target"] < 0 and s.soc_limit == 1)
            or (pending["target"] > 0 and s.soc_limit == 2)
        ):
            self._pending = None   # no real step, or the device closed that direction
            return
        if pending["grid"] is None:
            self._settle(s.now, (s.ac_w - pending["start"]) / step)

    def _response_grid(self, now: datetime, grid_w: float) -> None:
        pending = self._pending
        if pending is None or pending["grid"] is None:
            return
        # More discharge lowers the grid, more charge raises it.
        expected = -(pending["target"] - pending["start"])
        if abs(expected) < RESPONSE_MIN_STEP_W:
            self._pending = None
            return
        self._settle(now, (grid_w - pending["grid"]) / expected)

    def response_median(self, kind: str | None = None) -> float | None:
        values = [r["s"] for r in self.responses if kind is None or r["kind"] == kind]
        return round(median(values), 1) if values else None

    @property
    def response_time(self) -> float | None:
        return self.response_median()

    # ── Grid tracking ────────────────────────────────────────────────

    def grid_sample(self, now: datetime, grid_w: float | None, error_w: float | None) -> None:
        """One meter sample: the grid, and its distance from the buffer while
        balancing (None otherwise)."""
        if grid_w is not None:
            self._response_grid(now, grid_w)
        last, self._last_grid_at = self._last_grid_at, now
        if error_w is None or last is None:
            return
        seconds = min(TRACKING_MAX_WEIGHT, (now - last).total_seconds())
        self.tracking.add(now, error_w, seconds)

    @property
    def tracking_error(self) -> int | None:
        return self.tracking.quantile(0.5)

    # ── Realized margin ──────────────────────────────────────────────

    def _margin(self, a: Sample, b: Sample, seconds, ac_in, ac_out, dc_in, dc_out) -> None:
        day = self.margin
        if day is None or day.day != b.today:
            if day is not None:
                self.margin_yesterday = day.summary()
            day = self.margin = MarginDay(b.today)
        day.dc_in_kwh += dc_in
        day.dc_out_kwh += dc_out
        prices = self.price_source() if self.price_source is not None else None
        if prices is None:
            day.unpriced_kwh += ac_in + ac_out
            return
        day.eta_discharge = prices.eta_discharge
        day.wear = prices.wear
        day.priced_seconds += seconds
        day.import_seconds += prices.import_price * seconds
        grid = None
        if a.grid_w is not None and b.grid_w is not None:
            grid = (a.grid_w + b.grid_w) / 2
        if ac_in:
            # Charging: the part the grid supplied cost the import price; the
            # rest was surplus that would have been sold.
            power = ac_in * 3_600_000 / seconds
            from_grid = power if grid is None else min(power, max(0.0, grid))
            share = from_grid / power if power else 1.0
            day.from_grid_kwh += ac_in * share
            day.from_surplus_kwh += ac_in * (1 - share)
            day.cost += ac_in * (share * prices.import_price + (1 - share) * prices.export_value)
        if ac_out:
            # Discharging: the part the house used avoided import; the part
            # that went to the grid was sold.
            power = ac_out * 3_600_000 / seconds
            to_grid = 0.0 if grid is None else min(power, max(0.0, -grid))
            share = to_grid / power if power else 0.0
            day.to_grid_kwh += ac_out * share
            day.to_house_kwh += ac_out * (1 - share)
            day.revenue += ac_out * (share * prices.export_value + (1 - share) * prices.import_price)

    @property
    def realized_margin(self) -> float | None:
        result = self.margin.result if self.margin is not None else None
        return None if result is None else round(result, 2) + 0.0   # no -0.0

    # ── Storage ──────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            "ac_in": self.ac_in, "ac_out": self.ac_out,
            "dc_in": self.dc_in, "dc_out": self.dc_out,
            "since": self.since,
            "blocks": {k: list(v) for k, v in self.blocks.items()},
            "cycles": list(self.cycles),
            "capacities": list(self.capacities),
            "anchors": self.anchors,
            "capacity_kwh": self.capacity_kwh,
            "standby": self.standby.to_dict(),
            "standby_alert": self.standby_alert,
            "tracking": self.tracking.to_dict(),
            "responses": list(self.responses),
            "response_timeouts": self.response_timeouts,
            "margin": self.margin.to_dict() if self.margin else None,
            "margin_yesterday": self.margin_yesterday,
        }

    def load(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        for key in ("ac_in", "ac_out", "dc_in", "dc_out"):
            try:
                setattr(self, key, float(data.get(key) or 0.0))
            except (TypeError, ValueError):
                pass
        self.since = data.get("since") if isinstance(data.get("since"), str) else None
        blocks = data.get("blocks") or {}
        for kind in ("charge", "discharge"):
            self.blocks[kind] = deque(_dicts(blocks.get(kind)), maxlen=KEEP)
        self.cycles = deque(_dicts(data.get("cycles")), maxlen=KEEP)
        self.capacities = deque(_dicts(data.get("capacities")), maxlen=KEEP)
        anchors = data.get("anchors")
        self.anchors = {k: v for k, v in anchors.items() if isinstance(v, dict)} if isinstance(anchors, dict) else {}
        try:
            self.capacity_kwh = float(data["capacity_kwh"]) if data.get("capacity_kwh") else None
        except (TypeError, ValueError):
            self.capacity_kwh = None
        self.standby.load(data.get("standby"))
        self.standby_alert = bool(data.get("standby_alert"))
        self.tracking.load(data.get("tracking"))
        responses = [
            r for r in data.get("responses") or []
            if isinstance(r, dict) and isinstance(r.get("s"), (int, float))
        ]
        self.responses = deque(responses, maxlen=RESPONSE_KEEP)
        try:
            self.response_timeouts = int(data.get("response_timeouts") or 0)
        except (TypeError, ValueError):
            self.response_timeouts = 0
        self.margin = MarginDay.from_dict(data.get("margin"))
        yesterday = data.get("margin_yesterday")
        self.margin_yesterday = yesterday if isinstance(yesterday, dict) else None

    def diagnostics(self) -> dict[str, Any]:
        """The figures and their basis, for the diagnostics download."""
        return {
            "charge_efficiency": self.block_efficiency("charge"),
            "discharge_efficiency": self.block_efficiency("discharge"),
            "round_trip_ac": self.round_trip("ac"),
            "round_trip_dc": self.round_trip("dc"),
            "usable_capacity_kwh": self.usable_capacity,
            "full_cycles": self.full_cycles,
            "standby_draw_w": self.standby_draw,
            "standby_rest_hours": round(self.standby.seconds() / 3600, 1),
            "response_time_s": self.response_time,
            "response_start_s": self.response_median("start"),
            "response_stop_s": self.response_median("stop"),
            "response_timeouts": self.response_timeouts,
            "grid_tracking_error_w": self.tracking_error,
            "grid_tracking_p90_w": self.tracking.quantile(0.9),
            "grid_tracking_hours": round(self.tracking.seconds() / 3600, 1),
            "realized_margin": self.margin.summary() if self.margin else None,
            "realized_margin_yesterday": self.margin_yesterday,
            "totals_kwh": {
                "ac_in": round(self.ac_in, 3), "ac_out": round(self.ac_out, 3),
                "dc_in": round(self.dc_in, 3), "dc_out": round(self.dc_out, 3),
                "since": self.since,
            },
            "blocks": {k: list(v) for k, v in self.blocks.items()},
            "cycles": list(self.cycles),
            "capacities": list(self.capacities),
        }


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def start_of_day(now_local: datetime) -> datetime:
    """Local midnight, for the last_reset of the realized margin."""
    return now_local.replace(hour=0, minute=0, second=0, microsecond=0)


UTC = timezone.utc
