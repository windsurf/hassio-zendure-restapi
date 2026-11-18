"""Raise after: a running discharge goes up for a higher demand only once it has lasted (v1.8.3).

The diagnostics count a rise as a peak from 500 W (RISE_MIN_W); holding is not affected.

Measured on a 3000 Mix AC+ in smart_matching at ~145 W: a 2.2 kW kettle tap of
about 10 s was followed 4 s late (a written limit lands after about 4 s, in one
step), and the battery then exported -2.1 kW for about 4 s while it came back
down; 7 writes, ~2.7 Wh imported, ~2.9 Wh exported per peak over 13 peaks.
Now a rise of more than the margin (100 W or a fifth of the limit) waits Raise
after (15 s by default); lower and small rises go at once. A rise that falls
back in time is a skipped peak, while running. Raise after 0 is v1.8.2.
"""

import asyncio

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import DEFAULTS, OPT_RAISE_AFTER

from .test_stop_below_deadband import battery, charging, cycle, discharging


def run(monkeypatch, raise_after=15, **values):
    return battery(monkeypatch, **{OPT_RAISE_AFTER: raise_after, C.OPT_STOP_DISCHARGE_BELOW: 0, **values})


def trim(c, data):
    c._source_used = c._trim_source = "test"
    c._trim_writes = getattr(c, "_trim_writes", 0)
    c._trim_direction = "discharge" if data["outputLimit"] else "charge"
    c.coordinator.data = data
    asyncio.run(c._trim())


def test_default_is_fifteen_seconds():
    assert DEFAULTS[OPT_RAISE_AFTER] == 15


def test_a_short_peak_is_held_and_counted_as_skipped_while_running(monkeypatch):
    c, clock, feed = run(monkeypatch)
    data = discharging(150)
    for _ in range(5):                       # a 10 s kettle tap on top of 150 W
        feed.grid, feed.own = 2200.0, 150.0
        cycle(c, data)
        assert data["outputLimit"] == 150, "held at the running limit"
        clock.advance(2)
    feed.grid, feed.own = 5.0, 150.0         # the tap is over
    cycle(c, data)
    assert data["outputLimit"] == 150
    skipped = c.measure.switching["skipped"]
    assert c.measure.skipped_peaks == 1 and skipped["while_running_today"] == 1
    assert skipped["discharge_today"] == 1 and skipped["wh_today"] > 4


def test_a_lasting_load_is_followed_after_raise_after(monkeypatch):
    c, clock, feed = run(monkeypatch)
    data = discharging(150)
    feed.grid, feed.own = 1500.0, 150.0
    for _ in range(8):                       # 14 s: still held
        cycle(c, data)
        clock.advance(2)
    assert data["outputLimit"] == 150
    clock.advance(2)
    cycle(c, data)                           # 16 s: the oven is real
    assert data["outputLimit"] > 1000
    assert c.measure.skipped_peaks == 0


def test_lower_and_small_rises_go_at_once(monkeypatch):
    c, clock, feed = run(monkeypatch)
    data = discharging(400)
    feed.grid, feed.own = 60.0, 400.0        # +55 W: within the margin
    cycle(c, data)
    assert data["outputLimit"] == 455
    clock.advance(2)
    feed.grid, feed.own = -200.0, 455.0      # less demand: down at once
    cycle(c, data)
    assert data["outputLimit"] < 455


def test_the_margin_is_a_fifth_of_a_large_limit():
    assert C.ZendureController._raise_margin(150) == 100
    assert C.ZendureController._raise_margin(2000) == 400


def test_raise_after_zero_follows_at_once_as_before(monkeypatch):
    c, clock, feed = run(monkeypatch, raise_after=0)
    data = discharging(150)
    feed.grid, feed.own = 2200.0, 150.0
    cycle(c, data)
    assert data["outputLimit"] > 2000


def test_charging_is_not_held(monkeypatch):
    c, clock, feed = run(monkeypatch)
    c._last_direction = "charge"
    data = charging(300)
    feed.grid, feed.own = -1500.0, -300.0    # a sunny spell: taken at once
    cycle(c, data)
    assert data["inputLimit"] > 1000


def test_the_trim_loop_holds_too(monkeypatch):
    c, clock, feed = run(monkeypatch)
    c._trim_threshold = lambda: 10
    c._trim_factor = lambda: 1.0
    data = discharging(150)
    feed.grid = 2200.0
    trim(c, data)
    assert data["outputLimit"] == 150
    clock.advance(16)
    trim(c, data)
    assert data["outputLimit"] > 2000


def test_a_followed_peak_is_counted_with_its_export(monkeypatch):
    c, clock, feed = run(monkeypatch, raise_after=0)
    c._trim_threshold = lambda: 10
    c._trim_factor = lambda: 1.0
    data = discharging(150)
    feed.grid = 5.0
    trim(c, data)                            # settled: the base is 150 W
    clock.advance(1)
    feed.grid = 2200.0
    trim(c, data)                            # followed up at once
    assert data["outputLimit"] > 2000
    clock.advance(5)
    feed.grid = -2100.0                      # the tap is over, the battery exports
    trim(c, data)
    clock.advance(1)
    feed.grid = 5.0
    data["outputLimit"] = 150                # back at the base load
    trim(c, data)
    followed = c.measure.switching["followed"]
    assert c.measure.followed_peaks == 1
    assert followed["export_wh_today"] > 0 and followed["longest_s_today"] <= 10


