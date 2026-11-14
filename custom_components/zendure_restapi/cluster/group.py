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
    ROLE_PROTECT,
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
# SOC protection of the group (v1.7.6) charges each battery to its floor plus
# this many points, as a single battery does (SOC_REARM_POINTS in const.py;
# this package imports nothing from the integration).
PROTECT_REARM_POINTS = 5
# With Price Control (v1.8.1) the group waits at most this long for the
# cheapest period, and not at all once a battery sits this many points under
# its floor: SOC_PROTECT_WINDOW_H and SOC_PROTECT_HARD_POINTS in const.py.
PROTECT_WINDOW_S = 6 * 3600
PROTECT_HARD_POINTS = 2
# The primary is "at its cap" within this of it, and "at nothing" below this.
CAP_MARGIN_W = 50
# Peaks (v1.7.4): the secondaries are added only once the demand has stayed
# above Concentrate below for the cluster's Start after, unless the primary is
# at its cap. A boiling-water tap of 2.2 kW for 10 s took two secondaries for
# 10 to 30 s five times in one night; the primary alone covers such a peak up to
# its own maximum.


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


def split(total: float, rooms: dict[str, int], floor: bool = False) -> dict[str, int]:
    """`total` over the batteries in proportion to their room, never past it.

    What one battery cannot take (its share above its room) goes to the
    others, so the sum is min(total, sum of rooms) before rounding.

    Rounded per battery to ROUND_W. With `floor`, together never above the
    total rounded down (v1.7.4): 181 W over two came out as 100 + 100, 19 W
    into the grid with the primary at nothing and not allowed to charge; now one
    of them gets 50 and the shortfall comes from the grid, the cheaper side to be
    wrong. Only then: a primary that runs takes up a rounding excess by itself,
    and rounding down there only moved energy from the batteries to the grid
    (replay: +176 Wh import against -72 Wh export in an afternoon).
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
    out = {key: min(rooms[key], _round(value)) for key, value in shares.items()}
    if not floor:
        return out
    ceiling = int(min(max(0.0, float(total)), sum(max(0, r) for r in rooms.values())) // ROUND_W * ROUND_W)
    while sum(out.values()) > ceiling:
        # Take a step off the battery whose rounding added the most.
        key = max((k for k in out if out[k] > 0), key=lambda k: (out[k] - shares[k], k))
        out[key] -= ROUND_W
    return out


class Distributor:
    """The cluster's memory between two decisions, and the decision itself."""

    def __init__(self) -> None:
        self.primary: str | None = None
        self.roles: dict[str, Role] = {}
        self.concentrated = True
        self.last_split: float | None = None
        self.at_cap_since: float | None = None
        self.idle_since: float | None = None
        self.above_since: float | None = None
        self.demand: float = 0.0
        # SOC protection of the group runs (v1.7.6).
        self.protecting = False
        # With Price Control (v1.8.1): since when every battery is under its
        # floor, what it waits for, and why it charges now.
        self.protect_since: float | None = None
        self.protect_waits: str | None = None
        self.protect_why: str | None = None

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

    # ── SOC protection ───────────────────────────────────────────────────

    def _protect(
        self,
        active: list[Reading],
        s: GroupSettings,
        roles: dict[str, Role],
        readings: list[Reading],
        grid: float | None,
        now: float = 0.0,
    ) -> Decision | None:
        """Charge the group from the grid when every battery is under its floor (v1.7.6).

        Only then: while one battery still has charge, a battery under its
        floor that charges from the grid takes what the other discharges —
        measured in v1.7.0 as 2400 W from one battery into the other for 55 s.
        With all of them under their floor none discharges, and each charges
        at its own cap to its floor + PROTECT_REARM_POINTS; one that is there
        stands still until the last is. Only in the smart modes: standby means
        hands off, and the quick modes are the operator's. With `price_control`
        (v1.8.1) the start may wait for the cheapest period of the cluster's
        Price Control (_protect_wait); once it charges it carries on whatever
        the price. None when it does not act.
        """
        if not s.soc_protection:
            self.protecting = False
            self._protect_reset()
            return None
        if not self.protecting:
            if not all(r.below_floor for r in active):
                self._protect_reset()
                return None
            waits = self._protect_wait(active, s, now)
            if waits is not None:
                # Meanwhile the group runs as usual: discharging is closed for a
                # battery under its floor, surplus still charges it.
                self.protect_waits = waits
                return None
            self.protect_waits = None
            self.protecting = True

        def short(r: Reading) -> bool:
            return r.soc is not None and r.floor is not None and r.soc < r.floor + PROTECT_REARM_POINTS

        charging = [r for r in active if short(r) and not r.full]
        if not charging:
            self.protecting = False
            self._protect_reset()
            return None
        for r in charging:
            roles[r.key] = Role(ROLE_PROTECT, CHARGE, r.cap(CHARGE))
        self.primary = None
        self.last_split = None
        self.at_cap_since = None
        self.idle_since = None
        self.above_since = None
        names = ", ".join(f"{r.name} to {r.floor + PROTECT_REARM_POINTS:g} %" for r in charging)
        why = f"; {self.protect_why}" if self.protect_why else ""
        return self._finish(
            roles, "protecting", f"every battery under its SOC floor; charging {names}{why}", readings, grid,
        )

    def _protect_reset(self) -> None:
        """Not every battery under its floor, or protection off: forget a wait."""
        self.protect_since = None
        self.protect_waits = None
        self.protect_why = None

    def _protect_wait(self, active: list[Reading], s: GroupSettings, now: float) -> str | None:
        """With Price Control: what the group waits for, or None to charge now (v1.8.1).

        As a single battery does (v1.7.6): the cheapest period of the cluster's
        Price Control within PROTECT_WINDOW_S of the moment every battery was
        under its floor, of equal ones the first. One period is enough: from
        just under the floor to 5 points above it is minutes at full power.
        Direct, with the reason in the status, without Price Control or a price
        list, once the window is over, or with a battery PROTECT_HARD_POINTS or
        more under its floor.
        """
        self.protect_why = None
        if not s.soc_protection_price:
            return None
        if self.protect_since is None:
            self.protect_since = now
        if any(r.soc is not None and r.floor is not None and r.soc <= r.floor - PROTECT_HARD_POINTS for r in active):
            self.protect_why = f"direct: {PROTECT_HARD_POINTS} points or more under the floor"
            return None
        deadline = self.protect_since + PROTECT_WINDOW_S
        if now >= deadline:
            self.protect_why = f"direct: waited {PROTECT_WINDOW_S // 3600} h"
            return None
        periods = [p for p in s.price_periods if p[0] < deadline and p[1] > now]
        if not periods:
            self.protect_why = "direct: no prices from Price Control"
            return None
        best = min(periods, key=lambda p: (round(p[2], 5), p[0]))
        if best[0] <= now:
            self.protect_why = f"cheapest price within {PROTECT_WINDOW_S // 3600} h: {best[2]:g} per kWh"
            return None
        current = next((p for p in periods if p[0] <= now < p[1]), None)
        text = f"every battery under its SOC floor: charges at {best[3]}, {best[2]:g} per kWh"
        if current is not None:
            text += f" (now {current[2]:g})"
        return text

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
            # Hands off: a wait for a cheaper period starts again afterwards (v1.8.1).
            self._protect_reset()
            self.primary = None
            self.last_split = None
            status = "standby" if s.mode == STANDBY else "idle"
            reason = "cluster in standby" if s.mode == STANDBY else "no battery available"
            return self._finish(roles, status, reason, readings, grid)

        if s.mode in (QUICK_CHARGE, QUICK_DISCHARGE):
            self._protect_reset()
            self.primary = None
            self.last_split = None
            for r in active:
                roles[r.key] = Role(ROLE_QUICK)
            label = "charging" if s.mode == QUICK_CHARGE else "discharging"
            return self._finish(roles, "quick", f"all batteries {label} at their maximum", readings, grid)

        if s.mode not in SMART:
            self.protecting = False
            self._protect_reset()
            return self._finish(roles, "idle", f"unknown mode {s.mode}", readings, grid)

        protected = self._protect(active, s, roles, readings, grid, now)
        if protected is not None:
            return protected

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
            self.above_since = None
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
        # How long the demand has been above Concentrate below (v1.7.4), counted
        # at every decision, not only at a split.
        threshold = s.concentrate_below
        self.above_since = (self.above_since if self.above_since is not None else now) if demand > threshold else None
        held = (
            self.concentrated and demand > threshold and not at_cap and s.start_after > 0
            and now - self.above_since < s.start_after
        )

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

        why = self._why(primary, direction)
        if held:
            why = (f"{why}; secondaries wait: demand {round(demand)} W above {threshold} W "
                   f"for {round(now - self.above_since)} of {s.start_after} s")
        # A held peak does not use up the split: once the time is over the
        # secondaries come at the next decision, not a whole interval later.
        if not (due or urgent) or held:
            return self._finish(roles, self._running_status(roles, secondaries), why, readings, grid)

        # ── Concentrate below the threshold; a peak shorter than Start after
        # keeps the primary alone (v1.7.4).
        if self.concentrated and demand > threshold and not held:
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
            targets = split(total, rooms, floor=share == 0.0)

        for r in secondaries:
            old = roles[r.key]
            new = targets[r.key]
            old_limit = old.limit if old.direction == direction else 0
            if new == old_limit:
                roles[r.key] = Role(ROLE_SECONDARY, direction if new else NONE, new)
                continue
            # A small step is not worth a write, except a step down while the
            # group delivers more than the house takes and the primary has nothing
            # left to give back (v1.7.4): 100 -> 50 W was held at 100 W with
            # the primary at nothing, -53 W at 155 W of load.
            lowering = new < old_limit and excess < 0 and at_nothing
            if new and old_limit and abs(new - old_limit) < STEP_MIN and not lowering:
                continue
            roles[r.key] = Role(ROLE_SECONDARY, direction if new else NONE, new)

        self.last_split = now
        self.at_cap_since = None
        self.idle_since = None
        return self._finish(roles, self._running_status(roles, secondaries), why, readings, grid)

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
        if self.protect_waits:
            # SOC protection with Price Control waits for its period (v1.8.1).
            attributes["soc_protection_waits"] = self.protect_waits
        return Decision(roles, self.primary, status, reason, changed, attributes)
