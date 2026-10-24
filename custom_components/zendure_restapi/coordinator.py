"""DataUpdateCoordinator for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import ZendureApiError, ZendureLocalApi
from .const import (
    DEVICE_TYPE_BATTERY,
    IDENTITY_KEYS,
    DEVICE_TYPE_METER,
    DOMAIN,
    PACK_PREFIX,
)
from .properties import PACK_KEYS
from .registry import is_meter_report

_LOGGER = logging.getLogger(__name__)

# Envelope keys that may wrap the actual property map.
_PROPERTY_CONTAINERS = ("properties", "data", "payload")

# Envelope keys that may hold the list of battery packs.
_PACK_CONTAINERS = ("packData", "packInfo", "batteryList", "packs")

# Keys whose spelling differs per model, mapped to the spelling the entities
# use (v1.6.4). The 3000 Mix AC+ reports `oldMode`, the 2400 AC `OldMode`.
# The raw report in the diagnostics keeps the device's own spelling.
_KEY_ALIASES = {"OldMode": "oldMode"}

# What a restart record keeps of the report before and after (v1.6.8): the
# keys that say what the device will do next, and the status keys that a
# firmware upgrade was seen to change.
RESTART_KEYS = (
    "acMode", "inputLimit", "outputLimit", "smartMode", "electricLevel",
    "socLimit", "socStatus", "faultLevel", "remainOutTime", "messageId",
)
RESTART_HISTORY = 5

# messageId counts in 8 bits: it turns from 255 to 0 about every 41 minutes on
# a 3000 Mix AC+, one step per ~10 s, while the device keeps running. A drop is
# that turn when the previous number was high, the new one low, the steps fit
# the time since the previous report, and no poll failed in between. Any other
# drop is a restart: a restart was seen with 275 s unreachable and messageId 9.
MESSAGE_ID_MODULO = 256
WRAP_FROM = 200
WRAP_TO = 55
WRAP_SECONDS_PER_STEP = 5
WRAP_STEP_MARGIN = 5

# First sightings of keys without an entity, per entry, kept across restarts
# of Home Assistant (v1.6.8). A key in this store has been reported once.
SEEN_STORE_KEY = f"{DOMAIN}.unknown_keys"
SEEN_STORE_VERSION = 1


class ZendureCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls one device and exposes a flat key/value map.

    Per-pack values are flattened to ``pack1.socLevel``, ``pack2.power`` and so
    on, so every platform can address a value with a single string key.

    Two payload shapes exist in the wild and both are handled:

    * Battery: ``{"sn", "version", "product", "properties": {...},
      "packData": [...]}``
    * Meter: a flat object with ``deviceId`` and ``meterType`` and no serial
      number at all, which is why identity falls back to ``deviceId``.

    Unrecognised keys are collected rather than discarded. Reporting what the
    hardware actually sends is how the property map gets corrected.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        api: ZendureLocalApi,
        scan_interval: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_coordinator",
            update_interval=timedelta(seconds=scan_interval),
        )
        self.api = api
        # When the last successful payload arrived, and when the request that
        # produced it went out. The pair is what lets a second loop reason
        # about age: "how old is this reading" needs the first, and "could this
        # reading predate a write made a moment ago" needs the second.
        self.updated_at: datetime | None = None
        self.fetch_started_at: datetime | None = None
        self.raw: dict[str, Any] = {}
        self.pack_count = 0
        self.device_id: str | None = None
        self.product: str | None = None
        self.firmware: str | None = None
        self.device_type: str = DEVICE_TYPE_BATTERY
        self.unknown_keys: set[str] = set()
        self._reported_unknown: set[str] = set()
        self._known_keys: set[str] = set()

        # Device restarts (v1.6.8). A firmware upgrade or a power cut leaves
        # the device unreachable for a few minutes and brings it back with the
        # limits it last stored in flash. Nothing here writes to the device:
        # whether an upgrade or a calibration is still running cannot be read
        # from the report. A restart is recorded, counted and shown.
        self.restarts: deque[dict[str, Any]] = deque(maxlen=RESTART_HISTORY)
        self.restart_seq = 0
        self._failing_since: datetime | None = None
        self._last_good_at: datetime | None = None
        self._last_good: dict[str, Any] = {}

        # First sightings of keys without an entity (v1.6.8).
        self.entry_id: str | None = None
        self.title: str = ""
        self.first_seen: dict[str, str] = {}
        self._seen_store: Store | None = None
        self._seen_all: dict[str, dict[str, str]] = {}
        self._seen_loaded = False
        self._new_keys: list[str] = []

    def register_known_keys(self, keys: set[str]) -> None:
        """Record which keys the platforms are able to represent."""
        self._known_keys |= keys

    async def async_load_seen(self, entry_id: str, title: str) -> None:
        """Load the first sightings of unknown keys for this entry.

        The first time an entry runs with v1.6.8 there is no record yet. The
        keys it reports then are written down without a repair: they were
        there before, and a repair for every key a model has always had would
        be noise. Every key that appears after that gets one.
        """
        self.entry_id = entry_id
        self.title = title
        self._seen_store = Store(self.hass, SEEN_STORE_VERSION, SEEN_STORE_KEY)
        stored = await self._seen_store.async_load() or {}
        self._seen_all = stored if isinstance(stored, dict) else {}
        recorded = self._seen_all.get(entry_id)
        self._seen_loaded = isinstance(recorded, dict)
        self.first_seen = dict(recorded) if self._seen_loaded else {}

    def set_scan_interval(self, seconds: int) -> None:
        """Apply a new polling interval without reloading the entry."""
        self.update_interval = timedelta(seconds=seconds)

    async def _async_update_data(self) -> dict[str, Any]:
        self.fetch_started_at = dt_util.utcnow()
        try:
            payload = await self.api.async_get_report()
        except ZendureApiError as err:
            if self._failing_since is None:
                self._failing_since = self.fetch_started_at
            raise UpdateFailed(str(err)) from err

        now = dt_util.utcnow()
        self.updated_at = now
        self.raw = payload
        flat = self._flatten(payload)
        self._learn_identity(payload, flat)
        self._track_unknown(flat)
        self._watch_restart(flat, now)
        return flat

    # ── Device restarts ──────────────────────────────────────────────────

    def _watch_restart(self, flat: dict[str, Any], now: datetime) -> None:
        """Recognise a device that started anew since the previous report.

        `messageId` counts up in 8 bits and starts again after a restart: the
        first report after a firmware upgrade of a 3000 Mix AC+ carried
        messageId 9, after 275 s without a report. A lower number than last
        time is a restart, unless it is the counter turning from 255 to 0
        (`_is_wrap`). A report that is only late keeps counting (a report 28 s
        late during the same upgrade went from one number to the next), so it
        is not one, even when a poll failed in between.
        """
        unreachable = None
        if self._failing_since is not None:
            unreachable = round((now - self._failing_since).total_seconds())
            self._failing_since = None

        previous = _as_int(self._last_good.get("messageId"))
        current = _as_int(flat.get("messageId"))
        if (
            previous is not None and current is not None and current < previous
            and not self._is_wrap(previous, current, now, unreachable)
        ):
            self.note_restart("messageId", self._last_good, flat, unreachable, now)
        self._last_good = {key: flat.get(key) for key in RESTART_KEYS}
        self._last_good_at = now

    def _is_wrap(
        self, previous: int, current: int, now: datetime, unreachable: int | None
    ) -> bool:
        """Whether a drop in messageId is the 8-bit counter turning over."""
        if unreachable is not None or self._last_good_at is None:
            return False
        if previous < WRAP_FROM or current > WRAP_TO:
            return False
        steps = (current - previous) % MESSAGE_ID_MODULO
        elapsed = (now - self._last_good_at).total_seconds()
        return steps <= elapsed / WRAP_SECONDS_PER_STEP + WRAP_STEP_MARGIN

    def note_restart(
        self,
        cause: str,
        before: dict[str, Any],
        after: dict[str, Any],
        unreachable: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Record one restart with what the device reported before and after."""
        self.restart_seq += 1
        self.restarts.appendleft({
            "at": (now or dt_util.utcnow()).isoformat(timespec="seconds"),
            "cause": cause,
            "unreachable_s": unreachable,
            "before": {key: before.get(key) for key in RESTART_KEYS},
            "after": {key: after.get(key) for key in RESTART_KEYS},
        })
        _LOGGER.warning(
            "Zendure device %s restarted (%s%s); it reports acMode %s, inputLimit %s, "
            "outputLimit %s, smartMode %s",
            self.title or self.device_id, cause,
            f", unreachable for {unreachable} s" if unreachable is not None else "",
            after.get("acMode"), after.get("inputLimit"), after.get("outputLimit"),
            after.get("smartMode"),
        )

    # ── Identity ─────────────────────────────────────────────────────────

    def _learn_identity(self, payload: dict[str, Any], flat: dict[str, Any]) -> None:
        """Pick up serial, product and firmware from the report."""
        serial = payload.get("sn") or flat.get("sn")
        if isinstance(serial, str) and serial:
            self.api.sn = serial
            self.device_id = serial
        elif not self.device_id:
            # Meters report no serial number, only a device identifier.
            handle = payload.get("deviceId") or flat.get("deviceId")
            if isinstance(handle, str) and handle:
                self.device_id = handle

        product = payload.get("product")
        if isinstance(product, str) and product:
            self.product = product

        version = payload.get("version")
        if version is not None:
            self.firmware = str(version)

        self.device_type = (
            DEVICE_TYPE_METER if is_meter_report(payload) else DEVICE_TYPE_BATTERY
        )

    # ── Payload normalisation ────────────────────────────────────────────

    def _flatten(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Normalise the report envelope into one flat map."""
        flat: dict[str, Any] = {}

        for key, value in payload.items():
            if key in _PROPERTY_CONTAINERS and isinstance(value, dict):
                continue
            if key in _PACK_CONTAINERS and isinstance(value, list):
                continue
            if isinstance(value, (dict, list)):
                continue
            flat[key] = value

        for container in _PROPERTY_CONTAINERS:
            nested = payload.get(container)
            if isinstance(nested, dict):
                for key, value in nested.items():
                    if not isinstance(value, (dict, list)):
                        flat[key] = value

        flat.update(self._flatten_packs(payload))
        for alias, key in _KEY_ALIASES.items():
            if alias in flat:
                value = flat.pop(alias)
                flat.setdefault(key, value)
        return flat

    def _flatten_packs(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Turn the pack list into ``pack<n>.<key>`` entries."""
        packs: list[Any] = []
        for container in _PACK_CONTAINERS:
            candidate = payload.get(container)
            if isinstance(candidate, list) and candidate:
                packs = candidate
                break
            nested = payload.get("properties")
            if isinstance(nested, dict):
                candidate = nested.get(container)
                if isinstance(candidate, list) and candidate:
                    packs = candidate
                    break

        flat: dict[str, Any] = {}
        index = 0
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            index += 1
            for key, value in pack.items():
                if isinstance(value, (dict, list)):
                    continue
                flat[f"{PACK_PREFIX}{index}.{key}"] = value

        if index > self.pack_count:
            # Packs can appear late in a poll cycle; never shrink the count,
            # otherwise entities would be torn down on a single sparse reply.
            self.pack_count = index
        return flat

    # ── Discovery aid ────────────────────────────────────────────────────

    def _track_unknown(self, flat: dict[str, Any]) -> None:
        """Log keys the integration has no entity for, once each."""
        if not self._known_keys:
            return

        for key in flat:
            base = key.split(".", 1)[1] if key.startswith(PACK_PREFIX) and "." in key else key
            if base in IDENTITY_KEYS:
                # Identity, not telemetry: read by the integration itself and
                # never meant to become an entity of its own.
                continue
            if key in self._known_keys or base in self._known_keys or base in PACK_KEYS:
                continue
            self.unknown_keys.add(key)
            if key not in self._reported_unknown:
                self._reported_unknown.add(key)
                _LOGGER.info(
                    "Zendure reports property '%s' (value %r) that this version has no "
                    "entity for. Please report it so it can be added.",
                    key,
                    flat[key],
                )
                self._first_sighting(key)

        if self._seen_store is not None and not self._seen_loaded:
            # The first full report of a first run is the baseline: from here
            # on, a key that was not in it is new.
            self._seen_loaded = True
            self._save_seen()

    def _first_sighting(self, key: str) -> None:
        """Write down when a key without an entity was first reported.

        A key that is new for this entry raises one repair: after a firmware
        upgrade this is how a changed report becomes visible (v1.6.8).
        """
        if self._seen_store is None or self.entry_id is None or key in self.first_seen:
            return
        self.first_seen[key] = dt_util.utcnow().isoformat(timespec="seconds")
        self._save_seen()
        if not self._seen_loaded:
            # The first run records what was already there, silently.
            return
        self._new_keys.append(key)
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            f"new_property_{self.entry_id}",
            is_fixable=False,
            is_persistent=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="new_property",
            translation_placeholders={
                "device": self.title,
                "keys": ", ".join(self._new_keys),
            },
        )

    def _save_seen(self) -> None:
        if self._seen_store is None or self.entry_id is None:
            return
        self._seen_all[self.entry_id] = dict(self.first_seen)
        self._seen_store.async_delay_save(lambda: self._seen_all, 5)

    # ── Write helper ─────────────────────────────────────────────────────

    async def async_write(self, key: str, value: Any) -> None:
        """Write one property and refresh so the UI reflects the result."""
        await self.api.async_write_property(key, value)
        await self.async_request_refresh()


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
