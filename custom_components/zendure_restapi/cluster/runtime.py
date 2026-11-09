"""The battery cluster at work: a tick every two seconds (v1.7.0).

Reads every member, asks group.Distributor what each one should be, gives the
roles, and writes the batteries whose role or limit changed straight away,
one at a time with a short pause, so the meter sees each step on its own.
Home Assistant is used for the clock; of the integration only the trace
recorder (v1.7.5), which writes one row per decision while it is on.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from ..trace import ZendureTrace, cluster_columns
from .contract import NONE, ROLE_SECONDARY, SMART, GroupSettings, Member, Reading
from .group import Decision, Distributor

_LOGGER = logging.getLogger(__name__)

TICK = timedelta(seconds=2)
# Between two batteries written in one decision: the meter sees each step.
WRITE_GAP_S = 0.3


class ClusterCoordinator:
    """The group of batteries under one cluster entry."""

    def __init__(
        self,
        hass: HomeAssistant,
        settings: Callable[[], GroupSettings],
        on_update: Callable[[], None],
    ) -> None:
        self.hass = hass
        self._settings = settings
        self._on_update = on_update
        self.members: dict[str, Member] = {}
        self.distributor = Distributor()
        self.decision: Decision | None = None
        self.readings: list[Reading] = []
        self.grid: float | None = None
        self._unsub: Any = None
        self._busy = False
        # Trace recording of the cluster (v1.7.5): off until switched on,
        # also after a restart. A Virtual cluster sets trace_virtual to add the
        # house load and both meters.
        self.trace_virtual: Callable[[], dict[str, Any]] | None = None
        self.trace = ZendureTrace(
            hass,
            prefix=lambda: "trace_cluster",
            columns=lambda: cluster_columns(len(self.members), self.trace_virtual is not None),
        )

    # ── Life cycle ───────────────────────────────────────────────────────

    def set_members(self, members: list[Member]) -> None:
        self.members = {m.key: m for m in members}

    def start(self) -> None:
        if self._unsub is None:
            self._unsub = async_track_time_interval(self.hass, self._tick, TICK)

    def stop(self) -> None:
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self.trace.active:
            self.hass.async_create_task(self.trace.async_close())

    @callback
    def _tick(self, _now: Any = None) -> None:
        self.hass.async_create_task(self.async_evaluate())

    # ── For the primary ───────────────────────────────────────────────────

    def secondaries_against(self, key: str, direction: str) -> bool:
        """Whether a secondary still has a limit in another direction than `direction`."""
        for other, role in self.distributor.roles.items():
            if other == key or role.role != ROLE_SECONDARY:
                continue
            if role.limit > 0 and role.direction not in (NONE, direction):
                return True
        # What a secondary's device still holds counts too: its stop is on its way.
        for r in self.readings:
            role = self.distributor.roles.get(r.key)
            if (
                r.key != key
                and role is not None
                and role.role == ROLE_SECONDARY
                and r.direction not in (NONE, direction)
            ):
                return True
        return False

    # ── The decision ─────────────────────────────────────────────────────

    async def async_evaluate(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            settings = self._settings()
            readings = [m.reading() for m in self.members.values()]
            primary = self.members.get(self.distributor.primary or "")
            grid = primary.grid() if primary is not None else None
            if grid is None:
                grid = next((g for g in (m.grid() for m in self.members.values()) if g is not None), None)
            decision = self.distributor.decide(readings, settings, grid, dt_util.utcnow().timestamp())
            if decision.status == "idle" and not any(m.taking_part() for m in self.members.values()):
                decision.reason = "no battery taking part"
            self.readings, self.grid, self.decision = readings, grid, decision
            for key, role in decision.roles.items():
                self.members[key].set_role(role.role, role.direction, role.limit)
            first = True
            for key in decision.changed:
                if not first:
                    await asyncio.sleep(WRITE_GAP_S)
                first = False
                try:
                    await self.members[key].apply()
                except Exception:  # noqa: BLE001 - one battery must not stop the group
                    _LOGGER.exception("Battery cluster: writing %s failed", key)
            if decision.changed:
                _LOGGER.debug(
                    "cluster | %s | %s | %s", decision.status, decision.reason,
                    {k: (r.role, r.direction, r.limit) for k, r in decision.roles.items()},
                )
        except Exception:  # noqa: BLE001 - the tick must survive anything
            _LOGGER.exception("Battery cluster: evaluation failed")
        finally:
            self._busy = False
        await self._record()
        self._on_update()

    # ── Trace recording (v1.7.5) ─────────────────────────────────────

    async def _record(self) -> None:
        """One trace row for this decision. Records, never steers, never raises.

        The device limits, power and state of charge are as read for this
        decision, before its writes; role, direction and cluster limit are what
        it decided; the status of each battery is its own controller's, so a
        primary that stops or waits shows here as well.
        """
        if not self.trace.wanted:
            if self.trace.active:
                await self.trace.async_close()
            return
        try:
            await self.trace.async_sample(self.trace_row())
        except Exception:  # noqa: BLE001 - a recorder must not stop the cluster
            _LOGGER.exception("Battery cluster: trace row failed")

    def trace_row(self) -> dict[str, Any]:
        """The values of one row, by column name."""
        decision = self.decision
        try:
            mode = self._settings().mode
        except Exception:  # noqa: BLE001 - settings unreadable: leave the column empty
            mode = ""
        primary = self.members.get(decision.primary or "") if decision is not None else None
        row: dict[str, Any] = {
            "ts": dt_util.now().isoformat(timespec="milliseconds"),
            "mode": mode,
            "status": self.status,
            "reason": decision.reason if decision is not None else "",
            "grid_w": "" if self.grid is None else round(self.grid),
            "primary": primary.name if primary is not None else "",
            "group_power_w": _blank(self.group_power),
            "group_soc": _blank(self.group_soc),
        }
        readings = {r.key: r for r in self.readings}
        for n, member in enumerate(self.members.values(), start=1):
            reading = readings.get(member.key)
            role = decision.roles.get(member.key) if decision is not None else None
            state = getattr(getattr(member, "controller", None), "state", None)
            b = f"b{n}_"
            row.update({
                b + "name": member.name,
                b + "available": "" if reading is None else int(reading.available),
                b + "role": role.role if role is not None else "",
                b + "direction": role.direction if role is not None else "",
                b + "cluster_limit_w": role.limit if role is not None else "",
                b + "input_limit": "" if reading is None else reading.charge_limit,
                b + "output_limit": "" if reading is None else reading.discharge_limit,
                b + "power_w": "" if reading is None else round(reading.power),
                b + "soc": "" if reading is None else _blank(reading.soc),
                b + "status": getattr(state, "status", "") or "",
                b + "reason": getattr(state, "reason", "") or "",
            })
        if self.trace_virtual is not None:
            try:
                row.update(self.trace_virtual())
            except Exception:  # noqa: BLE001 - the virtual columns stay empty
                _LOGGER.debug("Virtual cluster: trace columns unavailable", exc_info=True)
        return row

    @property
    def steers(self) -> bool:
        """Whether the group holds the grid of the connection (v1.7.0).

        Only then does a battery outside the group stand aside. A cluster
        without a battery taking part, or in standby or a quick mode, steers
        nothing, and a single battery outside it keeps its own control.
        """
        try:
            mode = self._settings().mode
        except Exception:  # noqa: BLE001 - settings unreadable: assume the group steers
            return True
        if mode not in SMART:
            return False
        if not any(m.taking_part() for m in self.members.values()):
            return False
        # Before the first decision the readings are not in yet: count the members.
        return not self.readings or any(r.available for r in self.readings)

    # ── What the entities show ───────────────────────────────────────────

    @property
    def status(self) -> str:
        return self.decision.status if self.decision else "idle"

    @property
    def attributes(self) -> dict[str, Any]:
        if self.decision is None:
            return {}
        return {"reason": self.decision.reason, **self.decision.attributes}

    @property
    def group_power(self) -> float | None:
        live = [r for r in self.readings if r.available]
        return round(sum(r.power for r in live)) if live else None

    @property
    def group_soc(self) -> float | None:
        weighted = [(r.soc, r.capacity_kwh) for r in self.readings if r.soc is not None and r.capacity_kwh]
        total = sum(c for _, c in weighted)
        if not total:
            plain = [r.soc for r in self.readings if r.soc is not None]
            return round(sum(plain) / len(plain), 1) if plain else None
        return round(sum(s * c for s, c in weighted) / total, 1)

    @property
    def soc_spread(self) -> float | None:
        socs = [r.soc for r in self.readings if r.soc is not None]
        return round(max(socs) - min(socs), 1) if len(socs) > 1 else None


def _blank(value: Any) -> Any:
    """A number for the trace, or empty when there is none."""
    return "" if value is None else value
