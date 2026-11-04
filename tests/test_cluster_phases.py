"""Several batteries on one phase, up to four in a cluster (v1.7.2).

The phase is a label: the distribution works on the total of the connection
and never looks at it. Until v1.7.1 the config flow took one battery per
phase, so three at most; now several may share a phase and MAX_MEMBERS caps
the group.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.zendure_restapi import config_flow as CF
from custom_components.zendure_restapi.cluster.contract import (
    CHARGE, DISCHARGE, NONE, ROLE_PRIMARY, ROLE_SECONDARY, SMART_MATCHING, GroupSettings, Reading,
)
from custom_components.zendure_restapi.cluster.group import Distributor, split
from custom_components.zendure_restapi.const import (
    CONF_BATTERY_ENTRY, CONF_PHASE, MAX_MEMBERS, SUBENTRY_BATTERY,
)

SETTINGS = GroupSettings(
    mode=SMART_MATCHING, charge_buffer=0, discharge_buffer=0, start_charge_at=50,
    start_discharge_at=50, concentrate_below=300, redistribute_s=30,
)


def reading(key: str, *, priority: int = 5, power: float = 0.0, discharge_limit: int = 0, phase: int = 1) -> Reading:
    return Reading(
        key=key, name=key.upper(), phase=phase, priority=priority, available=True, power=power,
        charge_limit=0, discharge_limit=discharge_limit, charge_cap=800, discharge_cap=800,
        full=False, empty=False, soc=50.0, capacity_kwh=2.0, response_s=5.0,
    )


# ── Distribution ─────────────────────────────────────────────────────────

def test_max_members_is_four():
    assert MAX_MEMBERS == 4


def test_split_over_four():
    rooms = {"a": 800, "b": 800, "c": 800, "d": 400}
    # In proportion to room, each share rounded to 50 W: 2000 W gives 550/550/550/300.
    shares = split(2000, rooms)
    assert abs(sum(shares.values()) - 2000) <= 25 * len(rooms)
    assert split(2100, rooms) == {"a": 600, "b": 600, "c": 600, "d": 300}
    assert shares["d"] < shares["a"] == shares["b"] == shares["c"]
    assert all(shares[k] <= room for k, room in rooms.items())


def test_four_on_one_phase_one_primary_three_secondaries():
    d = Distributor()
    # The primary runs at its cap; the house asks 1500 W more.
    readings = [reading("a", priority=6, power=800, discharge_limit=800)] + [reading(k) for k in "bcd"]
    decision = d.decide(readings, SETTINGS, grid=1500.0, now=0.0)
    roles = decision.roles
    assert decision.primary == "a" and roles["a"].role == ROLE_PRIMARY
    secondaries = [roles[k] for k in "bcd"]
    assert all(r.role == ROLE_SECONDARY and r.direction == DISCHARGE for r in secondaries)
    # Demand 2300 W, the primary keeps 60 % of its cap (480 W), the rest is split.
    assert sum(r.limit for r in secondaries) == 1800
    assert decision.status == "regulating"


def test_phase_does_not_change_the_decision():
    def run(phases):
        d = Distributor()
        readings = [reading("a", priority=6, power=800, discharge_limit=800, phase=phases[0])] + [
            reading(k, phase=p) for k, p in zip("bcd", phases[1:])
        ]
        return d.decide(readings, SETTINGS, grid=1500.0, now=0.0)

    same = run([1, 1, 1, 1])
    spread = run([1, 2, 3, 1])
    assert same.roles == spread.roles and same.primary == spread.primary


def test_four_hand_back_on_a_load_drop():
    d = Distributor()
    readings = [reading("a", priority=6, power=800, discharge_limit=800)] + [reading(k) for k in "bcd"]
    d.decide(readings, SETTINGS, grid=1500.0, now=0.0)
    running = {k: d.roles[k].limit for k in "bcd"}
    # The load drops: the primary has gone to nothing, the grid exports.
    after = [reading("a", priority=6, power=0, discharge_limit=0)] + [
        reading(k, power=running[k], discharge_limit=running[k]) for k in "bcd"
    ]
    decision = d.decide(after, SETTINGS, grid=-900.0, now=20.0)
    assert sum(decision.roles[k].limit for k in "bcd") < sum(running.values())


# ── Config flow ──────────────────────────────────────────────────────────

def battery(entry_id: str, title: str) -> SimpleNamespace:
    return SimpleNamespace(entry_id=entry_id, title=title, data={"sn": f"SN00{entry_id}"}, options={})


def cluster_flow(batteries: list) -> tuple[CF.ZendureConfigFlow, dict]:
    flow = CF.ZendureConfigFlow()
    seen: dict = {}

    async def set_unique_id(*_a, **_k):
        return None

    flow.async_set_unique_id = set_unique_id
    flow._abort_if_unique_id_configured = lambda *a, **k: None
    flow._batteries = lambda: batteries
    flow.async_create_entry = lambda **kw: seen.setdefault("entry", kw)
    flow.async_show_form = lambda **kw: seen.setdefault("form", kw)
    return flow, seen


def test_cluster_takes_two_batteries_on_one_phase():
    a, b = battery("1", "Mix"), battery("2", "Mix")
    flow, seen = cluster_flow([a, b])
    labels = [CF.battery_label(a), CF.battery_label(b)]
    asyncio.run(flow.async_step_battery_cluster({labels[0]: "l1", labels[1]: "l1"}))
    assert "entry" in seen, seen.get("form", {}).get("errors")
    phases = [sub["data"][CF.CONF_PHASE] for sub in seen["entry"]["subentries"]]
    assert phases == ["l1", "l1"]


def test_cluster_takes_four_and_refuses_five():
    four = [battery(str(i), "Mix") for i in range(1, 5)]
    flow, seen = cluster_flow(four)
    asyncio.run(flow.async_step_battery_cluster({CF.battery_label(e): "l2" for e in four}))
    assert len(seen["entry"]["subentries"]) == 4

    five = [battery(str(i), "Mix") for i in range(1, 6)]
    flow, seen = cluster_flow(five)
    asyncio.run(flow.async_step_battery_cluster({CF.battery_label(e): "l1" for e in five}))
    assert "entry" not in seen
    assert seen["form"]["errors"] == {"base": "too_many_batteries"}
    assert seen["form"]["description_placeholders"] == {"max": "4"}


def subentry_flow(members: list[tuple[str, str]], batteries: list) -> tuple[CF.BatterySubentryFlow, dict]:
    flow = CF.BatterySubentryFlow()
    subentries = {
        f"s{i}": SimpleNamespace(subentry_type=SUBENTRY_BATTERY, data={CONF_BATTERY_ENTRY: e, CONF_PHASE: p})
        for i, (e, p) in enumerate(members)
    }
    flow._get_entry = lambda: SimpleNamespace(subentries=subentries)
    flow.hass = MagicMock()
    flow.hass.config_entries.async_entries.return_value = batteries
    seen: dict = {}
    flow.async_abort = lambda **kw: seen.setdefault("abort", kw)
    flow.async_show_form = lambda **kw: seen.setdefault("form", kw)
    return flow, seen


def test_add_battery_offers_every_phase():
    batteries = [battery(str(i), "Mix") for i in range(1, 5)]
    flow, seen = subentry_flow([("1", "l1"), ("2", "l2"), ("3", "l3")], batteries)
    asyncio.run(flow.async_step_user())
    assert "abort" not in seen
    schema = seen["form"]["data_schema"].schema
    phase = next(v for k, v in schema.items() if str(k) == CF.CONF_PHASE)
    assert list(phase.config["options"]) == ["l1", "l2", "l3"]


def test_add_battery_stops_at_four():
    batteries = [battery(str(i), "Mix") for i in range(1, 6)]
    flow, seen = subentry_flow([(str(i), "l1") for i in range(1, 5)], batteries)
    asyncio.run(flow.async_step_user())
    assert seen["abort"] == {"reason": "cluster_full", "description_placeholders": {"max": "4"}}


def test_reading_direction_helpers_unchanged():
    r = reading("a", power=-300)
    assert r.flow(CHARGE) == 300 and r.direction == NONE
