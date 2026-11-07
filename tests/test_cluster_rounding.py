"""v1.7.4: the secondaries never deliver more than the house takes while
the primary has nothing left to give back.

Two causes were seen with a Virtual cluster at low load: rounding each share up
to 50 W (181 W over two became 100 + 100), and STEP_MIN holding a secondary at
100 W when it should go to 50. Both only while the primary is at nothing: a
primary that runs takes up a rounding excess by itself.
"""

from custom_components.zendure_restapi.cluster.contract import (
    DISCHARGE, ROLE_SECONDARY, SMART_MATCHING, GroupSettings, Reading,
)
from custom_components.zendure_restapi.cluster.group import Distributor, split

SETTINGS = GroupSettings(
    mode=SMART_MATCHING, charge_buffer=0, discharge_buffer=0, start_charge_at=5,
    start_discharge_at=5, concentrate_below=0, redistribute_s=30,
)


def reading(key: str, cap: int, *, priority: int = 5, limit: int = 0) -> Reading:
    return Reading(
        key=key, name=key, phase=1, priority=priority, available=True, power=float(limit),
        charge_limit=0, discharge_limit=limit, charge_cap=cap, discharge_cap=cap,
        full=False, empty=False, soc=50.0, capacity_kwh=8.0, response_s=2.0,
    )


def secondaries(decision, keys="ab"):
    roles = [decision.roles[k] for k in keys]
    assert all(r.role == ROLE_SECONDARY for r in roles)
    return {k: (r.limit if r.direction == DISCHARGE else 0) for k, r in zip(keys, roles)}


def test_split_rounds_down_in_total_only_when_asked():
    rooms = {"a": 3000, "b": 3000}
    assert sum(split(181, rooms).values()) == 200
    assert sum(split(181, rooms, floor=True).values()) == 150
    assert split(2100, {"a": 800, "b": 800, "c": 800, "d": 400}, floor=True) == {
        "a": 600, "b": 600, "c": 600, "d": 300}
    assert sum(split(40, rooms, floor=True).values()) == 0


def test_primary_at_nothing_the_secondaries_come_down_to_the_load():
    # 181 W of house load: the secondaries hold 100 + 100, 19 W goes into the grid.
    readings = [reading("p", 3000, priority=6), reading("a", 3000, limit=100), reading("b", 3000, limit=100)]
    d = Distributor()
    d.roles = {"a": decision_role(100), "b": decision_role(100)}
    decision = d.decide(readings, SETTINGS, grid=-19.0, now=0.0)
    assert sum(secondaries(decision).values()) == 150


def test_a_running_primary_keeps_the_old_rounding_and_step():
    # The primary runs at 300 W and takes up the excess itself: nothing changes.
    readings = [reading("p", 300, priority=6, limit=300), reading("a", 3000, limit=100),
                reading("b", 3000, limit=100)]
    d = Distributor()
    d.roles = {"a": decision_role(100), "b": decision_role(100)}
    decision = d.decide(readings, SETTINGS, grid=-19.0, now=0.0)
    assert secondaries(decision) == {"a": 100, "b": 100}


def decision_role(limit: int):
    from custom_components.zendure_restapi.cluster.group import Role

    return Role(ROLE_SECONDARY, DISCHARGE, limit)
