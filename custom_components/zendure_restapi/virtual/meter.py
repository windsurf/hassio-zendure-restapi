"""The virtual P1 meter of a virtual cluster (v1.7.1).

    house load    = real grid + AC power of the real battery
    virtual grid  = house load x Load factor - what the clones deliver

The real grid is the reading the real battery steers on, through its own
meter source; every sample that battery's trim loop gets is a sample here.
The real battery's power comes from its last poll, so for up to one poll
after the real battery changes its power the house load is off by that
change: it is the real battery's own step, not the house.

The meter offers what the controller reads from an external meter — data,
updated_at, listeners — and is attached to every clone as its external
meter. It reads; it never writes anything.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from ..const import EXTERNAL_STATUS_LIVE, EXTERNAL_STATUS_STALE, METER_FALLBACK_AGE

_LOGGER = logging.getLogger(__name__)


class VirtualMeter:
    """House load of the real installation, minus the clones."""

    kind = "virtual"
    host = "virtual P1"

    def __init__(
        self,
        source: Any,
        clones: Callable[[], list[Any]],
        factor: Callable[[], float],
    ) -> None:
        self.hass = source.coordinator.hass
        self._source = source            # the real battery's controller
        self._clones = clones            # the VirtualDevices
        self._factor = factor
        self.data: dict[str, Any] = {}
        self.updated_at: datetime | None = None
        self.entry_id: str | None = None
        self.telegrams = 0
        self.house: float | None = None
        self.real_grid: float | None = None
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._subscribed: tuple[Any, Any] = (None, None)

    # ── What the controller reads ───────────────────────────────────────

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    def recent_power(self, max_age: float) -> Any:
        if self.updated_at is None:
            return None
        if (dt_util.utcnow() - self.updated_at).total_seconds() > max_age:
            return None
        return self.data.get("power")

    @property
    def status(self) -> str:
        return EXTERNAL_STATUS_LIVE if self.recent_power(METER_FALLBACK_AGE) is not None else EXTERNAL_STATUS_STALE

    @property
    def label(self) -> str:
        return "Virtual P1"

    @property
    def update_interval(self) -> float | None:
        return None

    def telegram_age(self) -> float | None:
        if self.updated_at is None:
            return None
        return round((dt_util.utcnow() - self.updated_at).total_seconds(), 1)

    # ── Following the real meter ────────────────────────────────────────

    @callback
    def async_link(self) -> None:
        """Subscribe to the meters the real battery reads; again whenever they change."""
        meters = (self._source.meter, self._source.external)
        if meters == self._subscribed:
            return
        self.async_unlink()
        self._subscribed = meters
        zendure, external = meters
        if zendure is not None:
            self._unsubs.append(zendure.async_add_listener(lambda: self._on_real("zendure")))
        if external is not None:
            self._unsubs.append(external.async_add_listener(lambda: self._on_real("external")))

    @callback
    def async_unlink(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._subscribed = (None, None)

    @callback
    def async_stop(self) -> None:
        self.async_unlink()
        self._listeners.clear()

    @callback
    def _on_real(self, source: str) -> None:
        # Only the samples the real battery acts on: one per telegram, never two.
        grid, used = self._source._meter_reading()
        if used != source:
            return
        self.sample(grid)

    @callback
    def sample(self, grid: float | None) -> None:
        if grid is None:
            return
        data = self._source.coordinator.data or {}
        battery = self._source._battery_power(data) if data else 0.0
        house = float(grid) + float(battery)
        delivered = 0.0
        for device in self._clones():
            device.step()
            delivered += device.power
        virtual = house * self._factor() - delivered
        self.real_grid = float(grid)
        self.house = house
        self.data = {"power": round(virtual, 1), "house": round(house, 1), "real_grid": round(float(grid), 1)}
        self.updated_at = dt_util.utcnow()
        self.telegrams += 1
        for listener in list(self._listeners):
            listener()

    def diagnostics(self) -> dict[str, Any]:
        return {"type": self.kind, "status": self.status, "telegrams": self.telegrams, **self.data}
