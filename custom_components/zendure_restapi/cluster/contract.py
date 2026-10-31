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
    phase: int                  # 1 to 3
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
