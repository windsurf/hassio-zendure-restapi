"""Diagnostics support for the Zendure RestAPI integration.

The download contains the device report alongside the list of keys this version
has no entity for, and the measured diagnostics with what they rest on. That list is the fastest route from "my model is not fully
supported" to a precise issue report.

Diagnostics files get attached to public issues, so everything that identifies a
device or its owner is stripped first. Two things make that harder than a set of
key names. The report is flattened before it reaches the entities, which turns
``packData[0].sn`` into ``pack1.sn`` and puts it out of reach of an exact-match
redaction. And the MQTT status is an RPC response whose contents are not
documented, so it is summarised rather than copied.
"""

from __future__ import annotations

import re
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_EXTERNAL_HOST, CONF_HOST, DOMAIN, INTEGRATION_VERSION
from .coordinator import ZendureCoordinator
from .p1meter import ExternalMeter
from .registry import pack_model

REDACT = {
    "sn", "deviceId", "deviceKey", "productKey", "password", "username",
    CONF_HOST, CONF_EXTERNAL_HOST,
}

# Matches the flattened per-pack form of anything in REDACT: pack1.sn, pack2.sn
# and so on. Without this the battery serial travels in flat_data untouched.
_FLAT_SENSITIVE = re.compile(
    r"^\w+\d+\.(" + "|".join(re.escape(k) for k in sorted(REDACT)) + r")$"
)


def _redact_flat(data: dict[str, Any]) -> dict[str, Any]:
    """Redact both plain and flattened forms of the sensitive keys."""
    cleaned = async_redact_data(dict(data), REDACT)
    return {
        key: ("**REDACTED**" if _FLAT_SENSITIVE.match(key) else value)
        for key, value in cleaned.items()
    }


def _summarise_mqtt(status: Any) -> Any:
    """Report whether MQTT answered, not what it said.

    The RPC response is undocumented and may carry a broker address, a client
    identifier or a device key. Reporting its shape is enough to tell whether
    the endpoint works, and cannot leak what has not been read.
    """
    if isinstance(status, str):
        return status
    if isinstance(status, dict):
        return {"responded": True, "keys": sorted(status)}
    return {"responded": status is not None}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    stored = hass.data[DOMAIN][entry.entry_id]
    if isinstance(stored, ExternalMeter):
        return {
            "integration_version": INTEGRATION_VERSION,
            "entry": {
                "data": async_redact_data(dict(entry.data), REDACT),
                "options": dict(entry.options),
            },
            "external_meter": stored.diagnostics(),
        }
    coordinator: ZendureCoordinator = stored

    mqtt_status: Any
    try:
        mqtt_status = await coordinator.api.async_get_mqtt_status()
    except Exception as err:  # noqa: BLE001 - diagnostics must never fail hard
        mqtt_status = f"unavailable: {err}"

    return {
        "integration_version": INTEGRATION_VERSION,
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT),
            "options": async_redact_data(dict(entry.options), REDACT),
        },
        "device_type": coordinator.device_type,
        "product": coordinator.product,
        "firmware": coordinator.firmware,
        "pack_count": coordinator.pack_count,
        "packs": _packs(coordinator.data or {}, coordinator.pack_count),
        "unknown_keys": sorted(coordinator.unknown_keys),
        "flat_data": _redact_flat(coordinator.data or {}),
        "raw_report": async_redact_data(dict(coordinator.raw or {}), REDACT),
        "mqtt_status": _summarise_mqtt(mqtt_status),
        "external_meter": _external(hass, entry),
        "measured": _measured(hass, entry),
    }


def _packs(data: dict[str, Any], count: int) -> list[dict[str, Any]]:
    """Per pack what the model is read from, without the serial (v1.6.4).

    The pack model follows from the first and fourth character of the serial
    and from packType. Those two characters name a product line, not a unit,
    so they go in the report while the serial itself stays redacted.
    """
    packs = []
    for index in range(1, count + 1):
        serial = data.get(f"pack{index}.sn")
        pack_type = data.get(f"pack{index}.packType")
        chars = (
            f"{serial[0]}..{serial[3]}"
            if isinstance(serial, str) and len(serial) >= 4 else None
        )
        model, kwh = pack_model(serial, pack_type)
        packs.append({
            "pack": index,
            "serial_chars_1_and_4": chars,
            "packType": pack_type,
            "model": model,
            "nominal_kwh": kwh,
        })
    return packs


def _measured(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    """Measured diagnostics with their basis: the last blocks, cycles and
    capacities (v1.6.3). Energy and timings only, nothing that identifies."""
    runtime = hass.data.get(DOMAIN, {}).get(f"{entry.entry_id}_runtime")
    return runtime["controller"].measure.diagnostics() if runtime else None


def _external(hass: HomeAssistant, entry: ConfigEntry) -> Any:
    """Counters of the external meter linked to this battery, if any."""
    runtime = hass.data.get(DOMAIN, {}).get(f"{entry.entry_id}_runtime")
    meter = runtime["controller"].external if runtime else None
    return meter.diagnostics() if meter is not None else None
