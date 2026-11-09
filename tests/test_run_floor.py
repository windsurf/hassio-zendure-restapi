"""A running direction is not ended by one cycle with nothing to do (v1.7.5).

Seen on a Virtual cluster: after a 2.4 kW boiling-water peak the virtual P1
showed -2,041 W while the copy's measured power lagged a sample (1,920 W while
it delivered ~2,190 W). Grid + battery - buffer came out at -126 W, the limit
went to 0, and the next base load needed a new start after Start after: in
about one peak out of four.
"""

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import RUN_FLOOR_W

from .test_stop_below_deadband import battery, cycle, discharging


def test_after_a_peak_the_limit_stays_at_the_floor_not_zero(monkeypatch):
    c, clock, feed = battery(monkeypatch)
    data = discharging(611)
    feed.grid, feed.own = -2041.0, 1920.0   # the peak is over, the reading lags
    cycle(c, data)
    assert data["outputLimit"] == RUN_FLOOR_W, "kept running at the floor"
    assert c._last_direction == "discharge" and c._trim_direction == "discharge"
    # next cycle the reading has caught up: the base load is taken up again
    clock.advance(10)
    feed.grid, feed.own = 120.0, float(RUN_FLOOR_W)
    cycle(c, data)
    assert data["outputLimit"] > 100
    assert c._start_since is None and c.measure.skipped_peaks == 0


def test_a_second_cycle_with_nothing_to_do_ends_the_direction(monkeypatch):
    c, clock, feed = battery(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 0, C.OPT_START_AFTER: 0})
    data = discharging(400)
    feed.grid, feed.own = -400.0, 400.0      # the sun came out
    cycle(c, data)
    assert data["outputLimit"] == RUN_FLOOR_W
    clock.advance(10)
    feed.grid, feed.own = -770.0, float(RUN_FLOOR_W)
    cycle(c, data)
    assert data["outputLimit"] == 0, "real surplus: the direction ends one cycle later"


def test_with_real_demand_nothing_changes(monkeypatch):
    c, clock, feed = battery(monkeypatch)
    data = discharging(300)
    feed.grid, feed.own = 50.0, 300.0
    cycle(c, data)
    assert data["outputLimit"] == 345 and not c._zero_once
