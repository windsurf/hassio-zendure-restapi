"""SOC protection and the battery's own bounds (split out of controller.py in v1.8.8).

Below its lower SOC limit a battery charges back, at once or waiting for a
cheap price or the sun; full and empty, as the device reports them, block a
direction. These are methods of ZendureController plus the module-level reads
of the device's SOC fields; nothing here holds state of its own.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import logging
from typing import Any

from homeassistant.util import dt as dt_util

from .cluster.contract import solar_wait
from .const import (
    DOMAIN,
    AC_MODE_CHARGE,
    DEVICE_SELF_CHARGE_W,
    SOC_REARM_POINTS,
    SOC_PROTECT_HARD_POINTS,
    SOC_PROTECT_START_POINTS,
    SOC_PROTECT_PRICE,
    SOC_PROTECT_WINDOW_H,
    MODE_MANUAL,
    MODE_QUICK_DISCHARGE,
    MODE_STANDBY,
    OPT_SOC_PROTECTION,
    SOC_PROTECT_OFF,
    soc_protection_choice,
)
from .settings import ROLE_PROTECT
from .control_util import _as_int

# The controller's own logger, so log settings for it keep covering these lines.
_LOGGER = logging.getLogger(__package__ + ".controller")


# ── SOC bounds ───────────────────────────────────────────────────────────

SOC_LIMIT_UPPER = 1
SOC_LIMIT_LOWER = 2

SOC_BLOCKED = {
    "charge": "charging blocked: battery full (upper limit)",
    "discharge": "discharging blocked: battery empty (lower limit)",
}


def _soc_bounds(data: dict[str, Any]) -> tuple[bool, bool]:
    """(full, empty) from the device's socLimit. Missing or unreadable is
    neither: a bound is only respected when the device reports one."""
    try:
        flag = int(data.get("socLimit"))
    except (TypeError, ValueError):
        return False, False
    return flag == SOC_LIMIT_UPPER, flag == SOC_LIMIT_LOWER


def _soc_percent(data: dict[str, Any], key: str, scale: float) -> float | None:
    """A state of charge in percent: electricLevel as is, minSoc and socSet in tenths."""
    try:
        return float(data.get(key)) * scale
    except (TypeError, ValueError):
        return None


def _below_floor(data: dict[str, Any]) -> bool:
    """Whether the state of charge is under the battery's lower SOC limit (minSoc, tenths)."""
    try:
        return float(data.get("electricLevel")) < float(data.get("minSoc")) / 10.0
    except (TypeError, ValueError):
        return False


def _refused(data: dict[str, Any], charging: bool) -> str:
    """Suffix for quick and manual: those modes keep their order at a bound,
    because the order is the operator's, but the status says it lands nowhere."""
    full, empty = _soc_bounds(data)
    if charging and full:
        return ", refused by device: battery full (upper limit)"
    if not charging and empty:
        return ", refused by device: battery empty (lower limit)"
    return ""


