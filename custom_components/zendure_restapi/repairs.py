"""Repairs that can be fixed from Home Assistant (v1.8.7).

A limit left behind in standby: the repair stops the battery once, as
choosing standby again would. Every other repair of this integration explains
and clears by itself.
"""

from __future__ import annotations

from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant

from .const import DOMAIN

RUNTIME_SUFFIX = "_runtime"


class StopBatteryFlow(ConfirmRepairFlow):
    """Confirm, then send the standby stop once."""

    def __init__(self, entry_id: str | None) -> None:
        super().__init__()
        self._entry_id = entry_id

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        if user_input is not None:
            runtime = self.hass.data.get(DOMAIN, {}).get(f"{self._entry_id}{RUNTIME_SUFFIX}") or {}
            controller = runtime.get("controller") if isinstance(runtime, dict) else None
            if controller is not None:
                controller.stop_once()
                coordinator = getattr(controller, "coordinator", None)
                if coordinator is not None:
                    await coordinator.async_request_refresh()
            return self.async_create_entry(data={})
        return await super().async_step_confirm(user_input)


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, Any] | None
) -> RepairsFlow:
    """The fix flow of a repair of this integration."""
    return StopBatteryFlow((data or {}).get("entry_id"))
