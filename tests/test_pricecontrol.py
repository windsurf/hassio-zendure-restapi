"""Price Control holds the last price list when the price sensor drops out.

A list once read carries on until its last period has passed. A fresh list
replaces it when it reaches at least as far ahead; an empty or shorter one
leaves the held list in use.
"""

from datetime import timedelta

from custom_components.zendure_restapi.pricecontrol import PriceControl

from .helpers import T0

DAY_START = T0.replace(hour=0)


def nord_pool(hours: int, start=DAY_START, value: float = 0.25) -> dict:
    """A Nord Pool sensor's attributes with `hours` hourly prices from `start`."""
    rows = [
        {
            "start": (start + timedelta(hours=h)).isoformat(),
            "end": (start + timedelta(hours=h + 1)).isoformat(),
            "value": value,
        }
        for h in range(hours)
    ]
    return {"raw_today": rows[:24], "raw_tomorrow": rows[24:]}


def make_price_control() -> PriceControl:
    pc = PriceControl.__new__(PriceControl)
    pc._held = []
    pc._held_format = None
    pc.prices_held = False
    return pc


def test_an_outage_keeps_the_list_until_it_runs_out():
    pc = make_price_control()
    today = nord_pool(24)
    assert len(pc._price_list(today, T0)) == 24 and not pc.prices_held
    # the sensor becomes unavailable: no attributes
    held = pc._price_list({}, T0 + timedelta(hours=2, minutes=40))
    assert len(held) == 24 and pc.prices_held
    assert pc._held_format == "nord_pool"
    # after the last period: nothing left to hold
    assert pc._price_list({}, DAY_START + timedelta(hours=24, minutes=1)) == []
    assert not pc.prices_held and pc._held == []


def test_a_shorter_list_leaves_the_held_one_in_use():
    pc = make_price_control()
    pc._price_list(nord_pool(48), T0)          # today and tomorrow
    used = pc._price_list(nord_pool(24), T0 + timedelta(minutes=15))   # tomorrow gone again
    assert len(used) == 48 and pc.prices_held


def test_a_list_that_reaches_as_far_replaces_the_held_one():
    pc = make_price_control()
    pc._price_list(nord_pool(24, value=0.25), T0)
    used = pc._price_list(nord_pool(24, value=0.30), T0 + timedelta(minutes=15))
    assert [p.value for p in used][:1] == [0.30] and not pc.prices_held


def test_the_new_day_at_midnight_is_taken_over():
    pc = make_price_control()
    pc._price_list(nord_pool(48), T0)          # yesterday's view: today and tomorrow
    midnight = DAY_START + timedelta(days=1)
    used = pc._price_list(nord_pool(24, start=midnight), midnight)   # the new today only
    assert used[0].start == midnight and not pc.prices_held
