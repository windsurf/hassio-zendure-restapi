"""Small helpers shared by the controller and its parts (v1.8.8)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.util import dt as dt_util


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _today(now: datetime) -> str:
    """The local date of a moment, for the counters that start again every day."""
    return dt_util.as_local(now).date().isoformat()


def _as_int(value: Any) -> int | None:
    """Best-effort integer, for recording rather than steering."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
