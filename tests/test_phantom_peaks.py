"""Phantom peaks in the virtual P1 meter (v1.8.7): counted, not corrected.

The house load is the real grid plus the real battery's last reading. When the
battery changes its power, the grid shows it at once and the battery's reading
only at its next poll: for up to one poll the house load jumps by the
battery's own step. A jump the battery's next reading takes back is counted.
"""

from datetime import timedelta

from custom_components.zendure_restapi.virtual.meter import PHANTOM_S, VirtualMeter

from .helpers import T0


def _meter():
    meter = VirtualMeter.__new__(VirtualMeter)
    meter.phantom = {"day": None, "today": 0, "total": 0, "wh_today": 0.0}
    meter._candidate = None
    meter._last = None
    return meter


def _feed(meter, rows):
    for seconds, house, battery in rows:
        meter._phantom(T0 + timedelta(seconds=seconds), house, battery)


def test_a_jump_the_next_battery_reading_takes_back_is_a_phantom():
    meter = _meter()
    # The battery steps from 200 to 1200 W: the grid drops 1000 W at once,
    # so the house reads 1000 W low until the battery's poll catches up.
    _feed(meter, [(0, 400, 200), (1, 400, 200), (2, -600, 200), (3, -600, 200), (8, 400, 1200)])
    summary = meter.phantom_summary()
    assert summary["phantom_peaks_today"] == 1 and summary["phantom_peaks"] == 1
    assert 1.5 < summary["phantom_wh_today"] < 2.0      # 1000 W for 6 s


def test_a_real_step_of_the_house_is_not_a_phantom():
    meter = _meter()
    # A kettle: the house goes up and stays up, the battery follows later.
    _feed(meter, [(0, 400, 200), (1, 2400, 200), (5, 2400, 200), (10, 2400, 1500)])
    assert meter.phantom_summary()["phantom_peaks_today"] == 0


def test_a_jump_not_taken_back_in_time_is_dropped():
    meter = _meter()
    _feed(meter, [(0, 400, 200), (1, -600, 200), (2 + PHANTOM_S, 400, 1200)])
    assert meter.phantom_summary()["phantom_peaks_today"] == 0
