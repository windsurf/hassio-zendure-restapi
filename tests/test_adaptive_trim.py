"""Adaptive trim threshold: on a noisy meter the trim loop stops chasing the noise (v1.8.4).

The trim loop ignores a step smaller than twice the meter's median step per
sample over the last 300 samples, once 60 are in; Trim threshold stays the
floor. A quiet meter (a few watts a second) keeps the setting; a noisy one
(steps of ~90 W a second) gets ~180 W. A change of meter empties the window.
"""

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import (
    DEFAULTS,
    NOISE_FACTOR,
    NOISE_MIN_SAMPLES,
    NOISE_WINDOW,
    OPT_RAISE_AFTER,
    OPT_TRIM_THRESHOLD,
)

from .test_raise_after import trim
from .test_stop_below_deadband import battery as _battery
from .test_stop_below_deadband import discharging


def battery(monkeypatch, **values):
    return _battery(monkeypatch, **{
        OPT_TRIM_THRESHOLD: DEFAULTS[OPT_TRIM_THRESHOLD], C.OPT_TRIM_FACTOR: DEFAULTS[C.OPT_TRIM_FACTOR], **values})


def feed_steps(c, feed, steps, start=200.0):
    """Meter samples that alternate up and down by the given steps."""
    value = start
    for i, step in enumerate(steps):
        value += step if i % 2 == 0 else -step
        feed.grid = value
        c._sample_noise()


def test_the_constants():
    assert (NOISE_FACTOR, NOISE_WINDOW, NOISE_MIN_SAMPLES) == (2.0, 300, 60)


def test_a_quiet_meter_keeps_the_setting(monkeypatch):
    c, _, feed = battery(monkeypatch)
    feed_steps(c, feed, [2, 3, 1, 4] * 50)
    assert c.meter_noise() is not None and c.meter_noise() < 5
    assert c._trim_threshold() == DEFAULTS[OPT_TRIM_THRESHOLD]


def test_a_noisy_meter_raises_the_threshold(monkeypatch):
    c, _, feed = battery(monkeypatch)
    feed_steps(c, feed, [80, 100, 90, 95] * 50)
    # The first sample has no step yet: 199 steps, one 80 fewer, median 95.
    assert c.meter_noise() == 95
    assert c._trim_threshold() == 190


def test_not_before_enough_samples(monkeypatch):
    c, _, feed = battery(monkeypatch)
    feed_steps(c, feed, [90] * (NOISE_MIN_SAMPLES - 1))
    assert c.meter_noise() is None
    assert c._trim_threshold() == DEFAULTS[OPT_TRIM_THRESHOLD]


def test_one_big_peak_does_not_move_the_median(monkeypatch):
    c, _, feed = battery(monkeypatch)
    feed_steps(c, feed, [3] * 150 + [2200, 2200] + [3] * 150)
    assert c._trim_threshold() == DEFAULTS[OPT_TRIM_THRESHOLD]


def test_a_higher_setting_wins(monkeypatch):
    c, _, feed = battery(monkeypatch, **{OPT_TRIM_THRESHOLD: 300})
    feed_steps(c, feed, [90] * 100)
    assert c._trim_threshold() == 300


def test_a_change_of_meter_empties_the_window(monkeypatch):
    c, _, feed = battery(monkeypatch)
    feed_steps(c, feed, [90] * 100)
    assert c.meter_noise() is not None
    c._meter_reading = lambda: (feed.grid, "other")
    c._sample_noise()
    assert c.meter_noise() is None


def test_the_trim_loop_ignores_a_step_within_the_noise(monkeypatch):
    c, _, feed = battery(monkeypatch)
    feed_steps(c, feed, [90] * 100)
    data = discharging(400)
    feed.grid = 120.0  # 120 W short: more than 40 W, less than ~180 W of noise
    c._noise_prev = feed.grid
    trim(c, data)
    assert c.coordinator.api.async_write_properties.await_count + c.coordinator.api.async_write_property.await_count == 0
    assert c._trim_threshold() == 180


def test_the_trim_loop_still_follows_a_real_step(monkeypatch):
    c, _, feed = battery(monkeypatch, **{OPT_RAISE_AFTER: 0})
    feed_steps(c, feed, [90] * 100)
    data = discharging(400)
    feed.grid = 600.0
    c._noise_prev = feed.grid
    trim(c, data)
    assert c.coordinator.api.async_write_properties.await_count + c.coordinator.api.async_write_property.await_count >= 1


def test_mode_threshold_advice(monkeypatch):
    """Advice only (v1.8.7): the upper quartile of the step, rounded up to 5 W,
    never under the default Mode threshold; nothing is set."""
    c, _, feed = battery(monkeypatch)
    assert c.mode_threshold_advice() is None
    feed_steps(c, feed, [2, 3, 1, 4] * 50)
    assert c.mode_threshold_advice() == DEFAULTS[C.OPT_MODE_THRESHOLD]
    c, _, feed = battery(monkeypatch)
    before = c.settings.get_int(C.OPT_MODE_THRESHOLD)
    feed_steps(c, feed, [80, 100, 90, 95] * 50)
    assert c.mode_threshold_advice() == 100
    assert c.settings.get_int(C.OPT_MODE_THRESHOLD) == before
