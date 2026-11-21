"""What the battery cluster knows about a battery, and what it may ask of it.

Nothing in this package imports from the rest of the integration. A battery is
a `Member`: a few readings and four orders. The Zendure side lives in
`member.py`; another kind of battery would be another implementation of the
same protocol, without a change here (v1.7.0).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

# Directions, as the cluster names them.
CHARGE = "charge"
DISCHARGE = "discharge"
NONE = "none"

# The modes of the group: the Operation mode names of the integration.
STANDBY = "standby"
SMART_MATCHING = "smart_matching"
SMART_CHARGE = "smart_charge_only"
SMART_DISCHARGE = "smart_discharge_only"
QUICK_CHARGE = "quick_charge"
QUICK_DISCHARGE = "quick_discharge"
GROUP_MODES = (STANDBY, SMART_MATCHING, SMART_DISCHARGE, SMART_CHARGE, QUICK_CHARGE, QUICK_DISCHARGE)
SMART = frozenset({SMART_MATCHING, SMART_CHARGE, SMART_DISCHARGE})

# What a battery is told to be.
ROLE_PRIMARY = "primary"          # runs its own loop on the group's grid and settings
ROLE_SECONDARY = "secondary"      # holds the limit it is given, writes nothing else
ROLE_QUICK = "quick"            # the group's quick mode, at its own cap
ROLE_STANDBY = "standby"        # stopped once, then hands off
ROLE_PROTECT = "protect"        # SOC protection of the group: charges at its own cap (v1.7.6)


def allowed(mode: str, direction: str) -> bool:
    """Whether a smart mode lets the group run a direction."""
    if direction == CHARGE:
        return mode in (SMART_MATCHING, SMART_CHARGE)
    if direction == DISCHARGE:
        return mode in (SMART_MATCHING, SMART_DISCHARGE)
    return False


@dataclass(frozen=True)
class Reading:
    """One battery as the cluster sees it at one moment."""

    key: str                    # the subentry id
    name: str
    phase: int                  # 1 to 3, a label; several batteries may share one
    priority: int               # 1 to 9, higher is primary first
    available: bool             # reporting, and taking part
    power: float                # W on the AC side, positive while discharging
    charge_limit: int           # W the device holds now (inputLimit)
    discharge_limit: int        # W the device holds now (outputLimit)
    charge_cap: int             # W it may charge: device, setting and Max share
    discharge_cap: int          # W it may discharge
    full: bool                  # the device closed charging (socLimit upper)
    empty: bool                 # the device closed discharging (socLimit lower)
    soc: float | None           # %
    capacity_kwh: float | None
    response_s: float | None    # measured median response, None until measured
    # The direction a primary wants to start but holds back from, because
    # secondaries still run the other way (NONE otherwise).
    waiting_for: str = NONE
    # Under its own lower SOC limit: it may charge, but not discharge (v1.7.0).
    below_floor: bool = False
    # The lower SOC limit in %, None when unknown (v1.7.6).
    floor: float | None = None

    def cap(self, direction: str) -> int:
        return self.charge_cap if direction == CHARGE else self.discharge_cap

    def closed(self, direction: str) -> bool:
        return self.full if direction == CHARGE else (self.empty or self.below_floor)

    def room(self, direction: str) -> int:
        """What this battery can still add in a direction: nothing when closed."""
        if not self.available or self.closed(direction):
            return 0
        return max(0, self.cap(direction))

    def limit(self, direction: str) -> int:
        return self.charge_limit if direction == CHARGE else self.discharge_limit

    @property
    def direction(self) -> str:
        """The direction the device is set to, from its limits."""
        if self.discharge_limit > 0:
            return DISCHARGE
        if self.charge_limit > 0:
            return CHARGE
        return NONE

    def flow(self, direction: str) -> float:
        """Measured power in a direction, as a positive number; 0 otherwise."""
        if direction == DISCHARGE:
            return max(0.0, self.power)
        if direction == CHARGE:
            return max(0.0, -self.power)
        return 0.0


@dataclass(frozen=True)
class GroupSettings:
    """The control settings of the group, read from the cluster entry."""

    mode: str
    charge_buffer: int
    discharge_buffer: int
    start_charge_at: int
    start_discharge_at: int
    concentrate_below: int
    redistribute_s: int
    # Seconds the demand stays above Concentrate below before a secondary is
    # added (v1.7.4); 0 adds them at the first split, as before.
    start_after: int = 0
    # SoC balance (v1.8.5), in percentage points of SoC: at the start of a
    # direction the battery with the most energy for it becomes primary when it
    # leads the current one by more than this; the secondaries share by energy.
    # 0 keeps the choice of v1.8.4 (priority, running, speed, room).
    soc_balance: int = 0
    # SOC protection of the group (v1.7.6): with every battery under its floor
    # they charge from the grid to their floor + 5. Off unless set.
    soc_protection: bool = False
    # Since v1.8.1 it may wait for the cheapest period of the cluster's own
    # Price Control: (start, end, import price, start as HH:MM local) per
    # period not yet over, start and end as epoch seconds. Empty without one.
    soc_protection_price: bool = False
    price_periods: tuple = ()
    # Since v1.8.2, with the fixed tariff: the windows of the day, (start, end,
    # start as HH:MM, end as HH:MM local), start and end as epoch seconds, today
    # and tomorrow. None for the other decisions or without Price Control.
    solar_windows: tuple | None = None


class Member(Protocol):
    """A battery under the cluster."""

    key: str

    def reading(self) -> Reading: ...

    def taking_part(self) -> bool:
        """Whether the owner lets the battery take part (its Take part switch)."""

    def grid(self) -> float | None:
        """The grid power the battery's loops act on, positive while importing."""

    def set_role(self, role: str, direction: str = NONE, limit: int = 0) -> None:
        """Primary, secondary with a limit, quick, or standby. Takes effect on the
        battery's next cycle; `apply` makes it take effect now."""

    async def apply(self) -> None:
        """Run the battery's cycle now, so a new role or limit is written."""


@dataclass(frozen=True)
class SolarWait:
    """What SOC protection does under the fixed tariff (v1.8.2).

    `charge_at` None: charge from the grid now, `why` says why. Otherwise it
    waits until `charge_at` (epoch seconds): for daylight first when
    `daylight_at` is set, then for solar surplus; `charge_label` is the end of
    the day as HH:MM when that is the moment, None when the wait runs out first.
    """

    charge_at: float | None
    why: str | None = None
    daylight_at: str | None = None
    charge_label: str | None = None


def solar_wait(since: float, now: float, windows: tuple | list, window_s: float) -> SolarWait:
    """Under the fixed tariff every quarter costs the same: wait for the sun.

    From the moment the battery fell under its floor (`since`) it charges from
    solar surplus during the window of the day, and from the grid at the end
    of that window, or once `window_s` has passed if that comes first. Under
    the floor at night, it first waits for the window to open, if that is
    within `window_s`. Windows are (start, end, start HH:MM, end HH:MM).
    """
    later = [w for w in windows if w[1] > since]
    if not later:
        return SolarWait(None, "direct: no daylight")
    start, end, start_label, end_label = later[0]
    deadline = since + window_s
    hours = int(window_s // 3600)
    if start > deadline:
        return SolarWait(None, f"direct: no daylight within {hours} h")
    charge_at = min(end, deadline)
    if now >= charge_at:
        return SolarWait(None, "direct: no solar surplus by the end of the day" if end <= deadline
                         else f"direct: waited {hours} h")
    return SolarWait(
        charge_at,
        daylight_at=start_label if now < start else None,
        charge_label=end_label if end <= deadline else None,
    )

