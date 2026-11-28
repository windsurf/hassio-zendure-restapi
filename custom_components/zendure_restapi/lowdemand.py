"""Low demand: stopping, starting and raising (split out of controller.py in v1.8.8).

A direction stops when the demand stays under its stop threshold for Stop
after; it starts again once a demand has lasted Start after, above a start
threshold with a gap; a running limit is raised only after Raise after. The
peaks skipped and followed are counted here. These are methods of
ZendureController; nothing here holds state of its own.
"""

from __future__ import annotations

from datetime import timedelta

import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .const import (
    FOLLOWED_PEAK_S,
    OPT_RAISE_AFTER,
    RAISE_MARGIN_SHARE,
    RAISE_MARGIN_W,
    RISE_MIN_W,
    START_GAP_MIN_W,
    START_GAP_SHARE,
    CONTROL_MIN_STEP,
    OPT_DISCHARGE_BUFFER,
    OPT_MODE_THRESHOLD,
    OPT_OPERATION_MODE,
    OPT_START_AFTER,
    OPT_STOP_AFTER,
    OPT_STOP_CHARGE_BELOW,
    OPT_STOP_DISCHARGE_BELOW,
    OPT_CONCENTRATE_BELOW,
)
from .control_util import _today

# The controller's own logger, so log settings for it keep covering these lines.
_LOGGER = logging.getLogger(__package__ + ".controller")


