"""Trim loop of the operation-mode controller (split out of controller.py in v1.8.8).

Once per meter sample the trim loop adjusts the limit within the direction that
is already running: ``limit + (grid - buffer) x factor``. It never starts a
direction, never reverses one and never overrules a mode. Here too: the meter's
noise and step, the adaptive trim threshold and the advice for Mode threshold.
These are methods of ZendureController; nothing here holds state of its own.
"""

from __future__ import annotations

import logging
import statistics
from collections import deque

from homeassistant.util import dt as dt_util

from .api import ZendureApiError
from .const import (
    DEFAULTS,
    RUN_FLOOR_W,
    MODE_ADVICE_SHARE,
    MODE_ADVICE_STEP,
    NOISE_FACTOR,
    NOISE_MIN_SAMPLES,
    NOISE_WINDOW,
    CONTROL_MIN_STEP,
    OPT_CHARGE_BUFFER,
    OPT_DISCHARGE_BUFFER,
    OPT_MIN_CHARGE_POWER,
    OPT_MIN_DISCHARGE_POWER,
    OPT_MODE_THRESHOLD,
    OPT_TRIM_THRESHOLD,
    OPT_TRIM_FACTOR,
)
from .control_util import _clamp

# The controller's own logger, so log settings for it keep covering these lines.
_LOGGER = logging.getLogger(__package__ + ".controller")


