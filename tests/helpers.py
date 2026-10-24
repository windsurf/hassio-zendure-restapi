"""Builders shared by the tests."""

from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi import coordinator as CO
from custom_components.zendure_restapi.measure import BatteryMeasure

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


def make_coordinator() -> CO.ZendureCoordinator:
    """A coordinator without Home Assistant: the restart and key bookkeeping only."""
    co = CO.ZendureCoordinator.__new__(CO.ZendureCoordinator)
    co.hass = MagicMock()
    co.restarts = deque(maxlen=CO.RESTART_HISTORY)
    co.restart_seq = 0
    co._failing_since = None
    co._last_good_at = None
    co._last_good = {}
    co.title = "Test battery"
    co.device_id = "test"
    co.entry_id = "entry1"
    co.unknown_keys = set()
    co._reported_unknown = set()
    co._known_keys = set()
    co.first_seen = {}
    co._seen_store = None
    co._seen_all = {}
    co._seen_loaded = False
    co._new_keys = []
    co.updated_at = None
    co.fetch_started_at = None
    co.data = {}
    co.api = MagicMock()
    co.pack_count = 0
    return co


def make_controller(mode: str, coordinator: Any | None = None) -> C.ZendureController:
    """A controller with a stand-in device API that records every write."""
    c = C.ZendureController.__new__(C.ZendureController)
    c.coordinator = coordinator or MagicMock()
    c.coordinator.api.async_write_properties = AsyncMock()
    c.coordinator.api.async_write_property = AsyncMock()
    c._last_written = {}
    c._write_source = "mode"
    c.measure = BatteryMeasure()
    c._multi_write = True
    c.meter = None
    c.external = None
    c.settings = MagicMock()
    c.settings.get.side_effect = lambda key, default=None: mode if key == C.OPT_OPERATION_MODE else default
    c._owns_limits = False
    c._standby_sent = False
    c._last_direction = "none"
    c._reverse_until = None
    c._seen_limits = {}
    c._foreign_writes = 0
    c._writer = "none"
    c._foreign_values = []
    c._foreign_times = deque()
    c.second_controller = False
    c._restart_seen = 0
    c._restart_mismatch = False
    c._smart_checked_at = None
    c._prev_smart = None
    c._prev_snapshot = {}
    c._smart_off_at = None
    c.state = MagicMock()
    c._set_state = MagicMock()  # the status entity is not under test here
    return c


def sent(c: C.ZendureController) -> list[list[tuple[str, Any]]]:
    """Every request the controller sent, in order, as (key, value) lists."""
    out = [list(call.args[0].items()) for call in c.coordinator.api.async_write_properties.await_args_list]
    out += [[call.args] for call in c.coordinator.api.async_write_property.await_args_list]
    return out
