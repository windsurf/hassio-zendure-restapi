"""Max share per copy of a Virtual cluster, and the Distribution table (v1.7.2).

A copy gets a Max share like a battery under a real cluster: the most it gives
or takes in the group, 0 = its own maximum. It counts at once. With a low Max
share on the primary the other copies take part at a low load too. The
Distribution table shows the primary's own direction and every battery's power.
"""

import json
from types import SimpleNamespace

from custom_components.zendure_restapi import cluster_dashboard as CD
from custom_components.zendure_restapi.cluster.contract import (
    DISCHARGE, ROLE_SECONDARY, SMART_MATCHING, GroupSettings, Reading,
)
from custom_components.zendure_restapi.cluster.group import Distributor
from custom_components.zendure_restapi.const import MAX_SHARE_LIMIT, OPT_MAX_SHARE_CLONES, VIRTUAL_DEFAULTS
from custom_components.zendure_restapi.virtual.cluster import VirtualCluster


def cluster(values: dict, clones: list | None = None) -> VirtualCluster:
    vc = VirtualCluster.__new__(VirtualCluster)
    vc.settings = SimpleNamespace(get=lambda key: values.get(key))
    vc.clones = clones or []
    return vc


def clone(index: int, joined: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        index=index,
        member=SimpleNamespace(max_share=0),
        view=SimpleNamespace(group=object() if joined else None, max_share=0),
    )


def test_defaults_are_own_maximum():
    assert all(VIRTUAL_DEFAULTS[key] == 0 for key in OPT_MAX_SHARE_CLONES)


def test_max_share_is_read_and_bounded():
    k1, k2, k3 = OPT_MAX_SHARE_CLONES
    vc = cluster({k1: 300, k2: -50, k3: 99999})
    assert [vc.max_share(i) for i in (1, 2, 3)] == [300, 0, MAX_SHARE_LIMIT]
    assert cluster({}).max_share(1) == 0
    assert cluster({k1: "x"}).max_share(1) == 0
    assert vc.max_share(4) == 0


def test_a_change_counts_at_once():
    first, second = clone(1), clone(2, joined=False)
    vc = cluster({OPT_MAX_SHARE_CLONES[0]: 300, OPT_MAX_SHARE_CLONES[1]: 500}, [first, second])
    vc.apply_max_share(1)
    vc.apply_max_share(2)
    assert first.member.max_share == 300 and first.view.max_share == 300
    # Not in the group (yet): the member keeps it for when it joins.
    assert second.member.max_share == 500 and second.view.max_share == 0


def reading(key: str, cap: int, *, priority: int = 5, power: float = 0.0, limit: int = 0) -> Reading:
    return Reading(
        key=key, name=key, phase=1, priority=priority, available=True, power=power,
        charge_limit=0, discharge_limit=limit, charge_cap=cap, discharge_cap=cap,
        full=False, empty=False, soc=50.0, capacity_kwh=8.0, response_s=2.0,
    )


def test_low_max_share_on_the_primary_brings_the_others_in_at_a_low_load():
    settings = GroupSettings(SMART_MATCHING, 0, 0, 50, 50, concentrate_below=0, redistribute_s=30)
    # Primary held to 300 W and at it; the house asks 400 W more.
    readings = [reading("p", 300, priority=6, power=300, limit=300), reading("a", 3000), reading("b", 3000)]
    decision = Distributor().decide(readings, settings, grid=400.0, now=0.0)
    roles = [decision.roles[k] for k in ("a", "b")]
    assert all(r.role == ROLE_SECONDARY and r.direction == DISCHARGE and r.limit > 0 for r in roles)
    # Demand 700 W, the primary keeps 60 % of its 300 W; the rest is split.
    assert sum(r.limit for r in roles) == 500


def test_without_max_share_the_primary_keeps_it_alone():
    settings = GroupSettings(SMART_MATCHING, 0, 0, 50, 50, concentrate_below=0, redistribute_s=30)
    readings = [reading("p", 3000, priority=6, power=300, limit=300), reading("a", 3000), reading("b", 3000)]
    decision = Distributor().decide(readings, settings, grid=400.0, now=0.0)
    assert all(decision.roles[k].limit == 0 for k in ("a", "b"))


def test_distribution_table_shows_the_primary_and_the_power():
    powers = {"Virtual copy 1": "sensor.virtual_copy_1_battery_power"}
    table = CD.distribution_table("sensor.virtual_cluster_status", powers)
    assert "| Battery | Role | Direction | Limit | Power |" in table
    assert "own loop" in table and json.dumps(powers) in table
    assert "state_attr('sensor.virtual_cluster_status', 'batteries')" in table


def test_copy_tab_offers_its_max_share():
    group = {f"max_share_clone_{n}": f"number.virtual_copy_{n}_max_share" for n in (1, 2, 3)}
    for n in (1, 2, 3):
        view = CD.clone_view(f"virtual_copy_{n}_", n, f"Virtual copy {n}", group, set())
        settings = next(s for s in view["sections"] if s["cards"][0].get("heading") == "Copy settings")
        assert settings["cards"][1]["entity"] == f"number.virtual_copy_{n}_max_share"
        assert settings["cards"][1]["features"] == [{"type": "numeric-input", "style": "slider"}]
