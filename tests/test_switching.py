"""Skipped peaks and relay switches (v1.7.4).

Skipped peaks: starts that Start after held back and that never came, because
the demand fell back in time (a kettle tap of 10 s). Relay switches: starts from
standstill and changes of direction the integration ordered; a run that stops
or turns again within two minutes is short, the trace of hunting.
"""

import asyncio
from datetime import timedelta

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.measure import BatteryMeasure

from .helpers import T0, make_controller
from .test_stop_below import controller


def run(coro):
    return asyncio.run(coro)


def test_a_short_run_and_a_long_run():
    m = BatteryMeasure()
    m.on_relay(T0, "2026-10-26", "start")
    m.on_run_end(T0 + timedelta(seconds=60), "2026-10-26")
    m.on_relay(T0 + timedelta(seconds=100), "2026-10-26", "start")
    m.on_relay(T0 + timedelta(seconds=400), "2026-10-26", "reversal")
    m.on_run_end(T0 + timedelta(seconds=430), "2026-10-26")
    relay = m.switching["relay"]
    assert m.relay_switches == 3
    assert (relay["starts_today"], relay["reversals_today"]) == (2, 1)
    assert relay["short_today"] == 2, "60 s and 30 s are short, 300 s is not"


def test_today_starts_again_the_totals_stay_and_survive_a_restart():
    m = BatteryMeasure()
    m.on_skipped_peak("2026-10-25", "discharge", 10.0, 6.1)
    m.on_relay(T0, "2026-10-25", "start")
    m.on_skipped_peak("2026-10-26", "charge", 12.0, 2.0)
    assert m.skipped_peaks == 2 and m.switching["skipped"]["today"] == 1
    assert m.switching["skipped"]["charge_today"] == 1 and m.switching["skipped"]["discharge_today"] == 0
    assert m.relay_switches == 1 and m.switching["relay"]["today"] == 0
    again = BatteryMeasure()
    again.load(m.to_dict())
    assert again.switching == m.switching


def test_a_peak_shorter_than_start_after_is_a_skipped_peak(monkeypatch):
    c, clock = controller(monkeypatch)
    assert run(c._hold_start(2400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(10)
    assert run(c._hold_start(2400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    # the tap is done: back to the base load, under the start threshold
    assert run(c._hold_start(3, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    skipped = c.measure.switching["skipped"]
    assert c.measure.skipped_peaks == 1 and skipped["discharge_today"] == 1
    assert skipped["longest_s_today"] == 10.0
    assert abs(skipped["wh_today"] - 2400 * 10 / 3600) < 0.2


def test_a_start_after_the_time_is_not_a_skipped_peak(monkeypatch):
    c, clock = controller(monkeypatch)
    run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, True, True))
    clock.advance(30)
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    assert c.measure.skipped_peaks == 0


def test_direction_writes_count_starts_and_reversals(monkeypatch):
    c = make_controller(C.MODE_SMART_MATCHING)
    c.coordinator.api.async_write_properties.return_value = None
    now = [T0]
    monkeypatch.setattr(C.dt_util, "utcnow", lambda: now[0])
    data = {"smartMode": 0, "inputLimit": 0, "outputLimit": 0, "acMode": C.AC_MODE_CHARGE}
    run(c._write_direction(data, C.AC_MODE_DISCHARGE, 300))
    now[0] += timedelta(seconds=10)
    run(c._write_direction(data, C.AC_MODE_DISCHARGE, 250))       # a step, no switch
    now[0] += timedelta(seconds=40)
    run(c._write_direction(data, C.AC_MODE_CHARGE, 400))          # turns after 50 s: short
    now[0] += timedelta(seconds=300)
    run(c._write_many([("inputLimit", 0), ("outputLimit", 0)], data))
    relay = c.measure.switching["relay"]
    assert c.measure.relay_switches == 2
    assert (relay["starts_today"], relay["reversals_today"], relay["short_today"]) == (1, 1, 1)
