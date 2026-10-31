"""The decisions of the battery cluster, without Home Assistant (v1.7.0).

One primary per group runs the loop that has been measured and tuned on a
single battery, unchanged, on the grid of the whole connection. The other
batteries are secondary: each holds a fixed limit in the primary's direction, set here
at most every `redistribute_s` seconds, and writes nothing else. To the primary
a secondary's step is a step in the house load, which its loop already handles
every day.

Why not one loop over all batteries: every loop step would then reach every
battery, the slowest one sets the pace, and the hold, the start factor and the
outstanding-order bookkeeping would have to be derived and measured again for
a cluster. Zendure's own integration does it that way; this keeps the measured
loop and moves only fixed blocks around it.

Everything here is a pure function of the readings and the clock, so it is
tested without Home Assistant (tests/test_distribute.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .contract import (
    CHARGE,
    DISCHARGE,
    NONE,
    QUICK_CHARGE,
    QUICK_DISCHARGE,
    ROLE_SECONDARY,
    ROLE_PRIMARY,
    ROLE_QUICK,
    ROLE_STANDBY,
    SMART,
    STANDBY,
    GroupSettings,
    Reading,
    allowed,
)

# The secondaries are set so the primary runs at this share of its room, which
# leaves it margin to trim both ways between redistributions.
PRIMARY_SHARE = 0.6
# Secondary limits are rounded to this, and a secondary is only written again
# when its limit moves at least STEP_MIN (to or from zero always counts).
ROUND_W = 50
STEP_MIN = 100
# Below Concentrate below the primary works alone; the secondaries come back
# only once the demand is past it again, and are released below this share
# of it. One inverter converting costs a fixed ~40 W, so two running at a
# few hundred watts throw that away twice.
CONCENTRATE_RELEASE = 0.8
# The primary at its cap, or at nothing while secondaries run, for this long:
# redistribute now rather than at the interval.
QUICK_REACT_S = 10.0
# Never redistribute sooner than this after the last time: a secondary takes
# 6 to 15 s to start from standstill, and a second redistribution inside that
# would act on its ramp.
MIN_GAP_S = 15.0
# The primary is "at its cap" within this of it, and "at nothing" below this.
CAP_MARGIN_W = 50


@dataclass
class Role:
    role: str
    direction: str = NONE
    limit: int = 0


@dataclass
class Decision:
    roles: dict[str, Role]
    primary: str | None
    status: str
    reason: str
    # Batteries whose role or limit changed, in the order to write them.
    changed: list[str] = field(default_factory=list)
    attributes: dict[str, Any] = field(default_factory=dict)


def _round(value: float) -> int:
    return int(round(value / ROUND_W) * ROUND_W)


def split(total: float, rooms: dict[str, int]) -> dict[str, int]:
    """`total` over the batteries in proportion to their room, never past it.

    What one battery cannot take (its share above its room) goes to the
    others, so the sum is min(total, sum of rooms) before rounding.
    """
    shares = {key: 0.0 for key in rooms}
    left = max(0.0, float(total))
    open_ = {key: room for key, room in rooms.items() if room > 0}
    while left > 0.5 and open_:
        weight = sum(open_.values())
        spill = 0.0
        for key, room in list(open_.items()):
            share = left * room / weight
            free = room - shares[key]
            if share >= free:
                shares[key] = float(room)
                spill += share - free
                del open_[key]
            else:
                shares[key] += share
        left = spill
    return {key: min(rooms[key], _round(value)) for key, value in shares.items()}


class Distributor:
    """The cluster's memory between two decisions, and the decision itself."""

    def __init__(self) -> None:
        self.primary: str | None = None
        self.roles: dict[str, Role] = {}
        self.concentrated = True
        self.last_split: float | None = None
        self.at_cap_since: float | None = None
        self.idle_since: float | None = None
        self.demand: float = 0.0

    # ── Primary ───────────────────────────────────────────────────────────

    @staticmethod
    def _rank(r: Reading, direction: str) -> tuple:
        speed = r.response_s if r.response_s is not None else 1e9
        room = r.room(direction) if direction != NONE else max(r.room(CHARGE), r.room(DISCHARGE))
        # One that already runs the wanted way is not stopped to make another one primary.
        running = direction != NONE and r.direction == direction
        return (-r.priority, not running, speed, -room, r.key)

    def _wanted(self, readings: list[Reading], s: GroupSettings, grid: float | None) -> str:
        """The direction the group needs now, as far as the grid says."""
        primary = next((r for r in readings if r.key == self.primary), None)
        if primary is not None and primary.direction != NONE:
            return primary.direction
        if grid is None:
            return NONE
        if grid > s.start_discharge_at and allowed(s.mode, DISCHARGE):
            return DISCHARGE
        if grid < -s.start_charge_at and allowed(s.mode, CHARGE):
            return CHARGE
        return NONE

    def _choose_primary(self, active: list[Reading], wanted: str) -> str | None:
        if not active:
            return None
        current = next((r for r in active if r.key == self.primary), None)
        if current is not None:
            # Kept unless it cannot serve what is wanted and another can: a
            # change of primary costs a stop and a cold start.
            if wanted == NONE or not current.closed(wanted):
                return current.key
            if not any(r.room(wanted) > 0 for r in active if r.key != current.key):
                return current.key
        candidates = [r for r in active if wanted == NONE or r.room(wanted) > 0] or active
        return min(candidates, key=lambda r: self._rank(r, wanted)).key

    # ── Decision ─────────────────────────────────────────────────────────

    def decide(
        self,
        readings: list[Reading],
        s: GroupSettings,
        grid: float | None,
        now: float,
    ) -> Decision:
        active = [r for r in readings if r.available]
        roles: dict[str, Role] = {r.key: Role(ROLE_STANDBY) for r in readings}

        if s.mode == STANDBY or not active:
            self.primary = None
            self.last_split = None
            status = "standby" if s.mode == STANDBY else "idle"
            reason = "group in standby" if s.mode == STANDBY else "no battery available"
            return self._finish(roles, status, reason, readings, grid)

        if s.mode in (QUICK_CHARGE, QUICK_DISCHARGE):
            self.primary = None
            self.last_split = None
            for r in active:
                roles[r.key] = Role(ROLE_QUICK)
            label = "charging" if s.mode == QUICK_CHARGE else "discharging"
            return self._finish(roles, "quick", f"all batteries {label} at their maximum", readings, grid)

        if s.mode not in SMART:
            return self._finish(roles, "idle", f"unknown mode {s.mode}", readings, grid)

        wanted = self._wanted(active, s, grid)
        previous = self.primary
        self.primary = self._choose_primary(active, wanted)
        primary = next(r for r in active if r.key == self.primary)
        roles[primary.key] = Role(ROLE_PRIMARY)
        secondaries = [r for r in active if r.key != primary.key]
        # A former primary that hands over runs on at nothing until the next split.
        for r in secondaries:
            old = self.roles.get(r.key)
            if old is not None and old.role == ROLE_SECONDARY:
                roles[r.key] = Role(ROLE_SECONDARY, old.direction, old.limit)
            else:
                roles[r.key] = Role(ROLE_SECONDARY, NONE, 0)

        running = {roles[r.key].direction for r in secondaries if roles[r.key].limit > 0}
        running_dir = next(iter(running)) if len(running) == 1 else NONE
        direction = primary.direction if primary.direction != NONE else running_dir

        def stop_all(status: str, reason: str) -> Decision:
            for r in secondaries:
                roles[r.key] = Role(ROLE_SECONDARY, NONE, 0)
            self.last_split = None
            self.at_cap_since = None
            self.idle_since = None
            return self._finish(roles, status, reason, readings, grid)

        if previous is not None and previous != self.primary:
            return stop_all("regulating", f"{primary.name} takes over as primary")

        # The primary wants the other direction while secondaries still run. That
        # is a real reversal only when the group has nothing left to do in the
        # running direction; after a load drop it is the secondaries delivering
        # too much, and they are lowered at once instead (below).
        reversal_asked = (
            primary.waiting_for != NONE and primary.waiting_for != running_dir and running_dir != NONE
        )

        if direction == NONE or not secondaries:
            reason = "primary alone" if not secondaries else "no direction running"
            return stop_all("concentrated", reason)

        if not allowed(s.mode, direction) or grid is None:
            why = "no valid grid reading" if grid is None else f"{direction} not allowed in {s.mode}"
            return stop_all("regulating" if grid is not None else "meter_stale", why)

        if running_dir not in (NONE, direction):
            return stop_all("reversing", f"secondaries stop: the primary runs {direction}")

        # ── Demand: what the batteries deliver now plus what the grid still asks
        sign = 1 if direction == DISCHARGE else -1
        buffer = s.discharge_buffer if direction == DISCHARGE else s.charge_buffer
        excess = sign * (grid - buffer)      # negative: the group delivers too much
        demand = sum(r.flow(direction) for r in active) + excess
        demand = max(0.0, demand)
        self.demand = demand

        if reversal_asked and demand <= CAP_MARGIN_W:
            return stop_all("reversing", f"secondaries stop before {primary.name} starts {primary.waiting_for}")

        # ── When to split again
        primary_limit = primary.limit(direction)
        primary_cap = primary.room(direction)
        secondaries_on = sum(roles[r.key].limit for r in secondaries)
        spare = sum(r.room(direction) - roles[r.key].limit for r in secondaries)
        at_cap = primary_cap > 0 and primary_limit >= primary_cap - CAP_MARGIN_W and spare > 0
        at_nothing = primary_limit <= CAP_MARGIN_W and secondaries_on > 0
        self.at_cap_since = (self.at_cap_since or now) if at_cap else None
        self.idle_since = (self.idle_since or now) if at_nothing else None

        due = self.last_split is None or now - self.last_split >= s.redistribute_s
        urgent = (
            (self.at_cap_since is not None and now - self.at_cap_since >= QUICK_REACT_S)
            or (self.idle_since is not None and now - self.idle_since >= QUICK_REACT_S)
        )
        if self.last_split is not None and now - self.last_split < MIN_GAP_S:
            due = urgent = False
        # The secondaries deliver more than the house takes and the primary has
        # nothing left to give back: lower them now, not after 10 or 15 s.
        # Measured before: 9 to 12 s of export up to 2300 W per load drop, then
        # the whole group stopped and the primary started cold.
        overshoot = at_nothing and excess < -CAP_MARGIN_W
        if reversal_asked or overshoot:
            urgent = True
        # A secondary that has reached its SOC bound stops at once.
        closed = [r for r in secondaries if roles[r.key].limit > 0 and r.closed(direction)]
        for r in closed:
            roles[r.key] = Role(ROLE_SECONDARY, NONE, 0)

        if not (due or urgent):
            return self._finish(roles, self._running_status(roles, secondaries), self._why(primary, direction), readings, grid)

        # ── Concentrate below the threshold
        threshold = s.concentrate_below
        if self.concentrated and demand > threshold:
            self.concentrated = False
        elif not self.concentrated and demand < threshold * CONCENTRATE_RELEASE:
            self.concentrated = True

        if self.concentrated:
            targets = {r.key: 0 for r in secondaries}
        else:
            rooms = {r.key: r.room(direction) for r in secondaries}
            # A primary that stands still keeps no share: moving load onto it
            # means a cold start of 6 to 15 s with the grid uncovered. It takes
            # its share at the first split after it runs again.
            share = PRIMARY_SHARE * primary_cap if primary_limit > CAP_MARGIN_W else 0.0
            total = min(sum(rooms.values()), max(0.0, demand - share))
            targets = split(total, rooms)

        for r in secondaries:
            old = roles[r.key]
            new = targets[r.key]
            old_limit = old.limit if old.direction == direction else 0
            if new == old_limit:
                roles[r.key] = Role(ROLE_SECONDARY, direction if new else NONE, new)
                continue
            if new and old_limit and abs(new - old_limit) < STEP_MIN:
                continue
            roles[r.key] = Role(ROLE_SECONDARY, direction if new else NONE, new)

        self.last_split = now
        self.at_cap_since = None
        self.idle_since = None
        return self._finish(roles, self._running_status(roles, secondaries), self._why(primary, direction), readings, grid)

    @staticmethod
    def _running_status(roles: dict[str, Role], secondaries: list[Reading]) -> str:
        """`concentrated` while the primary works alone, `regulating` once a secondary runs."""
        return "regulating" if any(roles[r.key].limit > 0 for r in secondaries) else "concentrated"

    @staticmethod
    def _why(primary: Reading, direction: str) -> str:
        return f"{primary.name} is primary, {direction}"

    def _finish(
        self, roles: dict[str, Role], status: str, reason: str, readings: list[Reading], grid: float | None,
    ) -> Decision:
        changed = [
            key for key, role in roles.items()
            if self.roles.get(key) != role
        ]
        # Stops before starts: a battery that lets go is written first.
        changed.sort(key=lambda key: roles[key].limit)
        self.roles = roles
        names = {r.key: r.name for r in readings}
        attributes = {
            "primary": names.get(self.primary) if self.primary else None,
            "demand": round(self.demand),
            "grid": None if grid is None else round(grid),
            "concentrated": self.concentrated,
            "batteries": {
                names[key]: {
                    "role": role.role,
                    "direction": role.direction,
                    "limit": role.limit,
                }
                for key, role in roles.items()
            },
        }
        return Decision(roles, self.primary, status, reason, changed, attributes)
