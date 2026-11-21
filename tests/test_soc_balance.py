"""SoC balance: the battery with the most energy works, the efficiency stays (v1.8.5).

As few batteries at once as before (Concentrate below, the primary's share);
the SoC only decides which one. When a direction starts, the battery with the
most energy for it becomes primary, after priority, if its SoC leads the
current primary by more than SoC balance (15 points). A primary that runs is
not replaced for its SoC. The secondaries share the same total by energy.
SoC balance 0 is the choice of v1.8.4.
"""

from dataclasses import replace

from custom_components.zendure_restapi.cluster.contract import (
    CHARGE, DISCHARGE, ROLE_PRIMARY, SMART_MATCHING, GroupSettings, Reading,
)
from custom_components.zendure_restapi.cluster.group import Distributor, split
from custom_components.zendure_restapi.cluster_entities import CLUSTER_NUMBERS
from custom_components.zendure_restapi.const import DEFAULTS, OPT_CONCENTRATE_BELOW, OPT_SOC_BALANCE
from custom_components.zendure_restapi.virtual.cluster import VIRTUAL_RESET_KEYS

ON = GroupSettings(
    mode=SMART_MATCHING, charge_buffer=0, discharge_buffer=0, start_charge_at=5,
    start_discharge_at=5, concentrate_below=0, redistribute_s=30, soc_balance=15,
)
OFF = replace(ON, soc_balance=0)


def reading(key, soc, *, priority=5, out=0, inp=0, cap=800, capacity=8.0) -> Reading:
    return Reading(
        key=key, name=key, phase=1, priority=priority, available=True, power=float(out - inp),
        charge_limit=inp, discharge_limit=out, charge_cap=cap, discharge_cap=cap,
        full=soc >= 100, empty=False, soc=float(soc), capacity_kwh=capacity, response_s=2.0, floor=5.0,
    )


def primary(decision):
    return next(k for k, r in decision.roles.items() if r.role == ROLE_PRIMARY)


def test_the_setting():
    assert DEFAULTS[OPT_SOC_BALANCE] == 15
    found = {key: d for d, key in CLUSTER_NUMBERS}
    d = found[OPT_SOC_BALANCE]
    assert (d.native_min_value, d.native_max_value, d.native_step, d.native_unit_of_measurement) == (0, 50, 5, "%")
    keys = [key for _, key in CLUSTER_NUMBERS]
    assert keys.index(OPT_SOC_BALANCE) > keys.index(OPT_CONCENTRATE_BELOW)
    assert OPT_SOC_BALANCE in VIRTUAL_RESET_KEYS


def test_discharge_starts_on_the_fullest():
    readings = [reading("a", 13), reading("b", 5), reading("c", 39)]
    assert primary(Distributor().decide(readings, ON, grid=300.0, now=0.0)) == "c"


def test_charge_starts_on_the_emptiest():
    readings = [reading("a", 60), reading("b", 20), reading("c", 90)]
    assert primary(Distributor().decide(readings, ON, grid=-300.0, now=0.0)) == "b"


def test_priority_goes_first():
    readings = [reading("a", 20, priority=6), reading("b", 90)]
    assert primary(Distributor().decide(readings, ON, grid=300.0, now=0.0)) == "a"


def test_the_primary_stays_within_the_balance():
    d = Distributor()
    d.primary = "a"
    readings = [reading("a", 40), reading("b", 50)]
    assert primary(d.decide(readings, ON, grid=300.0, now=0.0)) == "a"


def test_a_new_direction_changes_the_primary_beyond_the_balance():
    d = Distributor()
    d.primary = "a"
    readings = [reading("a", 25), reading("b", 60)]
    decision = d.decide(readings, ON, grid=300.0, now=0.0)
    assert d.primary == "b"
    assert decision.reason == "b takes over as primary: 60 % against 25 %"
    assert decision.attributes["primary_changes_today"] == 1
    assert decision.attributes["soc_spread"] == 35.0


def test_a_running_primary_is_not_replaced_for_its_soc():
    d = Distributor()
    d.primary = "a"
    readings = [reading("a", 20, out=300), reading("b", 90)]
    assert primary(d.decide(readings, ON, grid=50.0, now=0.0)) == "a"


def test_an_empty_primary_is_still_replaced():
    d = Distributor()
    d.primary = "a"
    empty = replace(reading("a", 5, out=300), empty=True)
    assert primary(d.decide([empty, reading("b", 50)], ON, grid=300.0, now=0.0)) == "b"


def test_off_is_the_choice_of_before():
    readings = [reading("a", 13), reading("b", 5), reading("c", 39)]
    assert primary(Distributor().decide(readings, OFF, grid=300.0, now=0.0)) == "a"


def test_split_by_weight_keeps_the_total_and_the_rooms():
    rooms = {"a": 800, "b": 800}
    out = split(600, rooms, weights={"a": 800.0, "b": 400.0})
    assert out == {"a": 400, "b": 200}
    assert sum(out.values()) == 600
    capped = split(1200, rooms, weights={"a": 800.0, "b": 100.0})
    assert capped == {"a": 800, "b": 400}
    assert split(600, rooms, weights={"a": 0.0, "b": 0.0}) == split(600, rooms)


def test_secondaries_share_by_energy():
    d = Distributor()
    d.primary = "p"
    readings = [reading("p", 90, out=800, priority=6), reading("a", 80), reading("b", 30)]
    decision = d.decide(readings, ON, grid=900.0, now=0.0)
    limits = {k: r.limit for k, r in decision.roles.items() if k != "p"}
    assert limits["a"] > limits["b"] > 0
    off = Distributor()
    off.primary = "p"
    plain = off.decide(readings, OFF, grid=900.0, now=0.0)
    assert sum(r.limit for k, r in plain.roles.items() if k != "p") == sum(limits.values())
    assert plain.roles["a"].limit == plain.roles["b"].limit


def test_energy_counts_the_floor_and_the_capacity():
    small = reading("s", 50, capacity=2.0)
    large = reading("l", 40, capacity=8.0)
    assert Distributor._energy(large, DISCHARGE) > Distributor._energy(small, DISCHARGE)
    assert Distributor._energy(reading("x", 5), DISCHARGE) == 0
    assert Distributor._energy(reading("x", 100), CHARGE) == 0
