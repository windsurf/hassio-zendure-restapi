"""Distance from the SOC bounds (v1.7.5, issue #8).

On two SolarFlow 800 Plus at minSoc 10 % the state of charge swung between 10
and 11 %: the device reopened discharging at 11, the battery discharged a few
hundred watts, dropped to 10, closed again and charged ~27 W by itself. A smart
mode now keeps a battery empty until minSoc + 5 and full until socSet - 5; SOC
protection charges to minSoc + 5; the device's own small charge limit at its
lower bound is left alone and is not counted as a lost write.
"""

import asyncio
from datetime import timedelta

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import SOC_REARM_POINTS
from custom_components.zendure_restapi.measure import ECHO_SETTLE, BatteryMeasure

from .helpers import T0, make_controller, sent


def data(level, soc_limit=0, input_limit=0, min_soc=100, soc_set=1000):
    return {"electricLevel": level, "socLimit": soc_limit, "minSoc": min_soc, "socSet": soc_set,
            "inputLimit": input_limit, "outputLimit": 0, "smartMode": 1, "acMode": C.AC_MODE_CHARGE}


def controller():
    c = make_controller(C.MODE_SMART_MATCHING)
    c.settings.coordinated = False
    c.settings.group = None
    return c


def test_the_margin_is_five_points():
    assert SOC_REARM_POINTS == 5


def test_empty_is_held_until_min_soc_plus_five():
    c = controller()
    assert c._bounds(data(10, soc_limit=2)) == (False, True)
    for level in (11, 12, 14):
        assert c._bounds(data(level))[1], f"still empty at {level} %"
    assert c._bounds(data(15)) == (False, False)
    assert not c._empty_latch


def test_full_is_held_until_soc_set_minus_five():
    c = controller()
    assert c._bounds(data(100, soc_limit=1)) == (True, False)
    for level in (99, 97, 96):
        assert c._bounds(data(level))[0], f"still full at {level} %"
    assert c._bounds(data(95)) == (False, False)


def test_without_a_device_flag_nothing_is_held():
    c = controller()
    assert c._bounds(data(11)) == (False, False)


def test_the_status_says_where_it_opens_again():
    c = controller()
    c.coordinator.data = data(11)
    c._bounds(data(10, soc_limit=2))
    c._bounds(data(11))
    assert c._blocked("discharge").endswith("discharges again from 15 %")


def test_the_devices_own_charge_limit_at_empty_is_left_alone():
    c = controller()
    assert c._rest_limits(data(10, soc_limit=2, input_limit=27)) == [("outputLimit", 0)]
    assert c._rest_limits(data(10, soc_limit=2, input_limit=0)) == [("inputLimit", 0), ("outputLimit", 0)]
    assert c._rest_limits(data(10, soc_limit=2, input_limit=400)) == [("inputLimit", 0), ("outputLimit", 0)]
    assert c._rest_limits(data(50, soc_limit=0, input_limit=27)) == [("inputLimit", 0), ("outputLimit", 0)]


def test_soc_protection_charges_on_to_min_soc_plus_five(monkeypatch):
    c = controller()
    c._cap = lambda d, charging: 800
    c._meter_reading = lambda: (0.0, "test")
    c._stop_key = lambda: "test"
    monkeypatch.setattr(C, "_OPEN_LIMITS", set())

    async def no_guard(d, wanted):
        return False

    c._reverse_guard = no_guard

    def run(level):
        return asyncio.run(c._handle_soc_protection(data(level, input_limit=0), C.MODE_SMART_MATCHING))

    assert run(12) is False, "above the floor and not protecting: nothing to do"
    assert run(9) is True and c._protecting
    assert ("inputLimit", 800) in [kv for req in sent(c) for kv in req]
    assert run(12) is True, "carries on past the floor"
    assert run(14) is True
    assert run(15) is False and not c._protecting


def test_soc_protection_stands_down_in_standby_and_in_a_cluster():
    c = controller()
    c._protecting = True
    assert asyncio.run(c._handle_soc_protection(data(9), C.MODE_STANDBY)) is False
    assert not c._protecting
    c._protecting = True
    c.settings.coordinated = True
    assert asyncio.run(c._handle_soc_protection(data(9), C.MODE_SMART_MATCHING)) is False
    assert not c._protecting


def test_the_devices_own_charge_at_empty_is_no_lost_write():
    m = BatteryMeasure()
    m.on_request(T0, {"inputLimit": 0})
    m.check_echo(T0 + timedelta(seconds=ECHO_SETTLE + 1), {"inputLimit": 27}, soc_limit=2)
    assert m.lost_writes == 0
    # elsewhere it still counts
    m.on_request(T0 + timedelta(seconds=30), {"inputLimit": 0})
    m.check_echo(T0 + timedelta(seconds=30 + ECHO_SETTLE + 1), {"inputLimit": 27}, soc_limit=0)
    assert m.lost_writes == 1
