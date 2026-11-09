"""Trace recording for a cluster (v1.7.5).

A battery's file carries the last four characters of its serial number, its
rows the role and the limit a cluster gave it. A battery cluster and a Virtual
cluster record one row per decision in trace_cluster_<stamp>_<nnn>.csv, with
the batteries as b1_…, b2_… and, on a Virtual cluster, the house load and both
meters.
"""

import asyncio
import csv
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.zendure_restapi import trace as TR
from custom_components.zendure_restapi.cluster.contract import (
    DISCHARGE, ROLE_PRIMARY, ROLE_SECONDARY, SMART_MATCHING, GroupSettings, Reading,
)
from custom_components.zendure_restapi.cluster.group import Decision, Role
from custom_components.zendure_restapi.cluster.runtime import ClusterCoordinator

from .helpers import make_controller


class Hass:
    """Just enough of Home Assistant for the recorder: a config folder and an executor."""

    def __init__(self, folder):
        self.config = SimpleNamespace(path=lambda *parts: str(folder.joinpath(*parts)))

    async def async_add_executor_job(self, fn, *args):
        return fn(*args)

    def async_create_task(self, coro):
        return asyncio.ensure_future(coro)


SETTINGS = GroupSettings(
    mode=SMART_MATCHING, charge_buffer=5, discharge_buffer=5, start_charge_at=5,
    start_discharge_at=5, concentrate_below=300, redistribute_s=30, start_after=30,
)


def reading(key, power, limit, soc):
    return Reading(
        key=key, name=key.upper(), phase=1, priority=5, available=True, power=float(power),
        charge_limit=0, discharge_limit=limit, charge_cap=3000, discharge_cap=3000,
        full=False, empty=False, soc=soc, capacity_kwh=8.0, response_s=None,
    )


def member(key, status, reason):
    return SimpleNamespace(key=key, name=key.upper(),
                           controller=SimpleNamespace(state=SimpleNamespace(status=status, reason=reason)))


def cluster(folder, virtual=None):
    c = ClusterCoordinator(Hass(folder), lambda: SETTINGS, lambda: None)
    c.trace_virtual = virtual
    c.set_members([member("a", "balancing", "grid 5 W"), member("b", "following", "limit 100 W")])
    c.readings = [reading("a", 140, 150, 60.0), reading("b", 95, 100, 55.5)]
    c.grid = 4.6
    c.decision = Decision(
        roles={"a": Role(ROLE_PRIMARY, DISCHARGE, 150), "b": Role(ROLE_SECONDARY, DISCHARGE, 100)},
        primary="a", status="regulating", reason="two batteries",
    )
    return c


def test_a_battery_file_carries_the_end_of_its_serial_number():
    c = make_controller("smart_matching")
    c.coordinator.device_id = "TEST00000000A1234"
    assert c._trace_prefix() == "trace_1234"
    c.coordinator.device_id = None
    assert c._trace_prefix() == "trace"


def test_the_battery_trace_ends_with_role_and_cluster_limit():
    assert TR.COLUMNS[-2:] == ("role", "cluster_limit_w")
    assert TR.COLUMNS[0] == "ts"


def test_cluster_columns_per_battery_and_virtual_at_the_end():
    columns = TR.cluster_columns(2, virtual=True)
    assert columns[: len(TR.CLUSTER_COLUMNS)] == TR.CLUSTER_COLUMNS
    assert "b1_role" in columns and "b2_cluster_limit_w" in columns and "b3_role" not in columns
    assert columns[-4:] == TR.VIRTUAL_COLUMNS
    assert TR.cluster_columns(9, virtual=False)[-len(TR.CLUSTER_BATTERY_COLUMNS):][0] == "b6_name"


def test_a_cluster_row_holds_the_decision_and_each_battery(tmp_path):
    row = cluster(tmp_path).trace_row()
    assert row["status"] == "regulating" and row["primary"] == "A" and row["grid_w"] == 5
    assert row["group_power_w"] == 235
    assert (row["b1_role"], row["b1_direction"], row["b1_cluster_limit_w"]) == (ROLE_PRIMARY, DISCHARGE, 150)
    assert (row["b2_role"], row["b2_output_limit"], row["b2_power_w"], row["b2_soc"]) == (ROLE_SECONDARY, 100, 95, 55.5)
    assert row["b1_status"] == "balancing" and row["b2_reason"] == "limit 100 W"
    assert row["b1_available"] == 1
    assert "house_w" not in row


def test_a_virtual_cluster_adds_house_load_and_both_meters(tmp_path):
    extra = {"house_w": 240.0, "real_grid_w": 4.0, "virtual_p1_w": 5.0, "load_factor": 1.0}
    row = cluster(tmp_path, virtual=lambda: extra).trace_row()
    assert {k: row[k] for k in extra} == extra


def test_no_decision_yet_gives_empty_roles(tmp_path):
    c = cluster(tmp_path)
    c.decision = None
    row = c.trace_row()
    assert row["status"] == "idle" and row["b1_role"] == "" and row["primary"] == ""


def test_switched_on_it_writes_a_cluster_file_with_one_row_per_decision(tmp_path):
    c = cluster(tmp_path, virtual=lambda: {"house_w": 240.0})

    async def run():
        await c.trace.async_start()
        for _ in range(3):
            await c._record()
        await c.trace.async_stop()

    asyncio.run(run())
    files = list((tmp_path / "zendure_restapi").glob("trace_cluster_*_001.csv"))
    assert len(files) == 1
    with files[0].open(encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert tuple(rows[0]) == TR.cluster_columns(2, virtual=True)
    assert len(rows) == 4
    header = rows[0]
    assert rows[1][header.index("b2_role")] == ROLE_SECONDARY
    assert rows[1][header.index("house_w")] == "240.0"


def test_off_it_writes_nothing(tmp_path):
    c = cluster(tmp_path)
    asyncio.run(c._record())
    assert not (tmp_path / "zendure_restapi").exists()


def test_a_recorder_failure_does_not_reach_the_cluster(tmp_path):
    c = cluster(tmp_path)
    c.trace = MagicMock(wanted=True, active=True)

    async def boom(_row):
        raise RuntimeError("disk")

    c.trace.async_sample = boom
    asyncio.run(c._record())  # no exception


def test_the_switch_follows_every_flush_so_rows_do_not_stay_at_zero(tmp_path):
    c = cluster(tmp_path)
    seen = []
    c.trace.add_listener(lambda: seen.append(c.trace.rows))

    async def run():
        await c.trace.async_start()
        for _ in range(TR.TRACE_FLUSH_ROWS):
            await c._record()

    asyncio.run(run())
    assert TR.TRACE_FLUSH_ROWS in seen, "the row count is shown after the first flush"


def test_a_copy_carries_its_counters_on_its_status():
    from custom_components.zendure_restapi.measure import BatteryMeasure

    m = BatteryMeasure()
    m.on_skipped_peak("2026-10-26", "discharge", 12.0, 3.5)
    summary = m.switching_summary("2026-10-26")
    assert summary["skipped_peaks"] == 1 and summary["skipped_peaks_today"] == 1
    assert summary["relay_switches"] == 0 and summary["short_runs_today"] == 0
    assert m.switching_summary("2026-10-27")["skipped_peaks_today"] == 0
