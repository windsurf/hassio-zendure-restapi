"""A limit left behind in standby (v1.8.7): a repair that stops the battery once.

Standby writes nothing after its one stop. A limit another automation, the app
or a manager on the device leaves behind keeps the battery running; after five
minutes a repair says so and offers to stop it once. The integration's own
limits and a restart holding a limit (its own repair) do not raise it.
"""

import asyncio
from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import STALE_LIMIT_S

from .helpers import T0, make_controller, make_coordinator, sent

HELD = {"inputLimit": 0, "outputLimit": 600, "smartMode": 1, "acMode": 2}


@pytest.fixture
def issues(monkeypatch):
    created, deleted = [], []
    monkeypatch.setattr(C.ir, "async_create_issue", lambda hass, domain, issue_id, **kw: created.append((issue_id, kw)))
    monkeypatch.setattr(C.ir, "async_delete_issue", lambda hass, domain, issue_id: deleted.append(issue_id))
    return created, deleted


def _standby(monkeypatch, data, at):
    monkeypatch.setattr(C.dt_util, "utcnow", lambda: at)
    return data


def _battery():
    co = make_coordinator()
    co.config_entry = MagicMock(entry_id="entry1", title="Test battery")
    c = make_controller(C.MODE_STANDBY, co)
    c._standby_sent = True   # standby stopped the battery before; passive now
    return c


def test_a_foreign_limit_raises_the_repair_after_five_minutes(monkeypatch, issues):
    created, _ = issues
    c = _battery()
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0)))
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0 + timedelta(seconds=STALE_LIMIT_S - 10))))
    assert created == []
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0 + timedelta(seconds=STALE_LIMIT_S))))
    assert [i for i, _ in created] == ["stale_limit_entry1"]
    issue = created[0][1]
    assert issue["is_fixable"] is True and issue["data"] == {"entry_id": "entry1"}
    assert issue["translation_placeholders"]["output"] == "600"
    assert sent(c) == [], "standby still writes nothing by itself"


def test_the_repair_stops_the_battery_once(monkeypatch, issues):
    created, deleted = issues
    c = _battery()
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0)))
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0 + timedelta(seconds=STALE_LIMIT_S))))
    c.stop_once()
    assert "stale_limit_entry1" in deleted
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0 + timedelta(seconds=STALE_LIMIT_S + 10))))
    assert sent(c) == [[("inputLimit", 0), ("outputLimit", 0), ("smartMode", 0)]]


def test_gone_when_the_limit_is_zero(monkeypatch, issues):
    created, deleted = issues
    c = _battery()
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0)))
    asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0 + timedelta(seconds=STALE_LIMIT_S))))
    asyncio.run(c._apply_standby(_standby(monkeypatch, {**HELD, "outputLimit": 0}, T0 + timedelta(seconds=STALE_LIMIT_S + 10))))
    assert deleted == ["stale_limit_entry1"]


def test_not_for_our_own_limit_or_a_restart(monkeypatch, issues):
    created, _ = issues
    c = _battery()
    c._owns_limits = True
    for seconds in (0, STALE_LIMIT_S):
        asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0 + timedelta(seconds=seconds))))
    c = _battery()
    c._restart_issue_up = True
    for seconds in (0, STALE_LIMIT_S):
        asyncio.run(c._apply_standby(_standby(monkeypatch, dict(HELD), T0 + timedelta(seconds=seconds))))
    assert created == []
