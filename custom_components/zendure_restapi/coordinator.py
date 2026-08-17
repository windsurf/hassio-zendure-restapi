"""DataUpdateCoordinator for the Zendure RestAPI integration."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ZendureApiError, ZendureLocalApi
from .const import (
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_METER,
    DOMAIN,
    METER_MARKER_KEY,
    PACK_PREFIX,
)
from .properties import PACK_KEYS

_LOGGER = logging.getLogger(__name__)

# Envelope keys that may wrap the actual property map.
_PROPERTY_CONTAINERS = ("properties", "data", "payload")

# Envelope keys that may hold the list of battery packs.
_PACK_CONTAINERS = ("packData", "packInfo", "batteryList", "packs")


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
        self.raw: dict[str, Any] = {}
        self.pack_count = 0
        self.device_id: str | None = None
        self.product: str | None = None
        self.firmware: str | None = None
        self.device_type: str = DEVICE_TYPE_BATTERY
        self.unknown_keys: set[str] = set()
        self._reported_unknown: set[str] = set()
        self._known_keys: set[str] = set()

    def register_known_keys(self, keys: set[str]) -> None:
        """Record which keys the platforms are able to represent."""
        self._known_keys |= keys

    def set_scan_interval(self, seconds: int) -> None:
        """Apply a new polling interval without reloading the entry."""
        self.update_interval = timedelta(seconds=seconds)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            payload = await self.api.async_get_report()
        except ZendureApiError as err:
            raise UpdateFailed(str(err)) from err

        self.raw = payload
        flat = self._flatten(payload)
        self._learn_identity(payload, flat)
        self._track_unknown(flat)
        return flat

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
            DEVICE_TYPE_METER if METER_MARKER_KEY in flat else DEVICE_TYPE_BATTERY
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

    # ── Write helper ─────────────────────────────────────────────────────

    async def async_write(self, key: str, value: Any) -> None:
        """Write one property and refresh so the UI reflects the result."""
        await self.api.async_write_property(key, value)
        await self.async_request_refresh()
