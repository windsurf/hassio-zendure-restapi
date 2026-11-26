"""Profiles learned from the battery's own use (v1.8.7): measured, never steering.

* **Efficiency curve.** The AC and DC energy per band of AC power, apart for
  charging and discharging, a power counting at the nearest band edge, only
  over stable stretches: AC and DC power run
  5 to 10 s apart after a change, so a sample counts once the AC power has
  stayed within a few percent for STABLE_S seconds. Per band the spot
  efficiency (DC/AC charging, AC/DC discharging); over the bands a fit of the
  loss, a + b·P + c·P² per direction; and the curve corrected by the measured
  round trip of whole cycles, which lies a few points under the product of the
  spot figures. A fit needs bands that differ in power (FIT_SPAN_LINEAR,
  FIT_SPAN_QUADRATIC), and the stages stay within the bands measured: no
  figure is shown for a power that was never run. The corrected curve, as a list of power and efficiency, is
  what a planner that takes stages per power can use as it is.
* **House load profile.** The house load (grid plus battery) per quarter of
  the day, workdays and weekends apart: the average, for energy, and the low
  quartile within the quarter, for the base load. Plus, per day, the load the
  battery could cover: stretches above the stop threshold that lasted at
  least *Start after*; and the load it did cover. The load expected now comes
  from the quarter itself, else the same quarter of the other kind of day,
  else the last quarter filled in the two hours before.
* **Usage profile.** Charged and discharged AC energy per hour of the day.

Older days count less: every sum fades with a half-life of HALF_LIFE_DAYS
days, so a season, a new appliance or a firmware shows within weeks. Pure
Python, so it can be replayed on a trace.
"""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ── Shared ───────────────────────────────────────────────────────────────

GAP_SECONDS = 60            # a longer gap between readings is not bridged
HALF_LIFE_DAYS = 30.0


def _fade(value: float, days: float) -> float:
    return value * 0.5 ** (days / HALF_LIFE_DAYS) if days > 0 else value


def _day_number(moment: datetime) -> int:
    return moment.date().toordinal()


# ── Efficiency curve ─────────────────────────────────────────────────────

# The AC power bands, W: a power counts at the nearest edge, so a battery
# charging at its maximum around 3000 W stays in one band. Under the lowest edge
# nothing counts; above the highest, the highest.
BAND_EDGES = (50, 100, 150, 200, 250, 300, 400, 500, 750, 1000, 1500, 2000, 2500, 3000)
BANDS_VERSION = 2           # 1: lower edges; the sums of version 1 are dropped once
FIT_SPAN_LINEAR = 2.0       # highest / lowest band power before a + b·P is fitted
FIT_SPAN_QUADRATIC = 4.0    # ... and before c·P² is, with three bands or more
BAND_MIN_KWH = 0.2          # AC in a band before its efficiency is shown
STABLE_S = 15.0             # the AC power held this long before a sample counts
STABLE_SHARE = 0.05         # within this share of itself ...
STABLE_MIN_W = 20.0         # ... or this many watts, whichever is larger
EFFICIENCY_RANGE = (0.3, 1.0)


def band_of(power_w: float) -> int | None:
    """The band index of an AC power: the nearest edge; None under the lowest edge."""
    power = abs(power_w)
    if power < BAND_EDGES[0]:
        return None
    return min(range(len(BAND_EDGES)), key=lambda i: (abs(power - BAND_EDGES[i]), -i))


