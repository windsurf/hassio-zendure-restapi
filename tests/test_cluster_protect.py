"""SOC protection of a battery cluster (v1.7.6).

While one battery still has charge, a battery under its floor is not charged
from the grid: it would take what the other discharges (v1.7.0: 2400 W from one
into the other for 55 s). Once every battery is under its floor, none
discharges, and the cluster charges them all at their cap, each to its floor +
5; one that is there stands still until the last one is.
"""

from dataclasses import replace

from custom_components.zendure_restapi.cluster.contract import (
    CHARGE, QUICK_CHARGE, ROLE_PROTECT, ROLE_STANDBY, SMART_DISCHARGE, SMART_MATCHING, STANDBY,
    GroupSettings, Reading,
)
from custom_components.zendure_restapi.cluster.group import PROTECT_REARM_POINTS, Distributor
from custom_components.zendure_restapi.const import SOC_REARM_POINTS

SETTINGS = GroupSettings(
    mode=SMART_MATCHING, charge_buffer=5, discharge_buffer=5, start_charge_at=5,
    start_discharge_at=5, concentrate_below=300, redistribute_s=30, start_after=30,
    soc_protection=True,
)


def reading(key: str, soc: float, floor: float = 10.0, cap: int = 2400) -> Reading:
    return Reading(
        key=key, name=key.upper(), phase=1, priority=5, available=True, power=0.0,
        charge_limit=0, discharge_limit=0, charge_cap=cap, discharge_cap=cap,
        full=False, empty=soc <= floor, soc=soc, capacity_kwh=2.0, response_s=None,
        below_floor=soc < floor, floor=floor,
    )


def test_the_margin_matches_a_single_battery():
    assert PROTECT_REARM_POINTS == SOC_REARM_POINTS


def test_one_battery_with_charge_left_no_protection():
    d = Distributor()
    decision = d.decide([reading("a", 9), reading("b", 40)], SETTINGS, grid=300.0, now=0.0)
    assert decision.status != "protecting" and not d.protecting
    assert all(r.role != ROLE_PROTECT for r in decision.roles.values())


def test_all_under_the_floor_all_charge_at_their_cap():
    d = Distributor()
    decision = d.decide([reading("a", 9), reading("b", 8, cap=1200)], SETTINGS, grid=300.0, now=0.0)
    assert decision.status == "protecting" and d.protecting
    assert decision.roles["a"].role == ROLE_PROTECT and decision.roles["a"].direction == CHARGE
    assert decision.roles["a"].limit == 2400 and decision.roles["b"].limit == 1200
    assert "A to 15 %" in decision.reason and "B to 15 %" in decision.reason


def test_each_to_its_own_floor_plus_five_then_the_group_carries_on():
    d = Distributor()
    d.decide([reading("a", 9), reading("b", 9, floor=20.0)], SETTINGS, grid=300.0, now=0.0)
    decision = d.decide([reading("a", 15), reading("b", 19, floor=20.0)], SETTINGS, grid=300.0, now=2.0)
    assert decision.status == "protecting"
    assert decision.roles["a"].role == ROLE_STANDBY, "there: stands still until the last one is"
    assert decision.roles["b"].role == ROLE_PROTECT, "past its floor, on to 25 %"
    decision = d.decide([reading("a", 15), reading("b", 25, floor=20.0)], SETTINGS, grid=300.0, now=4.0)
    assert decision.status != "protecting" and not d.protecting


def test_switched_off_nothing():
    d = Distributor()
    off = replace(SETTINGS, soc_protection=False)
    decision = d.decide([reading("a", 9), reading("b", 8)], off, grid=300.0, now=0.0)
    assert decision.status != "protecting"


def test_not_in_standby_or_the_quick_modes():
    for mode in (STANDBY, QUICK_CHARGE):
        d = Distributor()
        decision = d.decide([reading("a", 9), reading("b", 8)], replace(SETTINGS, mode=mode), grid=300.0, now=0.0)
        assert decision.status != "protecting"
    d = Distributor()
    decision = d.decide([reading("a", 9), reading("b", 8)], replace(SETTINGS, mode=SMART_DISCHARGE),
                        grid=300.0, now=0.0)
    assert decision.status == "protecting", "as on a single battery: every smart mode"


def test_a_battery_that_does_not_take_part_does_not_count():
    d = Distributor()
    away = replace(reading("b", 60), available=False)
    decision = d.decide([reading("a", 9), away], SETTINGS, grid=300.0, now=0.0)
    assert decision.status == "protecting"
    assert decision.roles["b"].role == ROLE_STANDBY


def test_the_settings_default_is_off_for_the_contract():
    assert GroupSettings(
        mode=SMART_MATCHING, charge_buffer=0, discharge_buffer=0, start_charge_at=0,
        start_discharge_at=0, concentrate_below=0, redistribute_s=30,
    ).soc_protection is False
