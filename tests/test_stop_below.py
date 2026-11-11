"""Stop below and Start after (v1.7.4): a running direction whose demand stays
under its stop limit for Stop after seconds stops, with both limits at 0 in one
request; a direction starts only once the demand has stayed past its start
threshold for Start after seconds, after a stop of its own past the larger of
the start threshold and the stop limit, also without a stop of its own (after a restart
of Home Assistant). At 0 W and 0 s nothing changes; a battery and a
cluster stop discharging and charging below 50 W after 30 s, and
start after 30 s, by default.
"""

import asyncio
from datetime import timedelta

from custom_components.zendure_restapi import controller as C

from .helpers import T0, make_controller, sent


class Settings:
    """The few settings the stop reads, as a battery or a cluster member sees them."""

    def __init__(self, mode=C.MODE_SMART_MATCHING, coordinated=False, group=None, **values):
        self.values = {
            C.OPT_OPERATION_MODE: mode,
            C.OPT_STOP_CHARGE_BELOW: 0,
            C.OPT_STOP_DISCHARGE_BELOW: 0,
            C.OPT_STOP_AFTER: 30,
            C.OPT_START_AFTER: 30,
            C.OPT_CONCENTRATE_BELOW: 300,
        }
        self.values.update(values)
        self.coordinated = coordinated
        self.group = group

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_int(self, key):
        return int(self.values[key])


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


def controller(monkeypatch, **values):
    c = make_controller(C.MODE_SMART_MATCHING)
    c.settings = Settings(**values)
    c._below_since = None
    c._below_dir = None
    c._stopped = None
    c._stopped_mode = None
    c._start_since = None
    c._start_dir = None
    c._trim_direction = "discharge"
    c._pending = 0.0
    clock = Clock()
    monkeypatch.setattr(C.dt_util, "utcnow", clock)
    return c, clock


def run(coro):
    return asyncio.run(coro)


DATA = {"smartMode": 1, "inputLimit": 0, "outputLimit": 60, "acMode": C.AC_MODE_DISCHARGE}


def test_off_by_default_writes_nothing(monkeypatch):
    c, clock = controller(monkeypatch)
    for _ in range(10):
        assert run(c._stop_below(dict(DATA), "discharge", 20)) is False
        clock.advance(10)
    assert sent(c) == []
    assert c._stopped is None


def test_stops_after_the_time_with_both_limits_in_one_request(monkeypatch):
    c, clock = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 100})
    assert run(c._stop_below(dict(DATA), "discharge", 60)) is False
    clock.advance(20)
    assert run(c._stop_below(dict(DATA), "discharge", 60)) is False
    assert sent(c) == [], "the trim loop follows the low demand until the time is up"
    clock.advance(10)
    assert run(c._stop_below(dict(DATA), "discharge", 60)) is True
    assert sent(c) == [[("outputLimit", 0)]], "the limit already at 0 is skipped"
    assert c._stopped == "discharge"
    assert c._last_direction == "none" and c._trim_direction is None and not c._owns_limits


def test_demand_back_above_the_limit_starts_the_time_again(monkeypatch):
    c, clock = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 100})
    run(c._stop_below(dict(DATA), "discharge", 60))
    clock.advance(20)
    assert run(c._stop_below(dict(DATA), "discharge", 150)) is False
    clock.advance(10)
    assert run(c._stop_below(dict(DATA), "discharge", 60)) is False
    clock.advance(20)
    assert run(c._stop_below(dict(DATA), "discharge", 60)) is False
    assert sent(c) == []


def test_zero_seconds_stops_at_once_and_the_limit_is_per_direction(monkeypatch):
    c, _ = controller(monkeypatch, **{C.OPT_STOP_CHARGE_BELOW: 100, C.OPT_STOP_AFTER: 0})
    assert run(c._stop_below(dict(DATA), "discharge", 60)) is False, "no limit for discharging"
    charging = {"smartMode": 1, "inputLimit": 60, "outputLimit": 0, "acMode": C.AC_MODE_CHARGE}
    assert run(c._stop_below(charging, "charge", 60)) is True
    assert sent(c) == [[("inputLimit", 0)]]


