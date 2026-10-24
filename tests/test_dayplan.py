"""Price lists from other integrations: read, and named in price_format.

A Tibber list comes from a template sensor filled by tibber.get_prices
(attribute prices, fields start_time and price). ENTSO-e and Frank Energie
keep the same family with another start field, and keep their own names.
"""

from datetime import timedelta

from custom_components.zendure_restapi.dayplan import price_format, read_prices

TIBBER = {
    "prices": [
        {"start_time": "2026-01-01T00:00:00.000+01:00", "price": 0.3439},
        {"start_time": "2026-01-01T00:15:00.000+01:00", "price": 0.3437},
    ]
}


def test_tibber_list_is_read_per_quarter():
    periods = read_prices(TIBBER)
    assert len(periods) == 2
    assert all(p.end - p.start == timedelta(minutes=15) for p in periods)
    assert [p.value for p in periods] == [0.3439, 0.3437]


def test_tibber_list_is_named():
    assert price_format(TIBBER) == "tibber"


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
