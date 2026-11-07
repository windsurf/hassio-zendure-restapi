"""Stop below under the deadband (v1.7.4): a battery that runs under 60 W is
idle (a change of direction is safe) but still running. Its stop limit is
checked all the same, and Start after does not count it as a new start.

Found in a replay: 40 W of demand on a 50 W stop limit ran on for 100 s and
longer, because the stop was only checked while the battery delivered 60 W or
more. These tests go through the whole cycle (_apply_smart), not through
_stop_below alone as test_stop_below does.
"""

import asyncio
from datetime import timedelta

from custom_components.zendure_restapi import controller as C

from .helpers import T0, make_controller, sent


class Settings:
    def __init__(self, **values):
        self.values = {
            C.OPT_OPERATION_MODE: C.MODE_SMART_MATCHING,
            C.OPT_STOP_CHARGE_BELOW: 80,
            C.OPT_STOP_DISCHARGE_BELOW: 50,
            C.OPT_STOP_AFTER: 30,
            C.OPT_START_AFTER: 30,
            C.OPT_START_DISCHARGE_AT: 5,
            C.OPT_START_CHARGE_AT: 5,
            C.OPT_DISCHARGE_BUFFER: 5,
            C.OPT_CHARGE_BUFFER: 5,
            C.OPT_MIN_DISCHARGE_POWER: 0,
            C.OPT_MIN_CHARGE_POWER: 0,
            C.OPT_MODE_THRESHOLD: 40,
        }
        self.values.update(values)
        self.coordinated = False
        self.group = None

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_int(self, key):
        return int(self.values.get(key, 0))


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Feed:
    """What the meter and the battery read on the next cycle."""

    grid = 0.0
    own = 0.0


def battery(monkeypatch, **values):
    c = make_controller(C.MODE_SMART_MATCHING)
    c.settings = Settings(**values)
    for name, value in {
        "_below_since": None, "_below_dir": None, "_stopped": None, "_stopped_mode": None,
        "_start_since": None, "_start_dir": None, "_start_wh": 0.0, "_start_last": None,
        "_trim_direction": None, "_pending": 0.0, "_hold": 0, "_resting": False,
        "_fast_write_at": None, "_aside_logged": False, "_idle_since": None,
        "_prev_grid": None, "_commanded": 0, "_idle_flag": False, "start_check": None,
        "waiting_for": None, "virtual": False, "_last_direction": "discharge",
        "_owns_limits": True,
    }.items():
        setattr(c, name, value)
    c.coordinator.fetch_started_at = None
    feed = Feed()
    c._claim_grid = lambda: None
    c._meter_power = lambda: feed.grid
    c._meter_reading = lambda: (feed.grid, "test")
    c._battery_power = lambda data: feed.own
    c._bounds = lambda data: (False, False)
    c._cap = lambda data, charging: 2400
    c._hold_cycles = lambda: 0
    c._stop_key = lambda: "test"

    async def no_guard(data, wanted):
        return False

    c._reverse_guard = no_guard
    clock = Clock()
    monkeypatch.setattr(C.dt_util, "utcnow", clock)
    monkeypatch.setattr(C, "_OPEN_LIMITS", set())
    return c, clock, feed


def cycle(c, data):
    asyncio.run(c._apply_smart(data, C.MODE_SMART_MATCHING))


def discharging(limit):
    return {"smartMode": 1, "inputLimit": 0, "outputLimit": limit, "acMode": C.AC_MODE_DISCHARGE}


def charging(limit):
    return {"smartMode": 1, "inputLimit": limit, "outputLimit": 0, "acMode": C.AC_MODE_CHARGE}


def test_discharging_40_w_under_a_50_w_limit_stops_after_stop_after(monkeypatch):
    c, clock, feed = battery(monkeypatch)
    data = discharging(40)
    feed.grid, feed.own = 5.0, 40.0
    for _ in range(3):
        cycle(c, data)
        clock.advance(10)
    assert data["outputLimit"] == 40, "runs on until Stop after is up"
    cycle(c, data)
    assert data["outputLimit"] == 0 and data["inputLimit"] == 0
    assert c._stopped == "discharge"


def test_discharging_59_w_with_a_little_import_stops_as_well(monkeypatch):
    c, clock, feed = battery(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 80})
    data = discharging(59)
    feed.grid, feed.own = 3.0, 59.0
    for _ in range(4):
        cycle(c, data)
        clock.advance(10)
    assert c._stopped == "discharge" and data["outputLimit"] == 0


def test_above_the_deadband_nothing_changes(monkeypatch):
    c, clock, feed = battery(monkeypatch)
    data = discharging(61)
    feed.grid, feed.own = 5.0, 61.0
    for _ in range(6):
        cycle(c, data)
        clock.advance(10)
    assert c._stopped is None and data["outputLimit"] == 61


def test_charging_50_w_under_an_80_w_limit_stops(monkeypatch):
    c, clock, feed = battery(monkeypatch)
    c._last_direction = "charge"
    data = charging(50)
    feed.grid, feed.own = 0.0, -50.0
    for _ in range(4):
        cycle(c, data)
        clock.advance(10)
    assert c._stopped == "charge" and data["inputLimit"] == 0


def test_a_running_battery_under_the_deadband_is_no_skipped_peak(monkeypatch):
    c, clock, feed = battery(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 0})
    data = discharging(40)
    feed.grid, feed.own = 120.0, 40.0
    cycle(c, data)
    assert c._start_since is None, "Start after does not count a battery that runs"
    assert data["outputLimit"] > 40, "the running direction is balanced at once"
    feed.grid = 5.0
    clock.advance(10)
    cycle(c, data)
    assert c.measure.skipped_peaks == 0


def test_surplus_under_the_deadband_still_changes_direction_at_once(monkeypatch):
    c, clock, feed = battery(monkeypatch, **{C.OPT_START_AFTER: 0})
    data = discharging(40)
    feed.grid, feed.own = -400.0, 40.0
    cycle(c, data)
    assert data["acMode"] == C.AC_MODE_CHARGE and data["inputLimit"] > 0
    assert data["outputLimit"] == 0
    assert sent(c), "written in this cycle, as before"