class ProtectionMixin:
    """SOC protection, waiting for price or sun, and the bounds a direction stops at."""

    # ── Safety ───────────────────────────────────────────────────────────

    async def _handle_soc_protection(self, data: dict[str, Any], mode: str) -> bool:
        """Charge from under the lower SOC bound to that bound + SOC_REARM_POINTS. Returns True when it acted."""
        if getattr(self.settings, "coordinated", False):
            self._stand_down()
            if getattr(self.settings, "role", None) == ROLE_PROTECT:
                return await self._protect_for_group(data)
            # In a battery cluster the group decides (v1.7.0): a battery
            # under its floor stops discharging there (see _bounds) instead of
            # charging from the grid on its own while the others discharge —
            # measured as 2400 W from one battery into the other for 55 s.
            # With every battery under its floor the cluster charges them all
            # (v1.7.6, role protect).
            return False
        if self._soc_protection() == SOC_PROTECT_OFF:
            self._stand_down()
            return False
        if mode in (MODE_QUICK_DISCHARGE, MODE_MANUAL):
            # Explicit user intent; protection would fight the operator.
            self._stand_down()
            return False
        if mode == MODE_STANDBY:
            # Standby means hands off, and a protection that still writes would
            # make that a lie. The device guards its own floor regardless.
            self._stand_down()
            return False

        soc = _soc_percent(data, "electricLevel", 1.0)
        floor = _soc_percent(data, "minSoc", 0.1)  # minSoc is stored in tenths
        if soc is None or floor is None:
            self._stand_down()
            return False
        # Charge from under the floor to the floor + SOC_REARM_POINTS (v1.7.5):
        # stopping at the floor itself left the battery on the edge, where it
        # swings across it. The planner does the same with a quarter at full
        # power. Once charging it carries on to the margin.
        target = floor + SOC_REARM_POINTS
        # A start needs the battery SOC_PROTECT_START_POINTS under its floor
        # (v1.8.6): the point the device drops at rest after closing is no reason.
        if soc >= target or (not self._protecting and soc > floor - SOC_PROTECT_START_POINTS):
            self._stand_down()
            return False

        if not self._protecting:
            # With Price Control (v1.7.6) the start may wait for the
            # cheapest quarter; once charging it carries on to the margin.
            self.protect_note = self._protect_waits(data, soc, floor)
            if self.protect_note is not None:
                return False

        if await self._reverse_guard(data, "charge"):
            return True

        self._protecting = True
        self.protect_note = None
        limit = self._cap(data, charging=True)
        await self._write_direction(data, AC_MODE_CHARGE, limit)
        self._set_state(
            "protecting",
            f"SOC {soc:g}% under {target:g}% (floor {floor:g}% + {SOC_REARM_POINTS}), charging"
            + (f"; {self._protect_why}" if self._protect_why else ""),
            target=limit,
            direction="charge",
        )
        return True

    def _soc_protection(self) -> str:
        """off, direct or price_control (v1.7.6); a stored switch value reads as before."""
        return soc_protection_choice(self.settings.get(OPT_SOC_PROTECTION))

    def _stand_down(self) -> None:
        """SOC protection does not act: forget a run and a wait."""
        self._protecting = False
        self._protect_since = None
        self._protect_why = None
        self.protect_note = None

    def _protect_waits(self, data: dict[str, Any], soc: float, floor: float) -> str | None:
        """With Price Control: why the charge waits, or None to charge now (v1.7.6).

        The battery waits for the cheapest period of the Price Control price
        list within SOC_PROTECT_WINDOW_H of the moment it fell under its
        floor. One period is enough: from just under the floor to 5 points
        above it is a few minutes at full power. Meanwhile it charges from
        surplus as usual and the device keeps it from discharging. Direct,
        with the reason in the status, when there is no Price Control or no
        price list, when the window is over, or when the battery sits
        SOC_PROTECT_HARD_POINTS or more under its floor. Under the fixed tariff
        (v1.8.2) every quarter costs the same, so it waits for the sun instead:
        solar surplus by day, the grid at the end of the day or after the window.
        """
        self._protect_why = None
        if self._soc_protection() != SOC_PROTECT_PRICE:
            return None
        now = dt_util.utcnow()
        if self._protect_since is None:
            self._protect_since = now
        if soc <= floor - SOC_PROTECT_HARD_POINTS:
            self._protect_why = f"direct: {SOC_PROTECT_HARD_POINTS} points or more under the floor"
            return None
        deadline = self._protect_since + timedelta(hours=SOC_PROTECT_WINDOW_H)
        if now >= deadline:
            self._protect_why = f"direct: waited {SOC_PROTECT_WINDOW_H} h"
            return None
        windows = self._solar_windows()
        if windows is not None:
            # Fixed tariff (v1.8.2): every quarter costs the same; wait for the sun.
            wait = solar_wait(self._protect_since.timestamp(), now.timestamp(), windows,
                              SOC_PROTECT_WINDOW_H * 3600)
            if wait.charge_at is None:
                self._protect_why = wait.why
                return None
            at = wait.charge_label or dt_util.as_local(dt_util.utc_from_timestamp(wait.charge_at)).strftime("%H:%M")
            head = f"SOC {soc:g}% under the floor {floor:g}%"
            if wait.daylight_at:
                return f"{head}: waits for daylight at {wait.daylight_at}, from the grid at {at} at the latest"
            return f"{head}: charges from solar, from the grid at {at} at the latest"
        periods = [p for p in self._import_periods() if p[0] < deadline and p[1] > now]
        if not periods:
            self._protect_why = "direct: no prices from Price Control"
            return None
        # The cheapest, and of equal ones the first.
        best = min(periods, key=lambda p: (round(p[2], 5), p[0]))
        if best[0] <= now:
            self._protect_why = f"cheapest price within {SOC_PROTECT_WINDOW_H} h: {best[2]:g} per kWh"
            return None
        current = next((p for p in periods if p[0] <= now < p[1]), None)
        at = dt_util.as_local(best[0]).strftime("%H:%M")
        text = f"SOC {soc:g}% under the floor {floor:g}%: charges at {at}, {best[2]:g} per kWh"
        if current is not None:
            text += f" (now {current[2]:g})"
        return text

    def _solar_windows(self) -> tuple | None:
        """The day windows of this battery's Price Control under the fixed tariff (v1.8.2)."""
        entry = getattr(self.coordinator, "config_entry", None)
        if entry is None:
            return None
        from .pricecontrol import solar_windows_for

        return solar_windows_for(self.coordinator.hass, entry.entry_id, False)

    def _import_periods(self) -> list[tuple[datetime, datetime, float]]:
        """(start, end, import price) ahead, from the Price Control entry of this battery."""
        entry = getattr(self.coordinator, "config_entry", None)
        if entry is None:
            return []
        for item in self.coordinator.hass.data.get(DOMAIN, {}).values():
            if getattr(item, "battery_entry_id", None) == entry.entry_id and hasattr(item, "import_periods"):
                try:
                    return list(item.import_periods())
                except Exception:  # noqa: BLE001 - a price list must not stop protection
                    _LOGGER.debug("No import periods from price control", exc_info=True)
                    return []
        return []

    async def _protect_for_group(self, data: dict[str, Any]) -> bool:
        """The cluster has every battery under its floor and charges them (v1.7.6)."""
        if await self._reverse_guard(data, "charge"):
            return True
        limit = self._cap(data, charging=True)
        await self._write_direction(data, AC_MODE_CHARGE, limit)
        soc = _soc_percent(data, "electricLevel", 1.0)
        floor = _soc_percent(data, "minSoc", 0.1)
        to = f" to {floor + SOC_REARM_POINTS:g}%" if floor is not None else ""
        self._set_state(
            "protecting",
            f"cluster: every battery under its SOC floor, SOC {soc if soc is not None else '?'}%, charging{to}",
            target=limit,
            direction="charge",
        )
        return True

    def _bounds(self, data: dict[str, Any]) -> tuple[bool, bool]:
        """(full, empty) as this battery must respect them.

        On its own: the device's socLimit, as before. In a battery cluster a
        battery under its SOC floor counts as empty as well: its own SOC
        protection stands down there, so the floor is held by not discharging.
        """
        full, empty = _soc_bounds(data)
        # Kept past the device's own flag (v1.7.5): empty until minSoc +
        # SOC_REARM_POINTS, full until socSet − SOC_REARM_POINTS. The device
        # reopens a direction one point away from its bound, and the battery
        # then swings across it every few seconds.
        soc = _soc_percent(data, "electricLevel", 1.0)
        floor = _soc_percent(data, "minSoc", 0.1)
        ceiling = _soc_percent(data, "socSet", 0.1)
        if empty:
            self._empty_latch = True
        elif self._empty_latch and soc is not None and floor is not None and soc >= floor + SOC_REARM_POINTS:
            self._empty_latch = False
        if full:
            self._full_latch = True
        elif self._full_latch and soc is not None and ceiling is not None and soc <= ceiling - SOC_REARM_POINTS:
            self._full_latch = False
        full = full or self._full_latch
        empty = empty or self._empty_latch
        if getattr(self.settings, "coordinated", False) and _below_floor(data):
            empty = True
        return full, empty

    def _blocked(self, direction: str) -> str:
        """Why a direction is closed, with where it opens again while held past the device."""
        text = SOC_BLOCKED[direction]
        data = self.coordinator.data or {}
        if direction == "discharge" and self._empty_latch:
            floor = _soc_percent(data, "minSoc", 0.1)
            if floor is not None:
                text += f", discharges again from {floor + SOC_REARM_POINTS:g} %"
        if direction == "charge" and self._full_latch:
            ceiling = _soc_percent(data, "socSet", 0.1)
            if ceiling is not None:
                text += f", charges again below {ceiling - SOC_REARM_POINTS:g} %"
        return text

    def _rest_limits(self, data: dict[str, Any], direction: str = "none") -> list[tuple[str, int]]:
        """Both limits to 0, except a small charge limit the device set itself
        at its lower bound (its own SOC protection): writing 0 against it every
        cycle only fights the device (v1.7.5). A limit of that size that this
        integration wrote itself is not the device's and goes to 0 (v1.8.7):
        a battery whose small charge was ended by its group would otherwise
        keep charging at its lower bound."""
        items = [("inputLimit", 0), ("outputLimit", 0)]
        if direction != "charge" and _soc_bounds(data)[1]:
            held = _as_int(data.get("inputLimit")) or 0
            ours = any(w[0] == held for w in self._last_written.get("inputLimit", ()))
            if 0 < held <= DEVICE_SELF_CHARGE_W and not ours:
                items = [("outputLimit", 0)]
        return items