def test_after_a_stop_a_short_peak_does_not_start_it_again(monkeypatch):
    c, clock = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 100})
    c._stopped, c._stopped_mode = "discharge", C.MODE_SMART_MATCHING
    assert run(c._hold_start(80, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    # 2.4 kW for 11 s, measured every half hour on a quiet night
    assert run(c._hold_start(2400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(10)
    assert run(c._hold_start(2400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(10)
    # back to the base load, under the 100 W: the time starts again
    assert run(c._hold_start(80, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    assert c._start_since is None


def test_starts_again_after_the_time_above_the_larger_threshold(monkeypatch):
    c, clock = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 100})
    c._stopped, c._stopped_mode = "discharge", C.MODE_SMART_MATCHING
    # the start threshold of 150 W is above the stop limit: 120 W is not enough
    assert run(c._hold_start(120, C.MODE_SMART_MATCHING, 150, -5, True, True)) is True
    assert c._start_since is None
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 150, -5, True, True)) is True
    clock.advance(30)
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 150, -5, True, True)) is False


def test_the_other_direction_starts_as_any_start_does(monkeypatch):
    c, _ = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 100, C.OPT_START_AFTER: 0})
    c._stopped, c._stopped_mode = "discharge", C.MODE_SMART_MATCHING
    assert run(c._hold_start(-50, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False


def test_a_changed_mode_or_a_limit_back_at_zero_ends_the_stop(monkeypatch):
    c, _ = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 100, C.OPT_START_AFTER: 0})
    c._stopped, c._stopped_mode = "discharge", C.MODE_SMART_DISCHARGE
    # the stop of its own ends; a start still needs the demand past the stop limit
    assert run(c._hold_start(80, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    assert c._stopped is None
    assert run(c._hold_start(150, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    c._stopped, c._stopped_mode = "discharge", C.MODE_SMART_MATCHING
    c.settings.values[C.OPT_STOP_DISCHARGE_BELOW] = 0
    assert run(c._hold_start(80, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    assert c._stopped is None


def test_in_a_cluster_the_limit_is_capped_at_concentrate_below(monkeypatch):
    group = Settings(**{C.OPT_CONCENTRATE_BELOW: 200})
    c, _ = controller(monkeypatch, coordinated=True, group=group, **{C.OPT_STOP_DISCHARGE_BELOW: 250})
    assert c._stop_limit(False) == 200
    group.values[C.OPT_CONCENTRATE_BELOW] = 0
    assert c._stop_limit(False) == 0, "no concentrating, no stop: the primary's demand is only its share"


def test_a_cluster_and_a_battery_stop_below_50_w_discharging_and_charging():
    from types import SimpleNamespace

    from custom_components.zendure_restapi.const import (
        CONF_DEVICE_TYPE, DEVICE_TYPE_CLUSTER, DEVICE_TYPE_VIRTUAL,
    )
    from custom_components.zendure_restapi.settings import ZendureSettings

    def settings(data, options=None):
        return ZendureSettings(None, SimpleNamespace(data=data, options=options or {}))

    for kind in (DEVICE_TYPE_CLUSTER, DEVICE_TYPE_VIRTUAL):
        s = settings({CONF_DEVICE_TYPE: kind})
        assert (s.get_int(C.OPT_STOP_DISCHARGE_BELOW), s.get_int(C.OPT_STOP_CHARGE_BELOW),
                s.get_int(C.OPT_STOP_AFTER), s.get_int(C.OPT_START_AFTER)) == (50, 50, 30, 30)
        assert settings({CONF_DEVICE_TYPE: kind}, {C.OPT_STOP_DISCHARGE_BELOW: 0}).get_int(
            C.OPT_STOP_DISCHARGE_BELOW) == 0, "a value that was set stays"
    battery = settings({})
    assert (battery.get_int(C.OPT_STOP_DISCHARGE_BELOW), battery.get_int(C.OPT_STOP_CHARGE_BELOW),
            battery.get_int(C.OPT_STOP_AFTER), battery.get_int(C.OPT_START_AFTER)) == (50, 50, 30, 30)


def test_stop_after_on_a_cluster_has_the_range_of_redistribute_every():
    from custom_components.zendure_restapi.cluster_entities import CLUSTER_NUMBERS

    ranges = {key: (d.native_min_value, d.native_max_value, d.native_step) for d, key in CLUSTER_NUMBERS}
    assert ranges[C.OPT_STOP_AFTER] == ranges[C.OPT_START_AFTER] == ranges["redistribute_every"] == (10, 120, 5)


def test_start_after_holds_a_start_from_stand_still(monkeypatch):
    # No stop of its own: the start threshold alone, held for Start after.
    c, clock = controller(monkeypatch)
    assert run(c._hold_start(2200, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(10)
    # a 10 s peak is over: the time starts again
    assert run(c._hold_start(3, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    assert c._start_since is None
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(20)
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(10)
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False


def test_start_after_at_zero_starts_as_in_v173(monkeypatch):
    c, _ = controller(monkeypatch, **{C.OPT_START_AFTER: 0})
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    assert run(c._hold_start(-400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False


def test_start_after_counts_per_direction_and_only_where_allowed(monkeypatch):
    c, clock = controller(monkeypatch)
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(20)
    # the flow turns: charging starts its own time
    assert run(c._hold_start(-400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(20)
    assert run(c._hold_start(-400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    clock.advance(10)
    assert run(c._hold_start(-400, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    # discharging not allowed (empty, or smart_charge_only): nothing to hold
    c._start_since = None
    assert run(c._hold_start(400, C.MODE_SMART_MATCHING, 5, -5, False, True)) is False


def test_reset_settings_of_a_battery_keeps_mode_power_and_meter():
    from custom_components.zendure_restapi.const import BATTERY_RESET_KEYS, DEFAULTS

    reset = set(BATTERY_RESET_KEYS)
    assert {C.OPT_STOP_CHARGE_BELOW, C.OPT_STOP_DISCHARGE_BELOW, C.OPT_STOP_AFTER, C.OPT_START_AFTER,
            C.OPT_CHARGE_BUFFER, C.OPT_DISCHARGE_BUFFER, C.OPT_MODE_THRESHOLD, C.OPT_TRIM_THRESHOLD} <= reset
    for kept in (C.OPT_OPERATION_MODE, C.OPT_MAX_CHARGE_POWER, C.OPT_MAX_DISCHARGE_POWER, C.OPT_SOC_PROTECTION,
                 C.OPT_METER_SOURCE, "price_control"):
        assert kept not in reset
    assert all(key in DEFAULTS for key in reset), "every key falls back to a default"


def test_without_a_stop_of_its_own_a_start_still_waits_for_the_stop_limit(monkeypatch):
    # A fresh controller after a restart of Home Assistant knows of no stop:
    # starting at 80 W with a 100 W stop limit would stop again after Stop after.
    c, _ = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 100, C.OPT_START_AFTER: 0})
    assert c._stopped is None
    assert run(c._hold_start(80, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    # v1.7.6: the start lies a fifth above the stop limit, 100 -> 120 W
    assert "under the start threshold 120 W" in c._set_state.call_args.args[1]
    assert run(c._hold_start(150, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    # the charge side has no stop limit: it starts as before
    assert run(c._hold_start(-50, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
    # within both start thresholds nothing is held
    assert run(c._hold_start(3, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False
