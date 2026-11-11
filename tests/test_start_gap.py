"""A gap at the start (v1.7.6).

With a stop limit set, a direction starts only a fifth above it, at least
20 W. Until v1.7.5 the start lay at the stop limit itself: a load hovering just
above it, 55 W on a 50 W limit, started the battery and stopped it again after
Stop after, and again.
"""

import asyncio

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import START_GAP_MIN_W, START_GAP_SHARE

from .test_stop_below import controller


def run(coro):
    return asyncio.run(coro)


def test_the_gap_is_a_fifth_at_least_20_w(monkeypatch):
    assert (START_GAP_SHARE, START_GAP_MIN_W) == (0.20, 20)
    for stop, start in ((50, 70), (80, 100), (100, 120), (250, 300), (10, 30)):
        c, _ = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: stop, C.OPT_STOP_CHARGE_BELOW: stop})
        assert c._start_threshold(False, 5) == start
        assert c._start_threshold(True, 5) == start


def test_without_a_stop_limit_nothing_changes(monkeypatch):
    c, _ = controller(monkeypatch)
    assert c._start_threshold(False, 5) == 5
    assert c._start_threshold(True, 30) == 30


def test_a_start_threshold_above_the_gap_still_counts(monkeypatch):
    c, _ = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 50})
    assert c._start_threshold(False, 150) == 150


def test_55_w_on_a_50_w_limit_no_longer_starts(monkeypatch):
    c, _ = controller(monkeypatch, **{C.OPT_STOP_DISCHARGE_BELOW: 50, C.OPT_START_AFTER: 0})
    assert run(c._hold_start(55, C.MODE_SMART_MATCHING, 5, -5, True, True)) is True
    assert "under the start threshold 70 W" in c._set_state.call_args.args[1]
    assert run(c._hold_start(75, C.MODE_SMART_MATCHING, 5, -5, True, True)) is False


def test_in_a_cluster_the_gap_follows_the_capped_limit(monkeypatch):
    from .test_stop_below import Settings

    group = Settings(**{C.OPT_CONCENTRATE_BELOW: 200})
    c, _ = controller(monkeypatch, coordinated=True, group=group, **{C.OPT_STOP_DISCHARGE_BELOW: 250})
    assert c._start_threshold(False, 5) == 240
