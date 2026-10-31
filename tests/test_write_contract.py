"""What the integration writes to the device, per mode, and in which order.

The order matters on the device: properties written while smartMode is 1 stay
in RAM, written while it is 0 they go to flash as well, and after a restart the
device comes back with what flash holds. These tests pin the requests so that a
change to them is a deliberate one.
"""

import asyncio

from custom_components.zendure_restapi import controller as C

from .helpers import make_controller, sent


def run(coro):
    return asyncio.run(coro)


def test_standby_sends_one_command_with_smartmode_last():
    c = make_controller(C.MODE_STANDBY)
    data = {"smartMode": 1, "inputLimit": 3000, "outputLimit": 0, "acMode": C.AC_MODE_CHARGE}
    run(c._apply_standby(data))
    # the opposite limit already holds 0 and is skipped
    assert sent(c) == [[("inputLimit", 0), ("smartMode", 0)]]
    run(c._apply_standby(data))
    assert len(sent(c)) == 1, "standby writes once per stay"


def test_standby_writes_nothing_when_the_device_already_rests():
    c = make_controller(C.MODE_STANDBY)
    run(c._apply_standby({"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": 1}))
    assert sent(c) == []


def test_direction_write_puts_smartmode_first():
    c = make_controller(C.MODE_QUICK_CHARGE)
    data = {"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": C.AC_MODE_DISCHARGE}
    run(c._write_direction(data, C.AC_MODE_CHARGE, 3000))
    assert sent(c) == [[("smartMode", 1), ("acMode", C.AC_MODE_CHARGE), ("inputLimit", 3000)]]


def test_direction_change_clears_the_other_limit_in_the_same_request():
    c = make_controller(C.MODE_QUICK_DISCHARGE)
    data = {"smartMode": 1, "inputLimit": 2000, "outputLimit": 0, "acMode": C.AC_MODE_CHARGE}
    run(c._write_direction(data, C.AC_MODE_DISCHARGE, 3000))
    assert sent(c) == [[("inputLimit", 0), ("acMode", C.AC_MODE_DISCHARGE), ("outputLimit", 3000)]]


def test_choosing_standby_again_resends_the_standby_command():
    c = make_controller(C.MODE_STANDBY)
    data = {"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": 1}
    run(c._apply_standby(data))
    data["inputLimit"] = 3000  # back from a restart with a limit from flash
    run(c._apply_standby(data))
    assert sent(c) == [], "passive until the user acts"
    c.reapply_mode()
    run(c._apply_standby(data))
    assert sent(c) == [[("inputLimit", 0)]]


# ── Standby at startup (v1.6.10, review) ─────────────────────────────────

def fresh(mode=C.MODE_STANDBY):
    c = make_controller(mode)
    c._standby_sent = None  # as after setup
    c.coordinator.entry_id = f"entry-{id(c)}"
    return c


def test_standby_at_startup_writes_nothing_and_keeps_hand_set_limits():
    c = fresh()
    run(c._apply_standby({"smartMode": 0, "inputLimit": 0, "outputLimit": 800, "acMode": C.AC_MODE_DISCHARGE}))
    assert sent(c) == []
    c._set_state.assert_called_with("standby", "passive, writing nothing")


def test_after_a_reload_an_own_limit_still_gets_the_stop():
    before = make_controller(C.MODE_QUICK_DISCHARGE)
    before.coordinator.entry_id = "entry-reload"
    run(before._write_direction({"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": 1},
                                C.AC_MODE_DISCHARGE, 1500))
    # The entry is reloaded (a battery leaving a cluster): a new controller in standby.
    after = make_controller(C.MODE_STANDBY, before.coordinator)
    after._standby_sent = None
    after.coordinator.api.async_write_properties.reset_mock()
    run(after._apply_standby({"smartMode": 1, "inputLimit": 0, "outputLimit": 1500, "acMode": C.AC_MODE_DISCHARGE}))
    assert sent(after) == [[("outputLimit", 0), ("smartMode", 0)]]
    # Stopped: a second reload leaves it alone.
    again = make_controller(C.MODE_STANDBY, before.coordinator)
    again._standby_sent = None
    again.coordinator.api.async_write_properties.reset_mock()
    run(again._apply_standby({"smartMode": 0, "inputLimit": 0, "outputLimit": 900, "acMode": C.AC_MODE_DISCHARGE}))
    assert sent(again) == []


def test_switching_into_standby_still_stops():
    c = fresh()
    run(c._apply_standby({"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": 1}))
    c._standby_sent = False  # what a cycle in another mode does
    run(c._apply_standby({"smartMode": 1, "inputLimit": 0, "outputLimit": 600, "acMode": C.AC_MODE_DISCHARGE}))
    assert sent(c) == [[("outputLimit", 0), ("smartMode", 0)]]