def _solve(matrix: list[list[float]], vector: list[float]) -> list[float] | None:
    """Gaussian elimination for a small system; None when it is singular."""
    n = len(vector)
    a = [row[:] + [vector[i]] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            return None
        a[col], a[pivot] = a[pivot], a[col]
        for r in range(n):
            if r != col:
                factor = a[r][col] / a[col][col]
                for c in range(col, n + 1):
                    a[r][c] -= factor * a[col][c]
    return [a[i][n] / a[i][i] for i in range(n)]


def fit_loss(points: list[tuple[float, float, float]]) -> dict[str, float] | None:
    """Least squares of loss = a + b·P + c·P², weighted, over (P, loss, weight).

    The points must differ in power: a + b·P once the highest power is
    FIT_SPAN_LINEAR times the lowest, the c·P² term too from three points and
    FIT_SPAN_QUADRATIC; closer points say nothing about the shape, so no fit.
    """
    points = [p for p in points if p[2] > 0 and p[0] > 0]
    if len(points) < 2:
        return None
    span = max(p[0] for p in points) / min(p[0] for p in points)
    terms = 3 if len(points) >= 3 and span >= FIT_SPAN_QUADRATIC else 2 if span >= FIT_SPAN_LINEAR else 0
    if not terms:
        return None
    matrix = [[0.0] * terms for _ in range(terms)]
    vector = [0.0] * terms
    for power, loss, weight in points:
        row = [power ** k for k in range(terms)]
        for i in range(terms):
            vector[i] += weight * row[i] * loss
            for j in range(terms):
                matrix[i][j] += weight * row[i] * row[j]
    solution = _solve(matrix, vector)
    if solution is None:
        return None
    a, b = solution[0], solution[1]
    c = solution[2] if terms == 3 else 0.0
    return {"a": round(a, 2), "b": round(b, 5), "c": round(c, 9)}


def _efficiency_from_fit(fit: dict[str, float], power: float, direction: str) -> float | None:
    loss = fit["a"] + fit["b"] * power + fit["c"] * power * power
    if power <= 0:
        return None
    eff = (power - loss) / power if direction == "charge" else power / (power + loss)
    return eff if EFFICIENCY_RANGE[0] <= eff <= EFFICIENCY_RANGE[1] else None


@dataclass
class EfficiencyCurve:
    """Spot efficiency per band of AC power, a fit of the loss, and the curve
    corrected by the round trip of whole cycles."""

    # direction -> band -> [ac_kwh, dc_kwh, seconds]
    bands: dict[str, dict[int, list[float]]] = field(
        default_factory=lambda: {"charge": {}, "discharge": {}}
    )
    day: int | None = None

    # Transient: the last sample and the recent AC powers.
    _last: tuple[datetime, float, float | None] | None = None
    _recent: deque = field(default_factory=lambda: deque(maxlen=64))

    def _age(self, now: datetime) -> None:
        today = _day_number(now)
        if self.day is not None and today > self.day:
            days = today - self.day
            for direction in self.bands.values():
                for sums in direction.values():
                    sums[0] = _fade(sums[0], days)
                    sums[1] = _fade(sums[1], days)
                    sums[2] = _fade(sums[2], days)
        self.day = today

    def _stable(self, now: datetime, ac_w: float) -> bool:
        """The AC power has stayed close to this value for STABLE_S seconds."""
        tolerance = max(STABLE_MIN_W, STABLE_SHARE * abs(ac_w))
        oldest = None
        for moment, power in self._recent:
            if (now - moment).total_seconds() > STABLE_S + GAP_SECONDS:
                continue
            if abs(power - ac_w) > tolerance:
                oldest = None
                continue
            oldest = moment if oldest is None else oldest
        self._recent.append((now, ac_w))
        return oldest is not None and (now - oldest).total_seconds() >= STABLE_S

    def add(self, now: datetime, ac_w: float, dc_w: float | None) -> None:
        """One battery poll: AC and DC power, positive while discharging."""
        self._age(now)
        stable = self._stable(now, ac_w)
        last, self._last = self._last, (now, ac_w, dc_w)
        if last is None or dc_w is None or last[2] is None or not stable:
            return
        seconds = (now - last[0]).total_seconds()
        if not 0 < seconds <= GAP_SECONDS:
            return
        ac = (last[1] + ac_w) / 2
        dc = (last[2] + dc_w) / 2
        band = band_of(ac)
        if band is None or ac * dc <= 0:
            return
        direction = "discharge" if ac > 0 else "charge"
        sums = self.bands[direction].setdefault(band, [0.0, 0.0, 0.0])
        sums[0] += abs(ac) * seconds / 3_600_000
        sums[1] += abs(dc) * seconds / 3_600_000
        sums[2] += seconds

    # ── Reading ──────────────────────────────────────────────────────

    def spot(self, direction: str) -> list[dict[str, Any]]:
        """Per band with enough energy: average AC power, efficiency, kWh."""
        out = []
        for band in sorted(self.bands[direction]):
            ac, dc, seconds = self.bands[direction][band]
            if ac < BAND_MIN_KWH or dc <= 0 or seconds <= 0:
                continue
            eff = dc / ac if direction == "charge" else ac / dc
            if not EFFICIENCY_RANGE[0] <= eff <= EFFICIENCY_RANGE[1]:
                continue
            out.append({
                "band_w": BAND_EDGES[band],
                "power_w": round(ac * 3_600_000 / seconds),
                "efficiency": round(eff * 100, 1),
                "kwh": round(ac, 2),
            })
        return out

    def fit(self, direction: str) -> dict[str, float] | None:
        """The loss in W as a + b·P + c·P² over the bands, weighted by energy."""
        points = []
        for row in self.spot(direction):
            power, eff = row["power_w"], row["efficiency"] / 100
            loss = power * (1 - eff) if direction == "charge" else power * (1 / eff - 1)
            points.append((power, loss, row["kwh"]))
        return fit_loss(points)

    def efficiency_at(self, direction: str, power_w: float) -> float | None:
        """Spot efficiency at a power, from the fit (0..1)."""
        fit = self.fit(direction)
        return None if fit is None else _efficiency_from_fit(fit, abs(power_w), direction)

    def correction(self, cycles: list[dict[str, Any]], charge_w: float | None,
                   discharge_w: float | None) -> float | None:
        """Factor per side that brings the spot curve to the measured round trip.

        The round trip of whole cycles includes what spot figures miss (idle
        draw, transients, the ends of the SOC range). Its ratio to the product
        of the spot efficiencies at the cycle's average powers is shared out
        equally over charging and discharging: the square root per side.
        """
        if not cycles or not charge_w or not discharge_w:
            return None
        eta_c = self.efficiency_at("charge", charge_w)
        eta_d = self.efficiency_at("discharge", discharge_w)
        if not eta_c or not eta_d:
            return None
        ac_in = sum(c.get("ac_in_kwh", 0.0) for c in cycles)
        ac_out = sum(c.get("ac_out_kwh", 0.0) for c in cycles)
        if ac_in <= 0:
            return None
        ratio = (ac_out / ac_in) / (eta_c * eta_d)
        if not 0.5 <= ratio <= 1.05:
            return None
        return math.sqrt(ratio)

    def curve(self, direction: str, factor: float | None) -> list[list[float]]:
        """Stages of [power W, efficiency 0..1] at the band edges from 250 W, within the
        bands measured, corrected by the round trip when there is a factor; the
        format a stage-based planner reads."""
        fit = self.fit(direction)
        if fit is None:
            return []
        measured = [row["band_w"] for row in self.spot(direction)]
        low, high = min(measured), max(measured)
        stages = []
        for edge in BAND_EDGES:
            if edge < 250 or not low <= edge <= high:
                continue
            eff = _efficiency_from_fit(fit, float(edge), direction)
            if eff is None:
                continue
            if factor:
                eff = min(1.0, eff * factor)
            stages.append([edge, round(eff, 3)])
        return stages

    def attributes(self, direction: str, cycles: list[dict[str, Any]], charge_w: float | None,
                   discharge_w: float | None) -> dict[str, Any]:
        factor = self.correction(cycles, charge_w, discharge_w)
        return {
            "curve": self.spot(direction),
            "loss_fit": self.fit(direction),
            "round_trip_factor": None if factor is None else round(factor, 4),
            "stages": self.curve(direction, factor),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "bands_version": BANDS_VERSION,
            "bands": {d: {str(b): [float(v) for v in s] for b, s in bands.items()} for d, bands in self.bands.items()},
        }

    def load(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self.day = data.get("day") if isinstance(data.get("day"), int) else None
        if data.get("bands_version") != BANDS_VERSION:
            return              # sums kept by other band edges: start again
        stored = data.get("bands")
        if not isinstance(stored, dict):
            return
        for direction in ("charge", "discharge"):
            bands = stored.get(direction)
            if not isinstance(bands, dict):
                continue
            for band, sums in bands.items():
                try:
                    index = int(band)
                    values = [float(v) for v in sums][:3]
                except (TypeError, ValueError):
                    continue
                if 0 <= index < len(BAND_EDGES) and len(values) == 3:
                    self.bands[direction][index] = values


# ── House load and usage per time of day ─────────────────────────────────

QUARTERS = 96
HOURS = 24
FALLBACK_QUARTERS = 8       # the expected load looks back two hours at most
LOW_SHARE = 0.25            # the low quartile within a quarter: the base load
PROFILE_WEIGHT = 1 / 7      # the share a new day gets in its slot


def _blend(old: float | None, new: float) -> float:
    return new if old is None else old + PROFILE_WEIGHT * (new - old)


@dataclass
class HouseProfile:
    """House load per quarter, workdays and weekends apart, and what the battery could cover."""

    average: dict[str, list[float | None]] = field(
        default_factory=lambda: {"workday": [None] * QUARTERS, "weekend": [None] * QUARTERS}
    )
    base: dict[str, list[float | None]] = field(
        default_factory=lambda: {"workday": [None] * QUARTERS, "weekend": [None] * QUARTERS}
    )
    days: int = 0
    today: dict[str, Any] = field(default_factory=dict)
    yesterday: dict[str, Any] | None = None

    # Transient.
    _quarter: tuple[str, int, str] | None = None     # (date, slot, kind)
    _samples: list[tuple[float, float]] = field(default_factory=list)   # (W, seconds)
    _last: datetime | None = None
    _run: list[float] = field(default_factory=lambda: [0.0, 0.0])        # seconds, kWh above the threshold

    def add(self, local: datetime, house_w: float, battery_w: float, threshold_w: float,
            min_s: float) -> None:
        """One meter sample in local time: the house load, the battery's AC power
        (positive discharging), the stop threshold and Start after of the battery."""
        last, self._last = self._last, local
        seconds = (local - last).total_seconds() if last is not None else 0.0
        if not 0 < seconds <= GAP_SECONDS:
            seconds = 0.0
        day = local.date().isoformat()
        if self.today.get("day") != day:
            self._close_run(min_s)
            if self.today.get("day"):
                self.yesterday = dict(self.today)
            self.today = {"day": day, "house_kwh": 0.0, "coverable_kwh": 0.0, "covered_kwh": 0.0}
        slot = (local.hour * 60 + local.minute) // 15
        kind = "weekend" if local.weekday() >= 5 else "workday"
        key = (day, slot, kind)
        if self._quarter != key:
            self._close_quarter()
            self._quarter = key
        house = max(0.0, house_w)
        if seconds:
            kwh = house * seconds / 3_600_000
            self._samples.append((house, seconds))
            self.today["house_kwh"] += kwh
            self.today["covered_kwh"] += min(house, max(0.0, battery_w)) * seconds / 3_600_000
            if house >= threshold_w:
                self._run[0] += seconds
                self._run[1] += kwh
            else:
                self._close_run(min_s)

    def _close_run(self, min_s: float) -> None:
        if self._run[0] >= min_s and self.today:
            self.today["coverable_kwh"] = self.today.get("coverable_kwh", 0.0) + self._run[1]
        self._run = [0.0, 0.0]

    def _close_quarter(self) -> None:
        quarter, samples = self._quarter, self._samples
        self._samples = []
        if quarter is None:
            return
        seconds = sum(s for _, s in samples)
        if seconds < 600:        # a quarter counts with ten minutes of readings
            return
        _, slot, kind = quarter
        average = sum(w * s for w, s in samples) / seconds
        running, low = 0.0, None
        for power, weight in sorted(samples):
            running += weight
            if running >= LOW_SHARE * seconds:
                low = power
                break
        self.average[kind][slot] = _blend(self.average[kind][slot], average)
        self.base[kind][slot] = _blend(self.base[kind][slot], low if low is not None else average)
        if slot == QUARTERS - 1:
            self.days += 1

    def lookup(self, local: datetime) -> tuple[float | None, str | None]:
        """The load expected now, W, and where it comes from: `profile` (this
        quarter), `other_days` (this quarter of the other kind of day) or
        `earlier_quarter` (the last quarter filled in the two hours before)."""
        slot = (local.hour * 60 + local.minute) // 15
        kind = "weekend" if local.weekday() >= 5 else "workday"
        other = "workday" if kind == "weekend" else "weekend"
        if self.average[kind][slot] is not None:
            return round(self.average[kind][slot]), "profile"
        if self.average[other][slot] is not None:
            return round(self.average[other][slot]), "other_days"
        for back in range(1, FALLBACK_QUARTERS + 1):
            value = self.average[kind][(slot - back) % QUARTERS]
            if value is not None:
                return round(value), "earlier_quarter"
        return None, None

    def expected(self, local: datetime) -> float | None:
        return self.lookup(local)[0]

    def attributes(self) -> dict[str, Any]:
        def rounded(values: list[float | None]) -> list[int | None]:
            return [None if v is None else round(v) for v in values]

        def day(values: dict[str, Any] | None) -> dict[str, Any] | None:
            if not values:
                return None
            return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in values.items()}

        return {
            "workday_w": rounded(self.average["workday"]),
            "weekend_w": rounded(self.average["weekend"]),
            "workday_base_w": rounded(self.base["workday"]),
            "weekend_base_w": rounded(self.base["weekend"]),
            "days": self.days,
            "today": day(self.today),
            "yesterday": day(self.yesterday),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "average": self.average, "base": self.base, "days": self.days,
            "today": self.today, "yesterday": self.yesterday,
        }

    def load(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        for name in ("average", "base"):
            stored = data.get(name)
            if isinstance(stored, dict):
                for kind in ("workday", "weekend"):
                    values = stored.get(kind)
                    if isinstance(values, list) and len(values) == QUARTERS:
                        getattr(self, name)[kind] = [
                            float(v) if isinstance(v, (int, float)) else None for v in values
                        ]
        self.days = int(data.get("days") or 0) if isinstance(data.get("days"), int) else 0
        self.today = data.get("today") if isinstance(data.get("today"), dict) else {}
        self.yesterday = data.get("yesterday") if isinstance(data.get("yesterday"), dict) else None


@dataclass
class UsageProfile:
    """Charged and discharged AC energy per hour of the day, fading with the days."""

    charge: list[float] = field(default_factory=lambda: [0.0] * HOURS)
    discharge: list[float] = field(default_factory=lambda: [0.0] * HOURS)
    day: int | None = None
    today: dict[str, float] = field(default_factory=lambda: {"charged_kwh": 0.0, "discharged_kwh": 0.0})

    def add(self, local: datetime, charged_kwh: float, discharged_kwh: float) -> None:
        number = _day_number(local)
        if self.day is not None and number > self.day:
            days = number - self.day
            self.charge = [_fade(v, days) for v in self.charge]
            self.discharge = [_fade(v, days) for v in self.discharge]
            self.today = {"charged_kwh": 0.0, "discharged_kwh": 0.0}
        self.day = number
        self.charge[local.hour] += charged_kwh
        self.discharge[local.hour] += discharged_kwh
        self.today["charged_kwh"] += charged_kwh
        self.today["discharged_kwh"] += discharged_kwh

    def shares(self, values: list[float]) -> list[float]:
        total = sum(values)
        return [round(v / total * 100, 1) if total else 0.0 for v in values]

    def attributes(self) -> dict[str, Any]:
        return {
            "charge_share_by_hour": self.shares(self.charge),
            "discharge_share_by_hour": self.shares(self.discharge),
            "charged_today_kwh": round(self.today["charged_kwh"], 2),
            "discharged_today_kwh": round(self.today["discharged_kwh"], 2),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "charge": [round(v, 4) for v in self.charge],
            "discharge": [round(v, 4) for v in self.discharge],
            "day": self.day, "today": self.today,
        }

    def load(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        for name in ("charge", "discharge"):
            values = data.get(name)
            if isinstance(values, list) and len(values) == HOURS:
                setattr(self, name, [float(v) if isinstance(v, (int, float)) else 0.0 for v in values])
        self.day = data.get("day") if isinstance(data.get("day"), int) else None
        today = data.get("today")
        if isinstance(today, dict):
            self.today = {
                "charged_kwh": float(today.get("charged_kwh") or 0.0),
                "discharged_kwh": float(today.get("discharged_kwh") or 0.0),
            }


def sum_usage(profiles: list[UsageProfile]) -> dict[str, Any]:
    """A cluster's usage: the members' hours added up."""
    total = UsageProfile()
    for profile in profiles:
        total.charge = [a + b for a, b in zip(total.charge, profile.charge)]
        total.discharge = [a + b for a, b in zip(total.discharge, profile.discharge)]
        total.today["charged_kwh"] += profile.today["charged_kwh"]
        total.today["discharged_kwh"] += profile.today["discharged_kwh"]
    return total.attributes()

