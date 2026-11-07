"""Peaks in a cluster (v1.7.4): the secondaries are added only once the demand
has stayed above Concentrate below for the cluster's Start after, unless the
primary is at its cap.

Seen on a Virtual cluster: a boiling-water tap of 2.2 kW for 10 s took two
secondaries for 10 to 30 s, five times in one night, while the primary alone
could have delivered up to 3000 W.
"""

from dataclasses import replace

from custom_components.zendure_restapi.cluster.contract import (
    DISCHARGE, ROLE_SECONDARY, SMART_MATCHING, GroupSettings, Reading,
)
from custom_components.zendure_restapi.cluster.group import Distributor

SETTINGS = GroupSettings(
    mode=SMART_MATCHING, charge_buffer=0, discharge_buffer=0, start_charge_at=5,
    start_discharge_at=5, concentrate_below=300, redistribute_s=30, start_after=30,
)


def reading(key: str, cap: int, *, priority: int = 5, limit: int = 0) -> Reading:
    return Reading(
        key=key, name=key, phase=1, priority=priority, available=True, power=float(limit),
        charge_limit=0, discharge_limit=limit, charge_cap=cap, discharge_cap=cap,
        full=False, empty=False, soc=50.0, capacity_kwh=8.0, response_s=2.0,
    )


def group(primary_limit: int, primary_cap: int = 3000) -> list[Reading]:
    return [reading("p", primary_cap, priority=6, limit=primary_limit),
            reading("a", 3000), reading("b", 3000)]


def on(decision) -> int:
    roles = [decision.roles[k] for k in "ab"]
    assert all(r.role == ROLE_SECONDARY for r in roles)
    return sum(r.limit for r in roles if r.direction == DISCHARGE)


def test_a_peak_shorter_than_start_after_takes_no_secondary():
    d = Distributor()
    # base load 150 W on the primary, then 2.25 kW more for about 10 s
    first = d.decide(group(150), SETTINGS, grid=2250.0, now=0.0)
    assert on(first) == 0 and first.status == "concentrated"
    assert "secondaries wait" in first.reason
    assert on(d.decide(group(150), SETTINGS, grid=2250.0, now=10.0)) == 0
    assert on(d.decide(group(150), SETTINGS, grid=0.0, now=20.0)) == 0
    assert d.above_since is None
    assert on(d.decide(group(150), SETTINGS, grid=0.0, now=60.0)) == 0


def test_demand_that_stays_above_brings_the_secondaries_after_start_after():
    d = Distributor()
    for t in (0.0, 10.0, 20.0):
        assert on(d.decide(group(150), SETTINGS, grid=2250.0, now=t)) == 0
    # the hold did not use up the split: at 30 s they come at once
    decision = d.decide(group(150), SETTINGS, grid=2250.0, now=30.0)
    assert on(decision) > 0 and decision.status == "regulating"


def test_a_primary_at_its_cap_gets_the_secondaries_at_once():
    d = Distributor()
    decision = d.decide(group(800, primary_cap=800), SETTINGS, grid=1600.0, now=0.0)
    assert on(decision) > 0


def test_start_after_zero_adds_them_at_the_first_split_as_before():
    d = Distributor()
    decision = d.decide(group(150), replace(SETTINGS, start_after=0), grid=2250.0, now=0.0)
    assert on(decision) > 0
