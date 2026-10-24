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
