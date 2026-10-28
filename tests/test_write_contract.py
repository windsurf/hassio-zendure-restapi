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


# ── Standby at startup (v1.6.10) ──────────────────────────────────────────


def started(mode: str, entry_id: str):
    """A controller as set up: the standby decision not taken yet."""
    c = make_controller(mode)
    c.coordinator.entry_id = entry_id
    c._standby_sent = None
    return c


def test_standby_at_startup_writes_nothing_and_keeps_a_limit_set_by_hand():
    C._OPEN_LIMITS.discard("startup")
    c = started(C.MODE_STANDBY, "startup")
    run(c._apply_standby({"smartMode": 1, "inputLimit": 0, "outputLimit": 600, "acMode": C.AC_MODE_DISCHARGE}))
    assert sent(c) == []


def test_a_reload_after_an_own_limit_stops_it_once():
    C._OPEN_LIMITS.discard("reload")
    before = started(C.MODE_QUICK_CHARGE, "reload")
    data = {"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": C.AC_MODE_DISCHARGE}
    run(before._write_direction(data, C.AC_MODE_CHARGE, 3000))
    after = started(C.MODE_STANDBY, "reload")       # reloaded, standby chosen
    run(after._apply_standby(data))
    assert sent(after) == [[("inputLimit", 0), ("smartMode", 0)]]
    again = started(C.MODE_STANDBY, "reload")       # a second reload
    run(again._apply_standby(data))
    assert sent(again) == []


def test_switching_to_standby_still_stops_the_battery():
    C._OPEN_LIMITS.discard("switch")
    c = started(C.MODE_STANDBY, "switch")
    c._standby_sent = False                         # the way out of another mode
    run(c._apply_standby({"smartMode": 1, "inputLimit": 0, "outputLimit": 600, "acMode": C.AC_MODE_DISCHARGE}))
    assert sent(c) == [[("outputLimit", 0), ("smartMode", 0)]]


def test_a_limit_written_moments_ago_is_stopped_even_if_the_report_reads_zero():
    """Write, then standby before the report shows the limit: the stop is not a no-op (v1.6.10)."""
    C._OPEN_LIMITS.discard("moments")
    c = started(C.MODE_QUICK_CHARGE, "moments")
    fresh = {"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": C.AC_MODE_DISCHARGE}
    run(c._write_direction(dict(fresh), C.AC_MODE_CHARGE, 500))
    assert c._owns_limits
    c._standby_sent = False                         # standby chosen right after
    before = len(sent(c))
    run(c._apply_standby(dict(fresh)))              # the report still reads 0
    assert sent(c)[before:] == [[("inputLimit", 0), ("outputLimit", 0), ("smartMode", 0)]]


def test_a_stop_without_an_own_limit_is_still_skipped_when_the_device_rests():
    C._OPEN_LIMITS.discard("rest")
    c = started(C.MODE_STANDBY, "rest")
    c._standby_sent = False                         # switching, but nothing of its own was written
    run(c._apply_standby({"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": 1}))
    assert sent(c) == []