def test_a_long_load_is_not_a_followed_peak(monkeypatch):
    c, clock, feed = run(monkeypatch, raise_after=0)
    c._trim_threshold = lambda: 10
    c._trim_factor = lambda: 1.0
    data = discharging(150)
    feed.grid = 5.0
    trim(c, data)
    feed.grid = 1500.0
    clock.advance(1)
    trim(c, data)
    feed.grid = 5.0
    for _ in range(14):                      # an oven for 70 s, covered
        clock.advance(5)
        trim(c, data)
    data["outputLimit"] = 150
    clock.advance(1)
    trim(c, data)
    assert c.measure.followed_peaks == 0


def test_a_small_held_rise_is_not_counted_as_a_skipped_peak(monkeypatch):
    """A fridge starting on a 150 W base load is held like any rise above the
    margin, but it is no peak: a night counted 52 of those at 0-0.7 Wh."""
    c, clock, feed = run(monkeypatch)
    data = discharging(150)
    for _ in range(3):                       # +270 W for 6 s: above the margin, under 500 W
        feed.grid, feed.own = 275.0, 150.0
        cycle(c, data)
        assert data["outputLimit"] == 150, "held all the same"
        clock.advance(2)
    feed.grid = 5.0
    cycle(c, data)
    assert c.measure.skipped_peaks == 0
    assert c.measure.switching["skipped"]["while_running_today"] == 0


def test_a_small_rise_and_a_charging_rise_are_no_followed_peaks(monkeypatch):
    """A meter that jumps 100-200 W between samples made 45 followed peaks in
    22 minutes on another installation; charging follows clouds, not the house."""
    c, clock, feed = run(monkeypatch, raise_after=0)
    c._trim_threshold = lambda: 10
    c._trim_factor = lambda: 1.0
    data = discharging(150)
    feed.grid = 5.0
    trim(c, data)
    for grid in (330.0, -320.0, 5.0):        # +325 W and back: within a second
        clock.advance(1)
        feed.grid = grid
        trim(c, data)
    assert c.measure.followed_peaks == 0
    c._last_direction = "charge"
    data = charging(300)
    feed.grid = -5.0
    trim(c, data)
    for grid in (-2200.0, 2100.0, -5.0):     # a cloud and the sun back: not a peak
        clock.advance(1)
        feed.grid = grid
        trim(c, data)
    assert c.measure.followed_peaks == 0


def test_the_cluster_status_carries_the_totals_of_its_batteries():
    from types import SimpleNamespace

    from custom_components.zendure_restapi.cluster_entities import peak_totals
    from custom_components.zendure_restapi.measure import BatteryMeasure

    today = C.dt_util.now().date().isoformat()
    one, two = BatteryMeasure(), BatteryMeasure()
    one.on_skipped_peak(today, "discharge", 8, 4.0, running=True)
    two.on_followed_peak(today, 9, 3.2, 2.7)
    two.on_followed_peak(today, 7, 1.0, 1.0)
    members = {k: SimpleNamespace(controller=SimpleNamespace(measure=m)) for k, m in (("a", one), ("b", two))}
    real = SimpleNamespace(members=members)
    virtual = SimpleNamespace(cluster=real)
    for cluster in (real, virtual):
        assert peak_totals(cluster) == {"skipped_peaks": 1, "skipped_peaks_today": 1,
                                        "followed_peaks": 2, "followed_peaks_today": 2}


def test_a_battery_in_a_cluster_takes_raise_after_from_the_cluster():
    """Raise after is a setting of the group, as Start after: the primary and
    every copy of a Virtual cluster read the cluster's value while they take part."""
    from custom_components.zendure_restapi.const import COORDINATED_KEYS
    from custom_components.zendure_restapi.settings import ROLE_PRIMARY, SettingsView
    from custom_components.zendure_restapi.virtual.cluster import MemorySettings

    assert OPT_RAISE_AFTER in COORDINATED_KEYS
    own = MemorySettings({OPT_RAISE_AFTER: 0})
    group = MemorySettings({OPT_RAISE_AFTER: 30})
    view = SettingsView(own)
    assert view.get_int(OPT_RAISE_AFTER) == 0, "alone: its own setting"
    view.group, view.role = group, ROLE_PRIMARY
    assert view.get_int(OPT_RAISE_AFTER) == 30, "in the cluster: the cluster's"
    view.group = view.role = None
    assert view.get_int(OPT_RAISE_AFTER) == 0


def test_the_cluster_has_a_raise_after_and_its_reset_takes_it_back():
    from custom_components.zendure_restapi.cluster_entities import CLUSTER_NUMBERS
    from custom_components.zendure_restapi.virtual.cluster import VIRTUAL_RESET_KEYS

    found = {key: d for d, key in CLUSTER_NUMBERS}
    d = found[OPT_RAISE_AFTER]
    assert (d.native_min_value, d.native_max_value, d.native_step) == (0, 120, 5)
    keys = [key for _, key in CLUSTER_NUMBERS]
    assert keys.index(OPT_RAISE_AFTER) == keys.index(C.OPT_START_AFTER) + 1, "after Start after"
    assert OPT_RAISE_AFTER in VIRTUAL_RESET_KEYS
    assert MemoryDefault().get_int(OPT_RAISE_AFTER) == 15


def MemoryDefault():
    from custom_components.zendure_restapi.virtual.cluster import MemorySettings

    return MemorySettings({})