class LowDemandMixin:
    """Stopping under the threshold, starting after a wait, and raising a running limit."""

    # ── Stop below (v1.7.4) ──────────────────────────────────────────────

    def _stop_limit(self, charging: bool) -> int:
        """The demand under which a running direction stops, 0 when off.

        In a battery cluster only the primary runs this loop, and its own demand
        is the cluster's only while it works alone: below Concentrate below. So
        the limit is capped there, and with Concentrate below at 0 it is off.
        """
        limit = max(0, self.settings.get_int(
            OPT_STOP_CHARGE_BELOW if charging else OPT_STOP_DISCHARGE_BELOW
        ))
        group = getattr(self.settings, "group", None)
        if limit > 0 and getattr(self.settings, "coordinated", False) and group is not None:
            limit = min(limit, max(0, group.get_int(OPT_CONCENTRATE_BELOW)))
        return limit

    def _stop_after(self) -> int:
        """Seconds the demand must stay under the limit before a direction stops."""
        return max(0, self.settings.get_int(OPT_STOP_AFTER))

    async def _stop_below(self, data: dict[str, Any], direction: str, raw: float) -> bool:
        """Stop a running direction whose demand stays under its limit; True when stopped.

        The demand is the target before clamp and floor: with the battery
        running the grid sits near zero and says nothing, the target is what the
        battery needs to keep it there. Stopping writes both limits to 0 in one
        request, the state in which the device rests (5 W measured, against
        ~37 W of conversion loss at 145 W). The trim loop is not touched before:
        it follows the low demand until the time is up.
        """
        limit = self._stop_limit(direction == "charge")
        if limit <= 0 or raw >= limit:
            self._below_since = None
            self._below_dir = None
            return False
        now = dt_util.utcnow()
        if self._below_since is None or self._below_dir != direction:
            self._below_since = now
            self._below_dir = direction
        waited = (now - self._below_since).total_seconds()
        after = self._stop_after()
        if waited < after:
            return False
        await self._write_many([("inputLimit", 0), ("outputLimit", 0)], data)
        self._owns_limits = False
        self._last_direction = "none"
        self._trim_direction = None
        self._pending = 0.0
        self._below_since = None
        self._below_dir = None
        self._stopped = direction
        self._stopped_mode = str(self.settings.get(OPT_OPERATION_MODE))
        self._start_since = None
        self._start_dir = None
        self._set_state(
            "tracking",
            f"stopped {direction}: demand {max(0.0, raw):.0f} W under {limit} W for {waited:.0f} s",
        )
        return True

    def _start_after(self) -> int:
        """Seconds the demand must stay past the start threshold before a direction starts."""
        return max(0, self.settings.get_int(OPT_START_AFTER))

    async def _hold_start(
        self,
        grid: float,
        mode: str,
        start_discharge: int,
        start_charge: int,
        allow_discharge: bool,
        allow_charge: bool,
    ) -> bool:
        """Hold back a start until the demand has stayed past its threshold; True while it holds.

        Only called while no direction runs (since v1.7.4 also not for one
        that runs under the deadband: that one is balanced, see _apply_smart).
        The threshold of a direction is
        the larger of its start threshold and its stop limit plus a gap
        (v1.7.6, _start_threshold), always (v1.7.4):
        a start below the stop limit would only stop again after Stop after.
        Until then this applied only after a stop of its own, which the
        controller keeps in memory alone, so after a restart of Home Assistant,
        a reload or a mode change the first start came at the start threshold
        and stopped again a minute later. A changed mode or a stop limit set
        back to 0 ends a stop of its own; a start ends it too (_smart_step).
        With the stop limits at 0 and Start after at 0 this holds nothing, and
        the start is exactly as in v1.7.3.
        """
        stopped = self._stopped
        if stopped is not None:
            if self._stop_limit(stopped == "charge") <= 0 or self._stopped_mode != mode:
                stopped = self._stopped = None
        discharge_at = self._start_threshold(False, start_discharge)
        charge_at = self._start_threshold(True, -start_charge)
        if grid > discharge_at and allow_discharge:
            wanted, demand, threshold = "discharge", grid, discharge_at
        elif -grid > charge_at and allow_charge:
            wanted, demand, threshold = "charge", -grid, charge_at
        else:
            # The demand fell back before Start after was up: a skipped peak.
            self._skipped_peak()
            self._start_since = None
            self._start_dir = None
            if stopped is None:
                # Past the start threshold but not past the stop limit: a start
                # here would stop again after Stop after.
                if grid > start_discharge and allow_discharge:
                    waiting, demand, limit = "discharge", grid, discharge_at
                elif grid < start_charge and allow_charge:
                    waiting, demand, limit = "charge", -grid, charge_at
                else:
                    return False
                self._set_state(
                    "tracking",
                    f"{waiting} waits: demand {demand:.0f} W under the start threshold {limit} W",
                )
                return True
            limit = discharge_at if stopped == "discharge" else charge_at
            demand = grid if stopped == "discharge" else -grid
            self._set_state(
                "tracking",
                f"stopped {stopped}: demand {max(0.0, demand):.0f} W, "
                f"starts again above {limit} W for {self._start_after()} s",
            )
            return True
        after = self._start_after()
        if after <= 0:
            return False
        now = dt_util.utcnow()
        if self._start_since is None or self._start_dir != wanted:
            # The other direction was waiting: that one never came.
            self._skipped_peak()
            self._start_since = now
            self._start_dir = wanted
            self._start_wh = 0.0
            self._start_last = now
        # What the grid gave meanwhile, for the skipped peak should it end so.
        self._start_wh += demand * max(0.0, (now - self._start_last).total_seconds()) / 3600.0
        self._start_last = now
        waited = (now - self._start_since).total_seconds()
        if waited >= after:
            return False
        self._set_state(
            "tracking",
            f"{wanted} waits: demand {demand:.0f} W above {threshold} W for {waited:.0f} of {after} s",
        )
        return True

    def _raise_after(self) -> int:
        """Seconds a higher demand lasts before a running discharge goes up for it (v1.8.3)."""
        return max(0, self.settings.get_int(OPT_RAISE_AFTER))

    @staticmethod
    def _raise_margin(limit: int) -> int:
        """How far above a running limit a demand counts as a rise (v1.8.3)."""
        return max(RAISE_MARGIN_W, int(limit * RAISE_MARGIN_SHARE))

    def _hold_raise(self, current: int, target: int, grid: float) -> int:
        """The limit a running discharge may go to now: held while a rise waits (v1.8.3).

        Start after keeps a short peak from starting a battery that stands still;
        a battery that already discharges followed every rise at once until
        v1.8.2: 4-5 s late on a peak of about 10 s, then exported for as long
        again while it came back down. Now a demand more than the margin above
        the running limit waits Raise after; lower and within the margin go at
        once, so the base load stays covered. A rise that falls back before
        the wait is over is a skipped peak, while running. Once the wait is
        over the battery follows that rise freely until the demand is back
        within the margin. Raise after 0 is the behaviour of v1.8.2.
        """
        after = self._raise_after()
        if after <= 0 or current <= 0:
            self._reset_raise()
            return target
        now = dt_util.utcnow()
        ref = self._raise_ref if self._raise_since is not None else current
        if target <= ref + self._raise_margin(ref):
            if self._raise_since is not None and not self._raise_done and self._raise_peak - ref >= RISE_MIN_W:
                # The demand fell back in time: a peak the battery did not follow.
                # Counted from RISE_MIN_W only: a held rise of 120 W on a low
                # base load is not a peak, and a night gave 52 of those.
                self.measure.on_skipped_peak(
                    _today(now), "discharge", (now - self._raise_since).total_seconds(),
                    self._raise_wh, running=True,
                )
            self._reset_raise()
            return target
        if self._raise_done:
            return target
        if self._raise_since is None:
            self._raise_since = now
            self._raise_ref = current
            self._raise_wh = 0.0
            self._raise_last = now
            self._raise_peak = current
        self._raise_peak = max(self._raise_peak, target)
        # The grid's share of the rise, for the skipped peak should it end so.
        excess = max(0.0, grid - self.settings.get_int(OPT_DISCHARGE_BUFFER))
        self._raise_wh += excess * max(0.0, (now - self._raise_last).total_seconds()) / 3600.0
        self._raise_last = now
        if (now - self._raise_since).total_seconds() >= after:
            self._raise_done = True
            return target
        return min(target, max(current, self._raise_ref))

    def _reset_raise(self) -> None:
        self._raise_since = None
        self._raise_ref = 0
        self._raise_wh = 0.0
        self._raise_last = None
        self._raise_done = False
        self._raise_peak = 0

    def _track_follow(self, charging: bool, current: int, grid: float) -> None:
        """Count a raise of the running direction that is back within a minute (v1.8.3).

        Measured on every meter sample while a discharge runs: the limit before
        it went up is the base; going at least RISE_MIN_W (and the margin) above
        it starts a follow, coming back within the margin of the base ends it. Within
        FOLLOWED_PEAK_S that was a short peak followed: the grid gave the start
        of it, and took the battery's late way back down. Longer is a load the
        battery covered, and is not counted.
        """
        now = dt_util.utcnow()
        seconds = max(0.0, (now - self._follow_last).total_seconds()) if self._follow_last else 0.0
        self._follow_last = now
        if charging:
            # Charging follows the sun, not the house: a rise there is a cloud
            # passing, and a noisy meter made 45 "peaks" of it in 22 minutes.
            self._follow_since = None
            self._follow_base = None
            return
        if self._follow_since is None:
            if self._follow_base is not None and current > self._follow_base + max(RISE_MIN_W, self._raise_margin(self._follow_base)):
                # Up since the previous sample: this sample's grid is already part of it.
                self._follow_since = now - timedelta(seconds=seconds)
                self._follow_import = 0.0
                self._follow_export = 0.0
            else:
                self._follow_base = current
                return
        # Import is the part of the peak not covered, export the part overdone
        # while the battery came back down.
        self._follow_import += max(0.0, grid) * seconds / 3600.0
        self._follow_export += max(0.0, -grid) * seconds / 3600.0
        lasted = (now - self._follow_since).total_seconds()
        base = self._follow_base or 0
        if current <= base + self._raise_margin(base):
            if lasted <= FOLLOWED_PEAK_S:
                self.measure.on_followed_peak(_today(now), lasted, self._follow_import, self._follow_export)
            self._follow_since = None
            self._follow_base = current
        elif lasted > FOLLOWED_PEAK_S:
            # A load the battery covers, not a peak: start again from here.
            self._follow_since = None
            self._follow_base = None

    def _start_threshold(self, charging: bool, start: int) -> int:
        """Where a direction starts: its start threshold, or past its stop limit (v1.7.6).

        With a stop limit set the start lies START_GAP_SHARE above it, at least
        START_GAP_MIN_W: 50 W stops, 70 W starts; 80 W stops, 100 W starts.
        Until v1.7.5 the start lay at the stop limit itself, and a load that
        hovered there, 55 W on a 50 W limit, started the battery and stopped it
        again after Stop after, and again. With the stop limit at 0 nothing
        changes.
        """
        stop = self._stop_limit(charging)
        if stop <= 0:
            return start
        return max(start, stop + max(START_GAP_MIN_W, round(stop * START_GAP_SHARE)))

    def _skipped_peak(self) -> None:
        """File a start that Start after held back and that never came (v1.7.4)."""
        since, direction = self._start_since, self._start_dir
        if since is None or direction is None:
            return
        now = dt_util.utcnow()
        self.measure.on_skipped_peak(
            _today(now), direction, (now - since).total_seconds(), self._start_wh
        )

    def _mode_threshold(self) -> int:
        """Smallest adjustment the mode loop bothers to write."""
        return max(CONTROL_MIN_STEP, self.settings.get_int(OPT_MODE_THRESHOLD))
