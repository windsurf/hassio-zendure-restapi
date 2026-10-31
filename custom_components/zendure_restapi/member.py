"""A Zendure battery as a member of the battery cluster (v1.7.0).

The one place where cluster/ meets this integration: it reads the battery's
controller and coordinator into a `Reading`, and turns a role into the
controller's settings view. Nothing in cluster/ knows it is talking to Zendure.
"""

from __future__ import annotations

from typing import Any

from .const import CONF_MAX_SHARE, CONF_PHASE, CONF_PRIORITY, DEFAULT_PRIORITY, OPT_TAKE_PART, PACK_PREFIX
from .controller import ZendureController, _as_int, _below_floor, _soc_bounds
from .cluster.contract import NONE, ROLE_PRIMARY, Reading
from .registry import pack_model
from .settings import SettingsView, ZendureSettings


def phase_number(value: Any) -> int:
    """'L2' or 2 to 2; anything else to 1."""
    text = str(value or "").upper().lstrip("L")
    return int(text) if text in ("1", "2", "3") else 1


class ZendureMember:
    """One battery entry under the cluster."""

    def __init__(
        self,
        key: str,
        name: str,
        data: dict[str, Any],
        controller: ZendureController,
        view: SettingsView,
        own: ZendureSettings,
    ) -> None:
        self.key = key
        self.name = name
        self.data = dict(data)
        self.phase = phase_number(data.get(CONF_PHASE))
        self.priority = int(data.get(CONF_PRIORITY) or DEFAULT_PRIORITY)
        self.max_share = int(data.get(CONF_MAX_SHARE) or 0)
        self.controller = controller
        self.view = view
        self.own = own

    # ── Joining and leaving ──────────────────────────────────────────────

    def join(self, group: ZendureSettings, group_name: str, start_check: Any) -> None:
        self.view.group = group
        self.view.group_name = group_name
        self.view.max_share = self.max_share
        # Standby until the first decision: never steer on a role not yet given.
        self.view.role = "standby"
        self.controller.start_check = lambda direction: start_check(self.key, direction)

    def leave(self) -> None:
        self.view.group = None
        self.view.group_name = None
        self.view.role = None
        self.view.max_share = 0
        self.controller.start_check = None
        self.controller.follow_direction = NONE
        self.controller.follow_limit = 0

    # ── The contract ─────────────────────────────────────────────────────

    def taking_part(self) -> bool:
        return self.own.get_bool(OPT_TAKE_PART)

    def reading(self) -> Reading:
        c = self.controller
        data = c.coordinator.data or {}
        available = bool(data) and c.coordinator.last_update_success is not False \
            and not c._unloaded and self.taking_part()
        full, empty = _soc_bounds(data) if data else (False, False)
        capacity = c.measure.usable_capacity
        if not capacity:
            total = 0.0
            for index in range(1, (c.coordinator.pack_count or 0) + 1):
                prefix = f"{PACK_PREFIX}{index}."
                _, kwh = pack_model(data.get(f"{prefix}sn"), data.get(f"{prefix}packType"))
                total += kwh or 0.0
            capacity = total or None
        return Reading(
            key=self.key,
            name=self.name,
            phase=self.phase,
            priority=self.priority,
            available=available,
            power=c._battery_power(data) if data else 0.0,
            charge_limit=_as_int(data.get("inputLimit")) or 0,
            discharge_limit=_as_int(data.get("outputLimit")) or 0,
            charge_cap=c._cap(data, True) if data else 0,
            discharge_cap=c._cap(data, False) if data else 0,
            full=full,
            empty=empty,
            soc=_as_int(data.get("electricLevel")),
            capacity_kwh=capacity,
            response_s=c.measure.response_time,
            waiting_for=c.waiting_for,
            below_floor=_below_floor(data) if data else False,
        )

    def grid(self) -> float | None:
        # The reading the battery's own loops act on, from the meter Meter source picks.
        return self.controller._meter_reading()[0]

    def set_role(self, role: str, direction: str = NONE, limit: int = 0) -> None:
        self.view.role = role
        self.controller.follow_direction = direction
        self.controller.follow_limit = int(limit)

    async def apply(self) -> None:
        await self.controller.async_run()

    @property
    def leading(self) -> bool:
        return self.view.role == ROLE_PRIMARY
