"""Price lists from other integrations: read, and named in price_format.

Since v1.7.5 only Nord Pool, EPEX Spot, Zonneplan, ENTSO-e and Frank Energie
are named. A list with the same fields from any other source (such as a
template sensor with start_time and price) is still read, and named "other".
"""

from datetime import timedelta

from custom_components.zendure_restapi.dayplan import price_format, read_prices

TEMPLATE = {
    "prices": [
        {"start_time": "2026-01-01T00:00:00.000+01:00", "price": 0.3439},
        {"start_time": "2026-01-01T00:15:00.000+01:00", "price": 0.3437},
    ]
}


def test_a_start_time_list_is_read_per_quarter():
    periods = read_prices(TEMPLATE)
    assert len(periods) == 2
    assert all(p.end - p.start == timedelta(minutes=15) for p in periods)
    assert [p.value for p in periods] == [0.3439, 0.3437]


def test_a_start_time_list_is_named_other():
    assert price_format(TEMPLATE) == "other"


def test_an_hour_list_is_still_read_and_named_other():
    attributes = {
        "raw_today": [
            {"hour": "2026-01-01T00:00:00+01:00", "price": 1.10},
            {"hour": "2026-01-01T01:00:00+01:00", "price": 1.20},
        ]
    }
    assert [p.value for p in read_prices(attributes)] == [1.10, 1.20]
    assert price_format(attributes) == "other"


def test_entso_e_keeps_its_name():
    attributes = {
        "prices_today": [
            {"time": "2026-01-01T00:00:00+01:00", "price": 0.10},
            {"time": "2026-01-01T01:00:00+01:00", "price": 0.11},
        ]
    }
    assert price_format(attributes) == "entso_e"


def test_frank_energie_keeps_its_name():
    attributes = {
        "prices": [
            {"from": "2026-01-01T00:00:00+01:00", "till": "2026-01-01T01:00:00+01:00", "price": 0.25},
        ]
    }
    assert price_format(attributes) == "frank_energie"