class TrimMixin:
    """The trim loop and what it learns from the meter."""

    # ── Trim loop ────────────────────────────────────────────────────────

    async def async_sample(self) -> None:
        """Handle one meter sample: record it, then trim on it.

        Recording comes first and runs in every mode, including standby, where
        this controller writes nothing and whatever else steers the device is
        the only thing moving. The row therefore describes the situation the
        trim step is about to act on, and the write it makes appears on the
        next row — cause before effect, which is what a trace is read for.
        """
        self._watch_writers()
        await self._record()
        await self.async_trim()
        try:
            self._tracking(self._meter_reading()[0])
        except Exception:  # noqa: BLE001 - measuring must never stop the loop
            _LOGGER.exception("Measured diagnostics: meter sample failed")

    async def async_trim(self) -> None:
        """Execute one trim step, once per meter sample. Never raises."""
        if self._trim_busy or self._busy:
            # The mode loop is authoritative; a trim that interleaved with it
            # would write against a half-applied decision.
            return
        self._trim_busy = True
        try:
            await self._trim()
        except ZendureApiError as err:
            _LOGGER.warning("Trim write failed: %s", err)
        except Exception:  # noqa: BLE001 - a control loop must not kill the poll
            _LOGGER.exception("Unexpected trim failure")
        finally:
            self._trim_busy = False

    def _sample_noise(self) -> None:
        """Note the step of the meter since its last sample (v1.8.4).

        Every sample, running or not, so the threshold is ready when a direction
        starts. A step between two meters is their offset, not noise: a change
        of source empties the window.
        """
        grid, source = self._meter_reading()
        if grid is None:
            return
        if self._noise is None or source != self._noise_source:
            self._noise = deque(maxlen=NOISE_WINDOW)
            self._noise_prev = None
            self._noise_source = source
        if self._noise_prev is not None:
            self._noise.append(abs(grid - self._noise_prev))
        self._noise_prev = grid

    def meter_noise(self) -> float | None:
        """Median step of the meter per sample, once enough samples are in."""
        if self._noise is None or len(self._noise) < NOISE_MIN_SAMPLES:
            return None
        return float(statistics.median(self._noise))

    def mode_threshold_advice(self) -> int | None:
        """A Mode threshold for this meter (v1.8.7): the upper quartile of its step
        per sample, rounded up to MODE_ADVICE_STEP, never under the default.

        Advice only: the setting is the user's. A quiet meter gets the default.
        """
        if self._noise is None or len(self._noise) < NOISE_MIN_SAMPLES:
            return None
        steps = sorted(self._noise)
        upper = steps[min(len(steps) - 1, int(MODE_ADVICE_SHARE * len(steps)))]
        rounded = -(-int(round(upper)) // MODE_ADVICE_STEP) * MODE_ADVICE_STEP
        return max(int(DEFAULTS[OPT_MODE_THRESHOLD]), rounded)

    async def _trim(self) -> None:
        """Adjust the running limit against the meter, within one direction."""
        self._sample_noise()
        self._write_source = "trim"
        direction = self._trim_direction
        if direction is None:
            return

        data = self.coordinator.data or {}
        if not data:
            return

        grid = self._meter_power()
        if grid is None:
            # No trustworthy feedback. A closed loop with no feedback is worse
            # than no loop, so the battery is stopped rather than steered on a
            # guess. Releasing to zero is always safe: it is the direction the
            # clamp already allows, and it cannot reverse anything.
            await self._fail_safe(data, "no valid meter reading")
            return

        charging = direction == "charge"
        full, empty = self._bounds(data)
        if (charging and full) or (not charging and empty):
            # The device has closed this direction. Adjusting a limit it
            # ignores is the write traffic the bound check exists to stop;
            # the mode loop clears the limit on its next cycle.
            return
        key = "inputLimit" if charging else "outputLimit"
        try:
            current = int(data.get(key) or 0)
        except (TypeError, ValueError):
            return
        if current <= 0:
            # Nothing is running any more; the mode loop decides what is next.
            self._trim_direction = None
            return

        if self._source_used != self._trim_source:
            # The first source seen is not a switch: resetting there would make
            # the default `zendure` behave differently from v1.4.3 on the first
            # trim after every start.
            if self._trim_source != "none":
                self._prev_grid = None
            self._trim_source = self._source_used

        self._track_follow(charging, current, grid)

        # ── What has arrived since the last look ─────────────────────
        # Movement in the direction the outstanding order predicted counts as
        # that order landing. Movement the other way is the house, and must
        # not be credited: over-crediting would let the loop order the same
        # watts twice, which is the whole thing this avoids.
        if self._prev_grid is not None and self._pending != 0.0:
            moved = grid - self._prev_grid
            if (self._pending < 0 < -moved) or (self._pending > 0 < moved):
                if abs(moved) >= abs(self._pending):
                    self._pending = 0.0
                else:
                    self._pending -= moved
        self._prev_grid = grid

        buffer = self.settings.get_int(
            OPT_CHARGE_BUFFER if charging else OPT_DISCHARGE_BUFFER
        )

        # Every sample is a fresh measurement, but not a fresh error: the part
        # already ordered and still on its way is subtracted before correcting.
        effective = (grid - buffer) + self._pending
        step = effective * self._trim_factor()

        # A discharge limit removes watts from the grid, a charge limit adds
        # them, so the same intent moves the two the opposite way.
        target = int(_clamp(
            current - step if charging else current + step,
            0,
            self._cap(data, charging),
        ))

        # The floor keeps the direction alive at at least this much, and blocks
        # the hand-back at zero: letting the trim loop release a limit the
        # setting demands would only have the mode loop restore it next poll.
        floor = self.settings.get_int(
            OPT_MIN_CHARGE_POWER if charging else OPT_MIN_DISCHARGE_POWER
        )
        if floor > 0 and target < floor:
            target = int(min(floor, self._cap(data, charging)))
        # The trim loop does not end a direction either (v1.7.5): ending is
        # the mode loop's, after two cycles in a row with nothing to do, or the
        # stop rule's. Seen after short peaks: a trim step on a lagging reading
        # took the limit to 0 and the next demand needed a new start.
        if current > 0 and target < min(RUN_FLOOR_W, current):
            target = min(RUN_FLOOR_W, current)
        # A higher demand waits Raise after before a running discharge goes up
        # for it (v1.8.3); lower goes at once.
        if not charging:
            target = self._hold_raise(current, target, grid)

        delta = target - current
        # Adjustments only. This loop never opens a direction — it is entered
        # solely while _trim_direction is set, which the mode loop clears the
        # moment the running limit reaches zero. A threshold above the day's
        # surplus therefore cannot leave the battery idle: starting is the mode
        # loop's business, and there the threshold is skipped while starting.
        if abs(delta) < self._trim_threshold():
            return

        await self._write(key, target, data)
        # Record it as ordered, in grid watts: a larger discharge limit means
        # the grid still has that much further to fall.
        self._pending += delta if charging else -delta
        self._fast_write_at = dt_util.utcnow()
        self._trim_writes += 1

        if target <= 0:
            self._owns_limits = False
            self._trim_direction = None

        self.state.meter_power = grid
        _LOGGER.debug(
            "trim | %s %sW -> %sW | grid=%.0fW buffer=%sW pending=%.0fW",
            direction, current, target, grid, buffer, self._pending,
        )

    def _trim_factor(self) -> float:
        """How much of each trim correction to apply, as a fraction.

        This damps the trim loop only; the mode loop keeps its full correction.
        The difference is dead time relative to loop period: one second of
        meter delay is a whole period here and a tenth of one there.

        Stored as whole percent so it travels the same integer path as every
        other setting.
        """
        percent = self.settings.get_int(OPT_TRIM_FACTOR)
        return _clamp(percent, 1, 100) / 100.0

    def _trim_threshold(self) -> int:
        """Smallest adjustment the trim loop bothers to write.

        Higher than the mode loop's by design, and measurably so: the trim loop
        runs once a second and therefore sees every excursion the house makes,
        including the ones that would have passed on their own. Correcting
        those does not settle the grid, it moves the battery to where the house
        no longer is.
        """
        floor = max(CONTROL_MIN_STEP, self.settings.get_int(OPT_TRIM_THRESHOLD))
        # On a noisy meter the threshold follows the noise (v1.8.4): a step the
        # meter makes by itself every second is no error to correct.
        noise = self.meter_noise()
        if noise is None:
            return floor
        return max(floor, int(round(NOISE_FACTOR * noise)))
