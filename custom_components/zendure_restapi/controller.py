"""Operation-mode controller for the Zendure RestAPI integration.

Two loops, because the two decisions have different natural tempos.

**The mode loop** runs once per battery poll, so it always acts on readings
from that same cycle. It owns everything that needs the device to be believed:
which operation mode applies, which direction runs, when a direction may start,
stop or reverse, SOC protection, and the power ceilings. Those questions change
on the scale of minutes, and polling the battery faster to answer them sooner
buys nothing.

**The trim loop** runs once per meter sample, and may do exactly one thing:
adjust the limit *within* the direction that is already running. It never
starts a direction, never reverses one, and never overrules a mode. That
restriction is not a safety compromise, it is the fix — see below.

Before v1.1.0 there was only the mode loop, so a meter reporting every second
was read once every ten. Nine samples out of ten went in the bin, and the one
that survived was whichever moment the poll happened to land on. A 1700 W
coffee machine was measured producing a swing of +1750, -1700, +1550, -1500 W
across 80 seconds, exporting 12.7 kWh-thousandths that should never have left
the house. The reversals were not wrong decisions: by the time the mode loop
looked, the grid genuinely had been sitting at -1700 W for several seconds, and
charging was the correct response to that reading. The fault was the lateness,
not the logic. A loop that trims every second never reaches the state in which
that reversal looks reasonable, which is why forbidding reversals in the fast
loop costs nothing.

The trim loop works incrementally: ``limit + (grid - buffer) x factor``. The
mode loop works absolutely: ``(grid + battery - buffer) x factor``. The two are
the same equation when the device is holding the limit it was given, and they
fail differently when it is not. The absolute form needs a fresh battery
reading, which only the mode loop has. The incremental form needs only the
limit last written, which the controller knows exactly because it wrote it —
but it drifts if the device silently fails to follow. Hence the pairing: the
fast loop trims, and every battery poll resynchronises against measured truth.

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

from datetime import datetime, timedelta

import logging
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import callback
from homeassistant.util import dt as dt_util

from .api import ZendureApiError
from .const import (
    AC_MODE_CHARGE,
    AC_MODE_DISCHARGE,
    CONTROL_DEADBAND,
    CONTROL_DIRECTION_HOLD_SECONDS,
    CONTROL_FACTOR_BALANCE,
    CONTROL_FACTOR_START,
    CONTROL_MIN_STEP,
    CONTROL_MIN_STEP_FAST,
    METER_MAX_AGE,
    MODE_MANUAL,
    MODE_QUICK_CHARGE,
    MODE_QUICK_DISCHARGE,
    MODE_SMART_CHARGE,
    MODE_SMART_DISCHARGE,
    MODE_SMART_MATCHING,
    MODE_STANDBY,
    OPT_CHARGE_BUFFER,
    OPT_DISCHARGE_BUFFER,
    OPT_DIRECTION_DELAY,
    OPT_MANUAL_POWER,
    OPT_MIN_CHARGE_POWER,
    OPT_MIN_DISCHARGE_POWER,
    OPT_MAX_CHARGE_POWER,
    OPT_MAX_DISCHARGE_POWER,
    OPT_OPERATION_MODE,
    OPT_SOC_PROTECTION,
    OPT_START_CHARGE_AT,
    OPT_START_DISCHARGE_AT,
    OPT_TRIM_FACTOR,
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



# Keys through which the device publishes its own power ceiling.
DEVICE_CAP_KEY = {True: "chargeMaxLimit", False: "inverseMaxPower"}


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
        self._idle_flag = True
        self._commanded = 0
        # Whether the limit currently on the device was set by this controller.
        # Only what we set ourselves may be cleared on the way to standby.
        self._owns_limits = False
        # When a reversal may complete. Held as a moment rather than a countdown
        # of polls: what the inverter needs is time, and a number of polls means
        # something different at every interval.
        self._reverse_until: datetime | None = None
        self._last_direction = "none"
        self._idle_since: Any = None

        # ── Trim loop ────────────────────────────────────────────────
        # The single contract point between the two loops. The mode loop sets
        # this to a direction when it has established one it is balancing, and
        # every other path leaves it at None. The trim loop acts if and only if
        # it holds a direction, so a mode, a reversal, a hold or a protection
        # cycle disables trimming by simply not enabling it. Re-deriving the
        # conditions inside the fast loop would be a second answer to the same
        # question, which is exactly the shape of bug this codebase keeps
        # finding.
        self._trim_direction: str | None = None
        self._trim_busy = False
        self._fast_write_at: datetime | None = None
        self._trim_writes = 0
        self._unsub_meter: Any = None

    # ── Wiring ───────────────────────────────────────────────────────────

    def attach_meter(self, meter: ZendureCoordinator | None) -> None:
        """Link the meter coordinator and subscribe the trim loop to it.

        Linking runs again on every setup and unload, because the order in
        which the battery and the meter are configured is not fixed. Any
        previous subscription is therefore dropped first: subscribing twice
        would run the trim loop twice per sample, which is not merely wasteful
        but doubles every correction.
        """
        self.detach_meter()
        self.meter = meter
        if meter is None:
            _LOGGER.info(
                "No Zendure meter linked. Smart modes need a grid reading and "
                "will stay blocked until a meter entry is added."
            )
            return

        @callback
        def _on_meter() -> None:
            meter.hass.async_create_task(self.async_trim())

        self._unsub_meter = meter.async_add_listener(_on_meter)
        _LOGGER.debug(
            "Trim loop subscribed to the meter coordinator (%ss interval)",
            meter.update_interval.total_seconds() if meter.update_interval else "?",
        )

    def detach_meter(self) -> None:
        """Drop the meter subscription, if there is one."""
        if self._unsub_meter is not None:
            self._unsub_meter()
            self._unsub_meter = None
        self._trim_direction = None

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
        # Trimming is re-enabled only by the branch that establishes a healthy
        # running direction. Clearing it here means every early return below —
        # no data, protection, standby, manual, quick, holding, blocked — stops
        # the trim loop without having to remember to say so.
        self._trim_direction = None

        data = self.coordinator.data or {}
        if not data:
            self._set_state("waiting", "no device data")
            return

        mode = self.settings.get(OPT_OPERATION_MODE)


        # Refresh the readings on every cycle, in every mode. Updating them only
        # on the smart path left quick, manual and standby showing whatever the
        # loop last saw, which is worse than showing nothing: a stale negative
        # battery power reads as "charging" while the device is discharging.
        self.state.meter_power = self._meter_power()
        self.state.battery_power = self._battery_power(data)

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

    # ── Trim loop ────────────────────────────────────────────────────────

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

    async def _trim(self) -> None:
        """Adjust the running limit against the meter, within one direction."""
        direction = self._trim_direction
        if direction is None:
            return

        data = self.coordinator.data or {}
        if not data:
            return

        grid = self._meter_power()
        if grid is None:
            # No trustworthy feedback. Winding down here would be a direction
            # decision, which is not this loop's to make; the mode loop already
            # clears both limits on a dead meter. Standing still until it runs
            # is the smaller of the two wrongs.
            self._trim_direction = None
            return

        charging = direction == "charge"
        key = "inputLimit" if charging else "outputLimit"
        try:
            current = int(data.get(key) or 0)
        except (TypeError, ValueError):
            return
        if current <= 0:
            # Nothing is running any more; the mode loop decides what is next.
            self._trim_direction = None
            return

        buffer = self.settings.get_int(
            OPT_CHARGE_BUFFER if charging else OPT_DISCHARGE_BUFFER
        )

        # One error term for both directions. Both buffers mean the same thing:
        # how many watts of grid import to aim for. The sign flips because a
        # charge limit is a magnitude in the opposite direction, not because
        # the two are chasing different targets.
        error = (grid - buffer) * self._trim_factor()
        target = int(_clamp(
            current - error if charging else current + error,
            0,
            self._cap(data, charging),
        ))

        # The floor applies here for the same reason it applies in the mode
        # loop: trimming below it would only be undone on the next poll, and
        # the two loops fighting over the same watt is worse than either
        # setting being slightly off.
        floor = self.settings.get_int(
            OPT_MIN_CHARGE_POWER if charging else OPT_MIN_DISCHARGE_POWER
        )
        if floor > 0 and 0 < target < floor:
            target = int(min(floor, self._cap(data, charging)))

        if abs(target - current) < CONTROL_MIN_STEP_FAST:
            return

        await self._write(key, target, data)
        self._fast_write_at = dt_util.utcnow()
        self._trim_writes += 1

        if target <= 0:
            # Zero is the floor of this loop, not a reversal: the limit is
            # released, the direction is not changed and acMode is left alone.
            # Restarting is a direction decision, so the mode loop takes it
            # from here. This is the branch that ends the coffee-machine
            # scenario without ever commanding the opposite direction.
            self._owns_limits = False
            self._trim_direction = None

        self.state.meter_power = grid
        _LOGGER.debug(
            "trim | %s %sW -> %sW | grid=%.0fW buffer=%sW",
            direction, current, target, grid, buffer,
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

    # ── Safety ───────────────────────────────────────────────────────────

    async def _handle_soc_protection(self, data: dict[str, Any], mode: str) -> bool:
        """Charge back to the lower SOC bound. Returns True when it acted."""
        if not self.settings.get_bool(OPT_SOC_PROTECTION):
            return False
        if mode in (MODE_QUICK_DISCHARGE, MODE_MANUAL):
            # Explicit user intent; protection would fight the operator.
            return False
        if mode == MODE_STANDBY:
            # Standby means hands off, and a protection that still writes would
            # make that a lie. The device guards its own floor regardless.
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

        if await self._reverse_guard(data, "charge"):
            return True

        limit = self._cap(data, charging=True)
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
        """Hands off. The controller reads and reports, and writes nothing.

        This matters when the device runs its own energy manager. A standby
        that keeps forcing both limits to zero does not stand by at all: it
        overrules the on-device manager on every poll, the manager restores its
        setpoint a second later, and the pair oscillate at the polling period.

        The one exception is a single cleanup on entering standby, and only for
        a limit this controller set itself. Leaving that behind would not be
        restraint, it would be abandoning a command mid-flight.
        """
        if self._owns_limits:
            await self._write("inputLimit", 0, data)
            await self._write("outputLimit", 0, data)
            self._owns_limits = False
            self._last_direction = "none"
            self._set_state("standby", "released own limits, now passive")
            return

        self._reverse_until = None
        self._set_state("standby", "passive, writing nothing")


    async def _apply_manual(self, data: dict[str, Any]) -> None:
        """Follow the manual power setpoint; its sign picks the direction."""
        power = self.settings.get_int(OPT_MANUAL_POWER)
        if power == 0:
            await self._write("inputLimit", 0, data)
            await self._write("outputLimit", 0, data)
            self._set_state("manual", "setpoint 0 W")
            return

        charging = power > 0
        if await self._reverse_guard(data, "charge" if charging else "discharge"):
            return

        cap = self._cap(data, charging)
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
        if await self._reverse_guard(data, "charge" if charging else "discharge"):
            return

        cap = self._cap(data, charging)
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

        # The device is the single source of truth for which direction is
        # running. Adopting it only when the controller has no memory left a
        # gap: a stale record could disagree with the device, and the two are
        # read by different pieces of logic. The wind-down branches below trust
        # the record, while _smart_step derives "is this a direction change"
        # from the device, so a disagreement deadlocked them against each
        # other — observed as waiting for idle before discharge while the
        # battery was charging at 3 kW, indefinitely.
        adopted = self._device_direction(data)
        if adopted != "none" and adopted != self._last_direction:
            _LOGGER.debug(
                "adopting device direction %s over remembered %s",
                adopted, self._last_direction,
            )
            self._last_direction = adopted
            self._owns_limits = True

        # Frequent limit writes are coming; keep them out of flash. The device
        # reverts smartMode to 0 across a reboot, so this cannot be assumed.
        if data.get("smartMode") != 1:
            await self._write("smartMode", 1, data)

        allow_charge = mode in (MODE_SMART_MATCHING, MODE_SMART_CHARGE)
        allow_discharge = mode in (MODE_SMART_MATCHING, MODE_SMART_DISCHARGE)

        # Both thresholds are a distance from zero, expressed as a positive
        # number of watts. Which side of zero is in the name, not in the sign:
        # one counts import, the other export. Keeping both positive makes the
        # pair read alike and, on a slider, look alike.
        start_discharge = self.settings.get_int(OPT_START_DISCHARGE_AT)
        start_charge = -self.settings.get_int(OPT_START_CHARGE_AT)

        idle = self._is_idle(data, own)

        # The settling period exists so a reading that still contains the old
        # direction is not treated as a fresh deviation. It is not a reason to
        # ignore the grid swinging hard the other way: that is not noise, it is
        # the load disappearing, and every cycle spent holding is a cycle spent
        # pushing power the wrong way across the meter.
        if self._hold > 0 and self._wrong_way(grid):
            self._hold = 0

        if self._hold > 0:
            # Report first, then count down. The other order announces "0 left"
            # on a cycle it still skips, which reads as a contradiction.
            remaining = self._hold
            self._hold -= 1
            unit = "cycle" if remaining == 1 else "cycles"
            self._set_state(
                "holding", f"settling after direction change, {remaining} {unit} to go"
            )
            return

        # A direction the mode forbids is wound down rather than continued.
        # Adopting whatever the device was doing is what lets a running
        # direction be reduced to zero; it must not also keep that direction
        # alive in a mode that rules it out. Observed as smart_charge_only
        # discharging at 200 W because the device happened to be discharging
        # when the mode was selected.
        forbidden = (
            (self._last_direction == "discharge" and not allow_discharge)
            or (self._last_direction == "charge" and not allow_charge)
        )
        if forbidden:
            await self._write("inputLimit", 0, data)
            await self._write("outputLimit", 0, data)
            self._owns_limits = False
            released = self._last_direction
            self._last_direction = "none"
            self._set_state(
                "released",
                f"{released} is not allowed in {mode}, limits cleared",
                direction="none",
            )
            return

        # A direction that is already running keeps being balanced, whatever the
        # grid does. The start thresholds gate *starting* a direction, not
        # continuing one — otherwise a load that disappears would leave the
        # battery discharging into the grid, with the controller waiting for an
        # idle state that can never arrive because nothing winds the limit down.
        if self._last_direction == "discharge" and not idle:
            await self._smart_step(
                data, charging=False, grid=grid, own=own, idle=idle,
            )
            return
        if self._last_direction == "charge" and not idle:
            await self._smart_step(
                data, charging=True, grid=grid, own=own, idle=idle,
            )
            return

        # Idle means a direction change is safe, not that no direction is
        # running: a limit of 30 W sits inside the deadband while the device is
        # still charging. Clearing the record here fought the reconciliation at
        # the top of this method, which restored it from the device on the very
        # next cycle — 98 times in 100 minutes on live hardware. The device
        # decides when a direction has ended, by reporting both limits at zero.
        if self._device_direction(data) == "none":
            self._last_direction = "none"

        if grid > start_discharge and allow_discharge:
            await self._smart_step(
                data, charging=False, grid=grid, own=own, idle=idle,
            )
        elif grid < start_charge and allow_charge:
            await self._smart_step(
                data, charging=True, grid=grid, own=own, idle=idle,
            )
        else:
            # Three different situations end up here and they are not the same
            # thing to read on a dashboard. The grid can be inside both start
            # thresholds, which is the quiet case the old wording described. Or
            # it is well past a threshold and the direction that would answer it
            # is ruled out by the mode. Reporting the second as "within
            # thresholds" states something the numbers visibly contradict, on
            # the one entity whose job is to explain why the battery is doing
            # what it does. Observed as "grid -137 W within thresholds" with a
            # 5 W start threshold, in smart_discharge_only.
            if grid > start_discharge and not allow_discharge:
                reason = f"grid {grid:.0f} W, discharging not allowed in {mode}"
            elif grid < start_charge and not allow_charge:
                reason = f"grid {grid:.0f} W, charging not allowed in {mode}"
            else:
                reason = f"grid {grid:.0f} W within thresholds"
            self._set_state(
                "tracking",
                reason,
                direction=self._last_direction,
            )

    def _wrong_way(self, grid: float) -> bool:
        """Whether the grid has crossed zero against the running direction."""
        if self._last_direction == "discharge":
            return grid < 0
        if self._last_direction == "charge":
            return grid > 0
        return False

    def _hold_cycles(self) -> int:
        """How many polls make up the settling time, at the current interval."""
        interval = self.coordinator.update_interval
        seconds = interval.total_seconds() if interval else 10.0
        if seconds <= 0:
            return 1
        return max(1, round(CONTROL_DIRECTION_HOLD_SECONDS / seconds))

    async def _reverse_guard(self, data: dict[str, Any], wanted: str) -> bool:
        """Hold a reversal for the configured time. True means: wait.

        Some inverters dislike going straight from full charge to full
        discharge. The smart modes already wind a direction down before
        starting the other one, but the quick and manual modes write their
        target immediately, so a mode switch could reverse several kilowatts
        within a single cycle.

        The pause is timed rather than counted in polls, and it starts when the
        opposite limit is actually cleared rather than when the mode changes —
        otherwise it would elapse while the old direction is still running.
        """
        delay = self.settings.get_int(OPT_DIRECTION_DELAY)
        if delay <= 0:
            self._reverse_until = None
            return False

        now = dt_util.utcnow()

        # A pause already under way outranks the device state. Clearing the
        # limits is the first thing the pause does, which leaves the device
        # reporting no direction at all — reading that as "nothing to reverse
        # from" would end the pause on the very next cycle.
        if self._reverse_until is None:
            current = self._device_direction(data)
            if current in ("none", wanted):
                return False

            await self._write("inputLimit", 0, data)
            await self._write("outputLimit", 0, data)
            self._owns_limits = False
            self._last_direction = "none"
            self._reverse_until = now + timedelta(seconds=delay)
            _LOGGER.debug(
                "reversal %s -> %s: cleared both limits, pausing %ss",
                current, wanted, delay,
            )

        remaining = (self._reverse_until - now).total_seconds()
        if remaining > 0:
            self._set_state(
                "reversing",
                f"pausing {remaining:.0f}s before {wanted}",
                direction="none",
            )
            return True

        self._reverse_until = None
        _LOGGER.debug("reversal pause elapsed, %s may start", wanted)
        return False

    def _device_direction(self, data: dict[str, Any]) -> str:
        """Which direction the device is actually set to, right now.

        Internal memory of the last direction is not the same thing. After a
        Home Assistant restart, an integration reload or a mode switch, that
        memory is empty while the device carries on with the limit it was last
        given. Treating that as "no direction" makes the next cycle look like a
        fresh start, which then waits for an idle state the device cannot reach
        while it is still executing that very limit.
        """
        def as_int(key: str) -> int:
            try:
                return int(data.get(key) or 0)
            except (TypeError, ValueError):
                return 0

        if as_int("outputLimit") > 0:
            return "discharge"
        if as_int("inputLimit") > 0:
            return "charge"
        return "none"

    def _is_idle(self, data: dict[str, Any], own: float) -> bool:
        """Whether a direction change is safe right now.

        Two tests, and the first one carries the weight. If both limits are at
        zero the controller is not commanding anything, so whatever the pack is
        doing is its own standby behaviour and a direction change cannot
        conflict with an outstanding command. Only when a limit is actually set
        does the measured power decide.
        """
        commanded = 0
        for key in ("inputLimit", "outputLimit"):
            try:
                commanded = max(commanded, int(data.get(key) or 0))
            except (TypeError, ValueError):
                pass
        self._commanded = commanded

        # The measured half of this test can be older than it looks. The mode
        # loop runs straight after its own poll, so `own` is normally fresh —
        # but the trim loop can write while that poll is still in flight, and
        # the payload then describes the device from before that write. The
        # window is short, a few hundred milliseconds, and it is entered once
        # per second, so it is not rare.
        #
        # What follows from an undetected stale reading is not cosmetic: `own`
        # near zero reads as idle, idle permits a direction change, and the
        # reversal is then written into an inverter that is running. Treating
        # the reading as "not idle" costs one cycle of patience.
        if self._fast_write_at is not None:
            started = self.coordinator.fetch_started_at
            if started is not None and self._fast_write_at >= started:
                self._idle_flag = False
                _LOGGER.debug(
                    "not idle: payload may predate the trim write at %s",
                    self._fast_write_at,
                )
                return False

        self._idle_flag = commanded == 0 or abs(own) < CONTROL_DEADBAND
        return self._idle_flag

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
        # Whether this is a genuine direction change is a property of the
        # device, not of what this controller remembers doing.
        starting = self._device_direction(data) != direction

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
        cap = self._cap(data, charging)

        # Both buffers mean the same thing: how many watts of grid import to
        # aim for. Earlier versions subtracted the buffer in both branches,
        # which made the charging side aim for that many watts of *export*
        # instead, under the same name. A little import is the cheaper way to
        # be wrong: exported energy earns the feed-in rate, while the same
        # energy kept in the battery is worth the import tariff.
        if charging:
            # grid is negative (export); soak up the surplus, leaving buffer.
            raw = (-grid - own + buffer) * factor
        else:
            # grid is positive (import); cover it down to buffer.
            raw = (grid + own - buffer) * factor

        target = int(_clamp(raw, 0, cap))

        # A floor may raise the target above what the meter asks for, in every
        # smart mode. Note what that means for smart_matching: with a discharge
        # floor of 200 W and a 90 W house, the battery supplies 200 W and the
        # remainder goes to the grid. Zero-on-the-meter therefore only holds
        # while both floors are at 0, which is the default.
        floor = self.settings.get_int(
            OPT_MIN_CHARGE_POWER if charging else OPT_MIN_DISCHARGE_POWER
        )
        if floor > 0 and target < floor:
            target = int(min(floor, cap))

        key = "inputLimit" if charging else "outputLimit"
        current = data.get(key)
        try:
            current_val = int(current)
        except (TypeError, ValueError):
            current_val = -1

        if abs(target - current_val) < CONTROL_MIN_STEP and not starting:
            # Settled, which is precisely the state in which the trim loop is
            # useful: the direction is established and healthy, and all that is
            # left is following the house between polls.
            self._trim_direction = direction if current_val > 0 else None
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

        if starting and await self._reverse_guard(
            data, "charge" if charging else "discharge"
        ):
            return

        if starting:
            self._hold = self._hold_cycles()
        self._last_direction = direction if target > 0 else "none"
        self._idle_since = None

        # Trimming resumes only once the direction is established. While
        # starting, the hold is running and the device has not yet settled at
        # the new limit, so a trim would be correcting against a transient.
        self._trim_direction = None if starting else (
            direction if target > 0 else None
        )

        self._set_state(
            "starting" if starting else "balancing",
            f"grid {grid:.0f} W, battery {own:.0f} W, factor {factor}",
            target=target,
            direction=direction,
        )

    def _cap(self, data: dict[str, Any], charging: bool) -> int:
        """Effective power ceiling: the lower of the setting and the device.

        Writing a limit the hardware cannot honour is not harmful — the device
        clamps it — but it makes the reported target a number that can never be
        reached, which is misleading when reading the dashboard.
        """
        setting = self.settings.get_int(
            OPT_MAX_CHARGE_POWER if charging else OPT_MAX_DISCHARGE_POWER
        )
        try:
            device = int(data.get(DEVICE_CAP_KEY[charging]) or 0)
        except (TypeError, ValueError):
            device = 0
        if device > 0:
            return min(setting, device)
        return setting

    # ── Readings ─────────────────────────────────────────────────────────

    def _meter_power(self) -> float | None:
        """Signed grid power from the linked meter: positive is import.

        The sign is taken as reported. Verified against a second meter on the
        same connection: 2088 W on the Zendure against 2059 W on the YouLess,
        both positive while importing.
        """
        if self.meter is None:
            return None
        if self.meter.last_update_success is False:
            return None

        # A coordinator that has stopped updating still reports its last
        # success as True for a while, so "did the last poll work" is not the
        # same question as "is this reading current". Closed-loop control needs
        # the second one.
        updated = self.meter.updated_at
        if updated is None:
            return None
        age = (dt_util.utcnow() - updated).total_seconds()
        if age > METER_MAX_AGE:
            _LOGGER.debug("meter reading %.0fs old, beyond %ss", age, METER_MAX_AGE)
            return None

        value = (self.meter.data or {}).get("total_power")
        if value is None:
            return None
        try:
            power = float(value)
        except (TypeError, ValueError):
            return None

        return power

    def _battery_power(self, data: dict[str, Any]) -> float:
        """Signed battery power as the grid sees it: positive while discharging.

        Read on the AC side, because that is the side the meter shares a
        connection point with. The DC pack reading is larger by the converter's
        own consumption, and folding that into the loop leaves a permanent
        offset: the controller subtracts watts that never reached the house, so
        it settles with the grid exporting by roughly the conversion loss
        instead of importing by the buffer.

        Observed on hardware: pack power 163 W while the house received 122 W
        and the grid sat at -4 W. The 41 W difference is the converter running
        itself, and it does not belong in a grid-referenced calculation.

        The DC pack reading remains the fallback for a device that does not
        report the AC fields.
        """
        home = data.get("outputHomePower")
        grid_in = data.get("gridInputPower")
        if home is not None or grid_in is not None:
            try:
                return float(home or 0) - float(grid_in or 0)
            except (TypeError, ValueError):
                pass

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

        target_key = key or ("inputLimit" if ac_mode == AC_MODE_CHARGE else "outputLimit")
        other_key = "outputLimit" if target_key == "inputLimit" else "inputLimit"

        # Clear the opposite limit before switching direction, so the device is
        # never briefly holding a limit that belongs to the direction it just
        # left.
        if data.get(other_key) not in (0, "0"):
            await self._write(other_key, 0, data)

        if data.get("acMode") != ac_mode:
            await self._write("acMode", ac_mode, data)

        await self._write(target_key, limit, data)
        self._owns_limits = limit > 0

    async def _write(self, key: str, value: Any, data: dict[str, Any]) -> None:
        """Write one property, skipping no-ops."""
        current = data.get(key)
        try:
            if current is not None and int(current) == int(value):
                _LOGGER.debug("skip %s=%s, device already holds it", key, value)
                return
        except (TypeError, ValueError):
            pass
        _LOGGER.debug("write %s: %s -> %s", key, current, value)
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
            "idle": self._idle_flag,
            "commanded_limit": self._commanded,
            "last_direction": self._last_direction,
            "writes_enabled": self.settings.get(OPT_OPERATION_MODE) != MODE_STANDBY,
            "trim_direction": self._trim_direction or "none",
            "trim_writes": self._trim_writes,
            "trim_factor": self._trim_factor(),
        }

        # One line per decision. The status sensor shows the outcome; this shows
        # the reasoning that led there, which is what you need when a mode does
        # something unexpected three steps into a sequence.
        _LOGGER.debug(
            "%s | %s | dir=%s target=%sW grid=%sW battery=%sW idle=%s commanded=%sW",
            status,
            reason,
            direction,
            target,
            self.state.meter_power,
            self.state.battery_power,
            self._idle_flag,
            self._commanded,
        )
