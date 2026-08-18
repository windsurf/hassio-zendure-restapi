"""Energy accumulation for the Home Assistant Energy dashboard.

The device reports instantaneous power only. Every payload observed on hardware
carries watts and no cumulative counter, so kilowatt-hours have to be built up
here.

Integration is trapezoidal: each interval uses the average of the previous and
current reading rather than either endpoint. With a ten second poll and a load
that steps, a left-hand rectangle would attribute the entire interval to the
old value and a right-hand one to the new; the average splits the difference
and is exact for any linear ramp.

Totals survive a restart through RestoreEntity. They are deliberately not reset
on reload: the Energy dashboard treats a drop as a meter replacement and would
double-count the difference.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util

_LOGGER = logging.getLogger(__name__)

# A gap longer than this means the integration was stopped, the device was
# unreachable, or the machine slept. Bridging it with the trapezoid rule would
# invent energy that was never measured, so the interval is skipped and the
# accumulator simply resumes.
MAX_GAP_SECONDS = 300

# Readings above this are treated as spurious rather than integrated.
MAX_PLAUSIBLE_POWER = 20000


class EnergyAccumulator:
    """Turns a series of power readings into a monotonic kWh total."""

    def __init__(self) -> None:
        self.total_kwh: float = 0.0
        self._last_power: float | None = None
        self._last_time: datetime | None = None

    def restore(self, total_kwh: float) -> None:
        """Adopt a total recovered from the state machine after a restart."""
        self.total_kwh = max(0.0, float(total_kwh))

    def add(self, power_w: float, now: datetime | None = None) -> float:
        """Fold one reading into the total and return the new total."""
        now = now or dt_util.utcnow()

        if power_w < 0 or power_w > MAX_PLAUSIBLE_POWER:
            _LOGGER.debug("Ignoring implausible power reading %s W", power_w)
            return self.total_kwh

        if self._last_power is not None and self._last_time is not None:
            elapsed = (now - self._last_time).total_seconds()
            if 0 < elapsed <= MAX_GAP_SECONDS:
                average_w = (self._last_power + power_w) / 2.0
                self.total_kwh += average_w * elapsed / 3_600_000.0
            elif elapsed > MAX_GAP_SECONDS:
                _LOGGER.debug(
                    "Skipping a %.0f s gap rather than interpolating across it", elapsed
                )

        self._last_power = power_w
        self._last_time = now
        return self.total_kwh


def split_signed(value: Any, positive: bool) -> float:
    """Take one side of a signed power reading, as a positive magnitude.

    Grid power and battery power are both signed, and the Energy dashboard
    wants each direction as its own always-increasing counter.
    """
    try:
        power = float(value)
    except (TypeError, ValueError):
        return 0.0
    if positive:
        return power if power > 0 else 0.0
    return -power if power < 0 else 0.0
