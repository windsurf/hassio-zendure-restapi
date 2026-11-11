"""The Inverter power limit and the settings the device keeps (v1.7.3).

inverseMaxPower is the battery's ceiling for feeding into the grid. From Home
Assistant it can be lowered, never raised: that is for the Zendure app. Since
v1.7.6 the same holds for chargeMaxLimit, the Charge power limit. A write of it,
of chargeMaxLimit, socSet or minSoc is read back from the reports, and the
outcome goes to the logbook.
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zendure_restapi import number as N


def entity(key: str, value: int, holds: bool = True):
    description = next(d for d in N.NUMBERS if d.key == key)
    data = {key: value}
    events: list = []
    tasks: list = []
    written: list = []

    async def write(k, v):
        written.append((k, v))
        if holds:
            data[k] = v

    coordinator = SimpleNamespace(data=data, entry_id="B", update_interval=timedelta(seconds=0),
                                  async_write=write)
    hass = SimpleNamespace(
        bus=SimpleNamespace(async_fire=lambda event, payload, context=None: events.append((event, payload, context))),
        states=SimpleNamespace(get=lambda _e: SimpleNamespace(name="Zendure Inverter power limit")),
        async_create_task=lambda coro: tasks.append(coro),
    )
    number = N.ZendureNumber.__new__(N.ZendureNumber)
    number.entity_description = description
    number.coordinator = coordinator
    number._data_key = key
    number.hass = hass
    number.entity_id = f"number.zendure_{key.lower()}"
    number._context = SimpleNamespace(user_id="tester")
    return number, events, tasks, written


def run(number, value, tasks):
    asyncio.run(number.async_set_native_value(value))
    for coro in tasks:
        asyncio.run(coro)


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(N, "CONFIRM_MIN_S", 0.0)
    monkeypatch.setattr(N, "CONFIRM_STEP_S", 0.0)


def test_raising_is_refused():
    number, events, tasks, written = entity("inverseMaxPower", 2400)
    with pytest.raises(HomeAssistantError):
        asyncio.run(number.async_set_native_value(3000))
    assert written == [] and events == []
    # Its maximum is what the device reports, so the frontend offers no more.
    assert number.native_max_value == 2400


def test_nothing_is_written_before_the_battery_reports_it():
    number, events, tasks, written = entity("inverseMaxPower", 3000)
    number.coordinator.data["inverseMaxPower"] = None
    with pytest.raises(HomeAssistantError, match="not reported it yet"):
        asyncio.run(number.async_set_native_value(2400))
    assert written == [] and events == []


def test_lowering_is_allowed_and_confirmed_in_the_logbook():
    number, events, tasks, written = entity("inverseMaxPower", 3000)
    run(number, 2400, tasks)
    assert written == [("inverseMaxPower", 2400)]
    (event, payload, context), = events
    assert event == "logbook_entry" and payload["entity_id"] == "number.zendure_inversemaxpower"
    assert payload["message"] == "changed from 3000 to 2400 W, confirmed by the battery"
    assert context.user_id == "tester"


def test_the_charge_power_limit_is_not_raised_either():
    # v1.7.6: the Zendure app blocks a raise per battery type (800 W out of the box).
    number, events, tasks, written = entity("chargeMaxLimit", 1000)
    assert number.native_max_value == 1000
    with pytest.raises(HomeAssistantError, match="Charge power limit"):
        asyncio.run(number.async_set_native_value(1200))
    assert written == [] and events == []


def test_the_charge_power_limit_is_lowered_and_confirmed():
    number, events, tasks, written = entity("chargeMaxLimit", 1000)
    run(number, 800, tasks)
    assert written == [("chargeMaxLimit", 800)]
    assert events[0][1]["message"] == "changed from 1000 to 800 W, confirmed by the battery"


def test_nothing_is_written_before_the_battery_reports_the_charge_power_limit():
    number, events, tasks, written = entity("chargeMaxLimit", 1000)
    number.coordinator.data["chargeMaxLimit"] = None
    with pytest.raises(HomeAssistantError, match="not reported it yet"):
        asyncio.run(number.async_set_native_value(800))
    assert written == []


def test_the_other_numbers_keep_their_range():
    number, *_ = entity("inputLimit", 0)
    number.coordinator.data["chargeMaxLimit"] = None
    assert number.native_max_value == N.POWER_CEILING


def test_a_value_the_device_does_not_take_is_not_confirmed():
    number, events, tasks, _ = entity("chargeMaxLimit", 3000, holds=False)
    run(number, 2000, tasks)
    assert events[0][1]["message"] == "set to 2000 W but not confirmed: the battery reports 3000 W"


def test_the_same_value_again_logs_nothing():
    number, events, tasks, written = entity("socSet", 1000)
    run(number, 100, tasks)
    assert written == [("socSet", 1000)] and events == []


def test_other_numbers_are_not_read_back():
    number, events, tasks, _ = entity("inputLimit", 0)
    run(number, 500, tasks)
    assert tasks == [] and events == []
