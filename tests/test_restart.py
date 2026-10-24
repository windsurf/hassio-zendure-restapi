"""A device restart: recognised, recorded and shown, never answered with a write.

Modelled on a firmware upgrade of a SolarFlow 3000 Mix AC+: 275 s without a
report, then messageId starting again, smartMode 0, and the limits the device
kept in flash (acMode 1, inputLimit 3000) while the integration was in standby.
messageId counts in 8 bits, so its turn from 255 to 0 is not a restart.
"""

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi import coordinator as CO
from custom_components.zendure_restapi.properties import convert

from .helpers import T0, make_controller, make_coordinator, sent

BEFORE = {"messageId": 120, "acMode": 1, "inputLimit": 0, "outputLimit": 0, "smartMode": 0,
          "electricLevel": 94, "socLimit": 0, "socStatus": 0, "faultLevel": 0}
AFTER = {"messageId": 9, "acMode": 1, "inputLimit": 3000, "outputLimit": 0, "smartMode": 0,
         "electricLevel": 94, "socLimit": 2, "socStatus": 0, "faultLevel": 0}


@pytest.fixture
def issues(monkeypatch):
    created, deleted = [], []
    monkeypatch.setattr(C.ir, "async_create_issue", lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)))
    monkeypatch.setattr(C.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id))
    monkeypatch.setattr(CO.ir, "async_create_issue", lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)))
    return created, deleted


def report(co, flat, at):
    co.updated_at = at
    co._watch_restart(flat, at)
    co.data = flat


def test_messageid_starting_again_is_a_restart():
    co = make_coordinator()
    report(co, BEFORE, T0)
    co._failing_since = T0 + timedelta(seconds=10)
    report(co, AFTER, T0 + timedelta(seconds=285))
    assert co.restart_seq == 1
    record = co.restarts[0]
    assert record["cause"] == "messageId" and record["unreachable_s"] == 275
    assert record["before"]["inputLimit"] == 0 and record["after"]["inputLimit"] == 3000
    assert record["after"]["faultLevel"] == 0


def test_a_late_report_that_keeps_counting_is_not_a_restart():
    co = make_coordinator()
    report(co, BEFORE, T0)
    co._failing_since = T0 + timedelta(seconds=10)
    report(co, {**BEFORE, "messageId": 121}, T0 + timedelta(seconds=38))
    assert co.restart_seq == 0 and not co.restarts


def test_messageid_turning_over_is_not_a_restart():
    co = make_coordinator()
    report(co, {**BEFORE, "messageId": 250}, T0)
    report(co, {**BEFORE, "messageId": 3}, T0 + timedelta(seconds=60))
    assert co.restart_seq == 0 and not co.restarts


def test_a_low_drop_after_time_unreachable_is_a_restart():
    co = make_coordinator()
    report(co, {**BEFORE, "messageId": 120}, T0)
    co._failing_since = T0 + timedelta(seconds=10)
    report(co, {**AFTER, "messageId": 9}, T0 + timedelta(seconds=280))
    assert co.restart_seq == 1 and co.restarts[0]["unreachable_s"] == 270


def test_a_drop_from_high_after_time_unreachable_is_a_restart():
    co = make_coordinator()
    report(co, {**BEFORE, "messageId": 250}, T0)
    co._failing_since = T0 + timedelta(seconds=10)
    report(co, {**AFTER, "messageId": 3}, T0 + timedelta(seconds=280))
    assert co.restart_seq == 1


def test_standby_restart_writes_nothing_and_raises_one_repair(issues):
    created, deleted = issues
    co = make_coordinator()
    co.config_entry = MagicMock(entry_id="entry1", title="Test battery")
    c = make_controller(C.MODE_STANDBY, co)
    report(co, BEFORE, T0)
    c._watch_writers()
    report(co, AFTER, T0 + timedelta(seconds=285))
    c._watch_writers()
    assert c._writer == "restart" and c._foreign_writes == 0
    assert sent(c) == []
    assert [i for i, _ in created] == ["restart_limits_entry1"]
    assert created[0][1]["translation_placeholders"]["input"] == "3000"
    # still holding: no second repair, no write
    c._watch_writers()
    c._restart_check(co.data)
    assert len(created) == 1 and sent(c) == []
    # the user chooses standby again: the command goes out, the repair clears
    c.reapply_mode()
    asyncio.run(c._apply_standby(co.data))
    assert sent(c) == [[("inputLimit", 0)]]
    c._restart_check(co.data)
    assert deleted == ["restart_limits_entry1"]


def test_restart_in_an_active_mode_raises_no_repair(issues):
    created, _ = issues
    co = make_coordinator()
    co.config_entry = MagicMock(entry_id="entry1", title="Test battery")
    c = make_controller(C.MODE_SMART_MATCHING, co)
    report(co, BEFORE, T0)
    c._watch_writers()
    report(co, AFTER, T0 + timedelta(seconds=285))
    c._watch_writers()
    assert c._writer == "restart" and created == []


def test_smartmode_dropping_by_itself_is_a_restart():
    co = make_coordinator()
    c = make_controller(C.MODE_SMART_MATCHING, co)
    report(co, {**BEFORE, "smartMode": 1, "messageId": None}, T0)
    c._watch_writers()
    report(co, {**BEFORE, "smartMode": 0, "messageId": None}, T0 + timedelta(seconds=10))
    c._watch_writers()
    assert co.restart_seq == 1 and co.restarts[0]["cause"] == "smartMode"


def test_smartmode_zero_written_by_standby_is_not_a_restart():
    co = make_coordinator()
    c = make_controller(C.MODE_STANDBY, co)
    report(co, {**BEFORE, "smartMode": 1, "inputLimit": 800, "messageId": None}, T0)
    c._watch_writers()
    asyncio.run(c._apply_standby(dict(co.data)))
    report(co, {**BEFORE, "smartMode": 0, "messageId": None}, T0 + timedelta(seconds=10))
    c._smart_off_at = T0 + timedelta(seconds=5)
    c._watch_writers()
    assert co.restart_seq == 0


def test_remaining_time_placeholder_reads_as_unknown():
    assert convert("remain_minutes", 59940) is None
    assert convert("remain_minutes", 4590) == 4590


def test_first_run_records_keys_silently_then_new_keys_raise_a_repair(issues):
    created, _ = issues
    co = make_coordinator()
    co._seen_store = MagicMock()
    co._known_keys = {"electricLevel"}
    co._track_unknown({"electricLevel": 50, "acReverse": 0})
    assert "acReverse" in co.first_seen and created == []
    co._reported_unknown.clear()
    co._track_unknown({"electricLevel": 50, "acReverse": 0, "testKey": 0})
    assert [i for i, _ in created] == ["new_property_entry1"]
    assert created[0][1]["translation_placeholders"]["keys"] == "testKey"
