"""Operation-mode controller for the Zendure RestAPI integration.

The controller runs once per coordinator poll, which means it always acts on
the readings that arrived in that same cycle. A controller on its own timer
would sooner or later correct twice for the same deviation, and that is how
charge/discharge oscillation gets built.

Three ideas from the community zenSDK automation are kept deliberately:

* The first step towards a new direction is undershot (factor 0.75). The
  device's actual response is not yet known, so committing the full correction
  invites overshoot. Once the direction holds, the controller balances at 1.00.
* A new direction only starts while the battery is genuinely idle (deadband),
  and only after a hold of two cycles following a direction change.
* If the meter stops reporting, everything stops. A closed loop with no
  feedback is worse than no loop at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.util import dt as dt_util

from .api import ZendureApiError
from .const import (
    AC_MODE_CHARGE,
    AC_MODE_DISCHARGE,
    CONTROL_DEADBAND,
    CONTROL_DIRECTION_HOLD,
    CONTROL_FACTOR_BALANCE,
    CONTROL_FACTOR_START,
    CONTROL_MIN_STEP,
    MODE_MANUAL,
    MODE_QUICK_CHARGE,
    MODE_QUICK_DISCHARGE,
    MODE_SMART_CHARGE,
    MODE_SMART_DISCHARGE,
    MODE_SMART_MATCHING,
    MODE_STANDBY,
    OPT_CHARGE_BUFFER,
    OPT_DISCHARGE_BUFFER,
    OPT_MANUAL_POWER,
    OPT_MAX_CHARGE_POWER,
    OPT_MAX_DISCHARGE_POWER,
    OPT_METER_INVERT,
    OPT_OPERATION_MODE,
    OPT_SOC_PROTECTION,
    OPT_STANDBY_DELAY,
    OPT_START_CHARGE_BELOW,
    OPT_START_DISCHARGE_ABOVE,
    SMART_MODES,
)
from .coordinator import ZendureCoordinator
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)


@dataclass
class ControllerState:
    """What the controller last did, and why. Surfaced as a sensor."""

    status: str = "idle"
    reason: str = "not started"
    target_power: int = 0
    direction: str = "none"
    meter_power: float | None = None
    battery_power: float | None = None
    last_run: Any = None
    blocked: bool = False
    attributes: dict[str, Any] = field(default_factory=dict)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class ZendureController:
    """Turns an operation mode into device writes."""

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        settings: ZendureSettings,
    ) -> None:
        self.coordinator = coordinator
        self.settings = settings
        self.state = ControllerState()
        self.meter: ZendureCoordinator | None = None

        self._busy = False
        self._hold = 0
        self._last_direction = "none"
        self._idle_since: Any = None

    # ── Wiring ───────────────────────────────────────────────────────────

    def attach_meter(self, meter: ZendureCoordinator | None) -> None:
        """Link the meter coordinator that provides the grid reading."""
        self.meter = meter
        if meter is None:
            _LOGGER.info(
                "No Zendure meter linked. Smart modes need a grid reading and "
                "will stay blocked until a meter entry is added."
            )

    # ── Main entry point ─────────────────────────────────────────────────

    async def async_run(self) -> None:
        """Execute one control cycle. Never raises."""
        if self._busy:
            return
        self._busy = True
        try:
            await self._run()
        except ZendureApiError as err:
            self.state.status = "error"
            self.state.reason = str(err)
            _LOGGER.warning("Controller write failed: %s", err)
        except Exception:  # noqa: BLE001 - a control loop must not kill the poll
            _LOGGER.exception("Unexpected controller failure")
        finally:
            self.state.last_run = dt_util.utcnow()
            self._busy = False

    async def _run(self) -> None:
        data = self.coordinator.data or {}
        if not data:
            self._set_state("waiting", "no device data")
            return

        mode = self.settings.get(OPT_OPERATION_MODE)

        # SOC protection outranks every mode: a pack below its own floor is
        # charged regardless of what the strategy would prefer.
        if await self._handle_soc_protection(data, mode):
            return

        if mode == MODE_STANDBY:
            await self._apply_standby(data)
        elif mode == MODE_MANUAL:
            await self._apply_manual(data)
        elif mode == MODE_QUICK_CHARGE:
            await self._apply_quick(data, charging=True)
        elif mode == MODE_QUICK_DISCHARGE:
            await self._apply_quick(data, charging=False)
        elif mode in SMART_MODES:
            await self._apply_smart(data, mode)
        else:
            self._set_state("idle", f"unknown mode {mode}")

    # ── Safety ───────────────────────────────────────────────────────────

    async def _handle_soc_protection(self, data: dict[str, Any], mode: str) -> bool:
        """Charge back to the lower SOC bound. Returns True when it acted."""
        if not self.settings.get_bool(OPT_SOC_PROTECTION):
            return False
        if mode in (MODE_QUICK_DISCHARGE, MODE_MANUAL):
            # Explicit user intent; protection would fight the operator.
            return False

        soc = data.get("electricLevel")
        floor = data.get("minSoc")
        if soc is None or floor is None:
            return False
        try:
            # minSoc is stored in tenths of a percent.
            if float(soc) >= float(floor) / 10.0:
                return False
        except (TypeError, ValueError):
            return False

        limit = self.settings.get_int(OPT_MAX_CHARGE_POWER)
        await self._write_direction(data, AC_MODE_CHARGE, limit)
        self._set_state(
            "protecting",
            f"SOC {soc}% below floor {float(floor) / 10.0}%",
            target=limit,
            direction="charge",
        )
        return True

    # ── Modes ────────────────────────────────────────────────────────────

    async def _apply_standby(self, data: dict[str, Any]) -> None:
        """Zero both limits, then drop to flash after the idle delay."""
        await self._write("inputLimit", 0, data)
        await self._write("outputLimit", 0, data)

        delay = self.settings.get_int(OPT_STANDBY_DELAY)
        now = dt_util.utcnow()
        if self._idle_since is None:
            self._idle_since = now

        idle_minutes = (now - self._idle_since).total_seconds() / 60.0
        if delay > 0 and idle_minutes >= delay and data.get("smartMode") == 1:
            # Leaving RAM mode lets the device settle its own state and cuts
            # standby draw. Writes are rare here, so flash wear is not a worry.
            await self._write("smartMode", 0, data)
            self._set_state("standby", f"idle {int(idle_minutes)} min, storage to flash")
            return

        self._set_state("standby", "limits at zero")

    async def _apply_manual(self, data: dict[str, Any]) -> None:
        """Follow the manual power setpoint; its sign picks the direction."""
        power = self.settings.get_int(OPT_MANUAL_POWER)
        if power == 0:
            await self._write("inputLimit", 0, data)
            await self._write("outputLimit", 0, data)
            self._set_state("manual", "setpoint 0 W")
            return

        charging = power > 0
        cap = self.settings.get_int(
            OPT_MAX_CHARGE_POWER if charging else OPT_MAX_DISCHARGE_POWER
        )
        limit = int(_clamp(abs(power), 0, cap))
        await self._write_direction(
            data, AC_MODE_CHARGE if charging else AC_MODE_DISCHARGE, limit
        )
        self._set_state(
            "manual",
            f"setpoint {power} W",
            target=limit,
            direction="charge" if charging else "discharge",
        )

    async def _apply_quick(self, data: dict[str, Any], charging: bool) -> None:
        """Charge or discharge at the configured maximum, ignoring the meter."""
        cap = self.settings.get_int(
            OPT_MAX_CHARGE_POWER if charging else OPT_MAX_DISCHARGE_POWER
        )
        await self._write_direction(
            data, AC_MODE_CHARGE if charging else AC_MODE_DISCHARGE, cap
        )
        self._set_state(
            "quick",
            f"{'charging' if charging else 'discharging'} at maximum",
            target=cap,
            direction="charge" if charging else "discharge",
        )

    async def _apply_smart(self, data: dict[str, Any], mode: str) -> None:
        """Track the grid meter towards zero exchange."""
        grid = self._meter_power()
        if grid is None:
            # No trustworthy feedback: stop rather than guess.
            await self._write("inputLimit", 0, data)
            await self._write("outputLimit", 0, data)
            self._set_state("blocked", "no meter reading", blocked=True)
            return

        own = self._battery_power(data)
        self.state.meter_power = grid
        self.state.battery_power = own

        allow_charge = mode in (MODE_SMART_MATCHING, MODE_SMART_CHARGE)
        allow_discharge = mode in (MODE_SMART_MATCHING, MODE_SMART_DISCHARGE)

        start_discharge = self.settings.get_int(OPT_START_DISCHARGE_ABOVE)
        start_charge = self.settings.get_int(OPT_START_CHARGE_BELOW)

        idle = abs(own) < CONTROL_DEADBAND
        if self._hold > 0:
            self._hold -= 1
            self._set_state("holding", f"direction hold, {self._hold} cycles left")
            return

        # A direction that is already running keeps being balanced, whatever the
        # grid does. The start thresholds gate *starting* a direction, not
        # continuing one — otherwise a load that disappears would leave the
        # battery discharging into the grid, with the controller waiting for an
        # idle state that can never arrive because nothing winds the limit down.
        if self._last_direction == "discharge" and not idle:
            await self._smart_step(data, charging=False, grid=grid, own=own, idle=idle)
            return
        if self._last_direction == "charge" and not idle:
            await self._smart_step(data, charging=True, grid=grid, own=own, idle=idle)
            return

        if idle and self._last_direction != "none":
            self._last_direction = "none"

        if grid > start_discharge and allow_discharge:
            await self._smart_step(data, charging=False, grid=grid, own=own, idle=idle)
        elif grid < start_charge and allow_charge:
            await self._smart_step(data, charging=True, grid=grid, own=own, idle=idle)
        else:
            self._set_state(
                "tracking",
                f"grid {grid:.0f} W within thresholds",
                direction="none",
            )

    async def _smart_step(
        self,
        data: dict[str, Any],
        charging: bool,
        grid: float,
        own: float,
        idle: bool,
    ) -> None:
        """Compute and apply one correction."""
        direction = "charge" if charging else "discharge"
        starting = self._last_direction != direction

        if starting and not idle:
            # A direction change while the battery is still moving power would
            # be acting on a reading that already includes the old direction.
            self._set_state(
                "waiting",
                f"battery at {own:.0f} W, waiting for idle before {direction}",
            )
            return

        factor = CONTROL_FACTOR_START if starting else CONTROL_FACTOR_BALANCE
        buffer = self.settings.get_int(
            OPT_CHARGE_BUFFER if charging else OPT_DISCHARGE_BUFFER
        )
        cap = self.settings.get_int(
            OPT_MAX_CHARGE_POWER if charging else OPT_MAX_DISCHARGE_POWER
        )

        if charging:
            # grid is negative (export); soak up the surplus minus the buffer.
            raw = (-grid - own - buffer) * factor
        else:
            # grid is positive (import); cover it minus the buffer.
            raw = (grid + own - buffer) * factor

        target = int(_clamp(raw, 0, cap))

        key = "inputLimit" if charging else "outputLimit"
        current = data.get(key)
        try:
            current_val = int(current)
        except (TypeError, ValueError):
            current_val = -1

        if abs(target - current_val) < CONTROL_MIN_STEP and not starting:
            self._set_state(
                "tracking",
                f"{direction} at {current_val} W, within {CONTROL_MIN_STEP} W",
                target=current_val,
                direction=direction,
            )
            return

        await self._write_direction(
            data, AC_MODE_CHARGE if charging else AC_MODE_DISCHARGE, target, key=key
        )

        if starting:
            self._hold = CONTROL_DIRECTION_HOLD
        self._last_direction = direction if target > 0 else "none"
        self._idle_since = None

        self._set_state(
            "starting" if starting else "balancing",
            f"grid {grid:.0f} W, battery {own:.0f} W, factor {factor}",
            target=target,
            direction=direction,
        )

    # ── Readings ─────────────────────────────────────────────────────────

    def _meter_power(self) -> float | None:
        """Signed grid power from the linked meter: positive is import."""
        if self.meter is None:
            return None
        if self.meter.last_update_success is False:
            return None

        value = (self.meter.data or {}).get("total_power")
        if value is None:
            return None
        try:
            power = float(value)
        except (TypeError, ValueError):
            return None

        if self.settings.get_bool(OPT_METER_INVERT):
            power = -power
        return power

    def _battery_power(self, data: dict[str, Any]) -> float:
        """Signed battery power: positive while discharging into the house.

        Measured on the DC side, because the AC-side fields have been observed
        to mirror the setpoint rather than the actual flow. That costs a few
        percent of conversion loss in the estimate, which the closed loop
        corrects on the following cycle.
        """
        power = data.get("pack1.power")
        current = data.get("pack1.batcur")
        try:
            magnitude = abs(float(power))
        except (TypeError, ValueError):
            return 0.0

        try:
            raw = int(current)
            if raw > 0x7FFF:
                raw -= 0x10000
            charging = raw > 0
        except (TypeError, ValueError):
            charging = data.get("packState") == 1

        return -magnitude if charging else magnitude

    # ── Writes ───────────────────────────────────────────────────────────

    async def _write_direction(
        self,
        data: dict[str, Any],
        ac_mode: int,
        limit: int,
        key: str | None = None,
    ) -> None:
        """Ensure RAM storage, the right direction, and the limit."""
        # Frequent limit writes must not hit flash.
        if data.get("smartMode") != 1:
            await self._write("smartMode", 1, data)

        if data.get("acMode") != ac_mode:
            await self._write("acMode", ac_mode, data)

        target_key = key or ("inputLimit" if ac_mode == AC_MODE_CHARGE else "outputLimit")
        other_key = "outputLimit" if target_key == "inputLimit" else "inputLimit"

        if data.get(other_key) not in (0, "0"):
            await self._write(other_key, 0, data)
        await self._write(target_key, limit, data)

    async def _write(self, key: str, value: Any, data: dict[str, Any]) -> None:
        """Write one property, skipping no-ops."""
        current = data.get(key)
        try:
            if current is not None and int(current) == int(value):
                return
        except (TypeError, ValueError):
            pass
        await self.coordinator.api.async_write_property(key, value)
        data[key] = value

    # ── State ────────────────────────────────────────────────────────────

    def _set_state(
        self,
        status: str,
        reason: str,
        target: int = 0,
        direction: str = "none",
        blocked: bool = False,
    ) -> None:
        self.state.status = status
        self.state.reason = reason
        self.state.target_power = target
        self.state.direction = direction
        self.state.blocked = blocked
        self.state.attributes = {
            "reason": reason,
            "direction": direction,
            "target_power": target,
            "meter_power": self.state.meter_power,
            "battery_power": self.state.battery_power,
            "hold_cycles": self._hold,
            "mode": self.settings.get(OPT_OPERATION_MODE),
        }
