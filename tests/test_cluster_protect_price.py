"""SOC protection of a cluster at the cheapest price (v1.8.1).

With every battery under its floor and SOC protection on price_control, the
cluster waits for the cheapest period of its own Price Control within 6 hours,
as a single battery does since v1.7.6. Directly without a price list, after 6
hours, or once a battery sits 2 points or more under its floor. Once it
charges it carries on to the floor + 5, whatever the price.
"""

from dataclasses import replace

from custom_components.zendure_restapi.cluster.contract import ROLE_PROTECT, GroupSettings
from custom_components.zendure_restapi.cluster.group import PROTECT_HARD_POINTS, PROTECT_WINDOW_S, Distributor
from custom_components.zendure_restapi.const import (
    CLUSTER_SOC_PROTECT_MODES, SOC_PROTECT_HARD_POINTS, SOC_PROTECT_PRICE, SOC_PROTECT_WINDOW_H,
)

from .test_cluster_protect import SETTINGS, reading

H = 3600.0


def prices(*rows) -> tuple:
    """(start hour, price) rows of a quarter each, from t = 0."""
    return tuple((h * H, h * H + 900.0, price, f"{int(h):02d}:{int(h % 1 * 60):02d}") for h, price in rows)


PRICED = replace(SETTINGS, soc_protection_price=True,
                 price_periods=prices((0, 0.30), (1, 0.30), (3, 0.12), (5, 0.20), (7, 0.05)))
EMPTY = [reading("a", 9), reading("b", 9)]


def test_the_limits_match_a_single_battery():
    assert PROTECT_WINDOW_S == SOC_PROTECT_WINDOW_H * 3600 and PROTECT_HARD_POINTS == SOC_PROTECT_HARD_POINTS
    assert SOC_PROTECT_PRICE in CLUSTER_SOC_PROTECT_MODES


def test_a_cheaper_period_ahead_waits():
    d = Distributor()
    decision = d.decide(EMPTY, PRICED, grid=300.0, now=60.0)
    assert decision.status != "protecting" and not d.protecting
    assert all(r.role != ROLE_PROTECT for r in decision.roles.values())
    # The cheapest within 6 hours of the start, not the one at 07:00.
    assert decision.attributes["soc_protection_waits"] == (
        "every battery under its SOC floor: charges at 03:00, 0.12 per kWh (now 0.3)")


def test_at_the_cheapest_period_it_charges_and_carries_on():
    d = Distributor()
    d.decide(EMPTY, PRICED, grid=300.0, now=60.0)
    decision = d.decide(EMPTY, PRICED, grid=300.0, now=3 * H + 60.0)
    assert decision.status == "protecting" and "cheapest price within 6 h: 0.12 per kWh" in decision.reason
    assert "soc_protection_waits" not in decision.attributes
    # Past the cheap quarter, still under floor + 5: it carries on.
    decision = d.decide([reading("a", 12), reading("b", 11)], PRICED, grid=300.0, now=4 * H)
    assert decision.status == "protecting"


def test_without_prices_at_once():
    d = Distributor()
    decision = d.decide(EMPTY, replace(PRICED, price_periods=()), grid=300.0, now=60.0)
    assert decision.status == "protecting" and "direct: no prices from Price Control" in decision.reason


def test_two_points_under_the_floor_at_once():
    d = Distributor()
    decision = d.decide([reading("a", 8), reading("b", 9)], PRICED, grid=300.0, now=60.0)
    assert decision.status == "protecting" and "direct: 2 points or more under the floor" in decision.reason


def test_after_six_hours_at_once():
    d = Distributor()
    late = replace(PRICED, price_periods=prices((0, 0.30), (5.9, 0.10)))
    assert d.decide(EMPTY, late, grid=300.0, now=0.0).status != "protecting"
    decision = d.decide(EMPTY, late, grid=300.0, now=6 * H)
    assert decision.status == "protecting" and "direct: waited 6 h" in decision.reason


def test_back_over_the_floor_the_wait_ends():
    d = Distributor()
    d.decide(EMPTY, PRICED, grid=300.0, now=60.0)
    decision = d.decide([reading("a", 11), reading("b", 9)], PRICED, grid=-300.0, now=120.0)
    assert "soc_protection_waits" not in decision.attributes and d.protect_since is None


def test_direct_does_not_wait():
    d = Distributor()
    decision = d.decide(EMPTY, replace(PRICED, soc_protection_price=False), grid=300.0, now=60.0)
    assert decision.status == "protecting" and "direct" not in decision.reason and "cheapest" not in decision.reason


def test_settings_carry_the_prices_of_the_cluster_price_control():
    from types import SimpleNamespace

    from custom_components import zendure_restapi as Z
    from custom_components.zendure_restapi.pricecontrol import PriceControl
    from homeassistant.util import dt as dt_util

    pc = PriceControl.__new__(PriceControl)
    pc.group = True
    pc._battery_entry_id = "cluster1"
    start = dt_util.utcnow()
    pc.import_periods = lambda: [(start, start, 0.21)]
    hass = SimpleNamespace(data={"zendure_restapi": {"pc": pc}})
    settings = SimpleNamespace(get=lambda key: SOC_PROTECT_PRICE)
    out = Z.protection_settings(hass, "cluster1", settings)
    assert out["soc_protection"] and out["soc_protection_price"]
    assert out["price_periods"][0][2] == 0.21 and len(out["price_periods"][0]) == 4
    assert Z.protection_settings(hass, "other", settings)["price_periods"] == ()
    assert GroupSettings(mode="standby", charge_buffer=0, discharge_buffer=0, start_charge_at=0,
                         start_discharge_at=0, concentrate_below=0, redistribute_s=30).price_periods == ()
