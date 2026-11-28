"""A new installation writes nothing to the battery (since v1.6.10).

A battery added for the first time comes up with every setting at its default,
and the default Operation mode is standby: hands off until a mode is chosen.
From the first poll on, nothing is written, whatever the battery reports: a
limit left by the Zendure app or by hand, its smart mode on, a state of charge
under its lower limit, or an inverter that runs. These tests build the
controller the way the integration does, through its own constructor and the
settings of an entry without options, and run the whole mode loop and the trim
loop over many polls; only the battery API is a stand-in that counts writes.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import DEFAULTS, MODE_STANDBY, OPT_OPERATION_MODE
from custom_components.zendure_restapi.settings import ZendureSettings

POLLS = 30


def new_installation(data: dict) -> C.ZendureController:
    """A controller as the integration sets it up for a new entry: no options at all."""
    entry = SimpleNamespace(entry_id="new-entry", options={}, data={"device_type": "battery"})
    hass = MagicMock()
    hass.data = {}
    settings = ZendureSettings(hass, entry)
    coordinator = MagicMock()
    coordinator.hass = hass
    coordinator.entry_id = entry.entry_id
    coordinator.config_entry = entry
    coordinator.data = data
    coordinator.pack_count = 1
    coordinator.restart_seq = 0
    coordinator.fetch_started_at = None
    coordinator.update_interval = timedelta(seconds=2)
    coordinator.updated_at = C.dt_util.utcnow()
    coordinator.api.async_write_properties = AsyncMock()
    coordinator.api.async_write_property = AsyncMock()
    controller = C.ZendureController(coordinator, settings)
    controller._set_state = MagicMock()  # the status entity is not under test
    return controller


def writes(controller: C.ZendureController) -> int:
    api = controller.coordinator.api
    return api.async_write_properties.await_count + api.async_write_property.await_count


def run_polls(controller: C.ZendureController) -> None:
    async def polls() -> None:
        for _ in range(POLLS):
            controller.coordinator.updated_at = C.dt_util.utcnow()
            await controller._run()
            await controller.async_trim()

    asyncio.run(polls())


BATTERY = {
    "electricLevel": 40, "minSoc": 100, "socSet": 1000, "socLimit": 0,
    "smartMode": 0, "acMode": 1, "inputLimit": 0, "outputLimit": 0,
    "packInputPower": 0, "outputPackPower": 0, "outputHomePower": 0, "gridInputPower": 0,
}


def test_the_default_operation_mode_is_standby():
    assert DEFAULTS[OPT_OPERATION_MODE] == MODE_STANDBY


@pytest.mark.parametrize("report", [
    pytest.param({}, id="at-rest"),
    pytest.param({"smartMode": 1, "acMode": 2, "outputLimit": 800, "outputHomePower": 790,
                  "outputPackPower": 830}, id="discharging-on-a-limit-of-the-app"),
    pytest.param({"smartMode": 1, "acMode": 1, "inputLimit": 2400, "gridInputPower": 2400,
                  "packInputPower": 2300}, id="charging-on-a-limit-from-flash"),
    pytest.param({"electricLevel": 3, "socLimit": 2}, id="under-its-lower-limit"),
    pytest.param({"electricLevel": 100, "socLimit": 1, "smartMode": 1, "inputLimit": 600}, id="full"),
])
def test_a_new_installation_writes_nothing(report):
    controller = new_installation({**BATTERY, **report})
    run_polls(controller)
    assert writes(controller) == 0
    assert controller._standby_sent is not None, "the standby decision was taken"


def test_choosing_standby_afterwards_still_stops_the_battery():
    """The guard is for the start only: a user who picks standby gets the stop."""
    controller = new_installation({**BATTERY, "smartMode": 1, "acMode": 2, "outputLimit": 800})
    run_polls(controller)
    assert writes(controller) == 0
    controller.reapply_mode()
    run_polls(controller)
    assert writes(controller) == 1
