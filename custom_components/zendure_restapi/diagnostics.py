"""Diagnostics support for the Zendure RestAPI integration.

The download contains the untouched device report alongside the list of keys
this version has no entity for. That list is the fastest route from "my model
is not fully supported" to a precise issue report.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, DOMAIN, INTEGRATION_VERSION
from .coordinator import ZendureCoordinator

REDACT = {"sn", "deviceId", "deviceKey", "productKey", "password", "username", CONF_HOST}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator: ZendureCoordinator = hass.data[DOMAIN][entry.entry_id]

    mqtt_status: Any
    try:
        mqtt_status = await coordinator.api.async_get_mqtt_status()
    except Exception as err:  # noqa: BLE001 - diagnostics must never fail hard
        mqtt_status = f"unavailable: {err}"

    return {
        "integration_version": INTEGRATION_VERSION,
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT),
            "options": dict(entry.options),
        },
        "device_type": coordinator.device_type,
        "product": coordinator.product,
        "firmware": coordinator.firmware,
        "pack_count": coordinator.pack_count,
        "unknown_keys": sorted(coordinator.unknown_keys),
        "flat_data": async_redact_data(dict(coordinator.data or {}), REDACT),
        "raw_report": async_redact_data(dict(coordinator.raw or {}), REDACT),
        "mqtt_status": mqtt_status,
    }
