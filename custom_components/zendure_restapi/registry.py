"""Model naming for Zendure devices.

This registry deliberately does *not* gate entity creation. Which entities
exist is decided by the keys the device actually reports, so an unlisted or
brand-new model still works in full and is simply labelled generically until a
name is added here.

Naming has three sources, in order of reliability:

1. The ``product`` field in the report payload. Live hardware returns
   ``"solarFlow3000MixAC+"``, which is authoritative and needs no guessing.
2. The mDNS service name ``Zendure-<Model>-<Last12MAC>``.
3. Nothing at all, in which case the device type decides the fallback label.
"""

from __future__ import annotations

import re

UNKNOWN_MODEL = "SolarFlow (generic)"
UNKNOWN_METER = "Zendure Meter"

# Normalised token -> display name.
MODEL_NAMES: dict[str, str] = {
    "solarflow800": "SolarFlow 800",
    "solarflow800plus": "SolarFlow 800 Plus",
    "solarflow800pro": "SolarFlow 800 Pro",
    "solarflow800pro2": "SolarFlow 800 Pro 2",
    "solarflow1600ac": "SolarFlow 1600 AC",
    "solarflow1600acplus": "SolarFlow 1600 AC+",
    "solarflow2400ac": "SolarFlow 2400 AC",
    "solarflow2400acplus": "SolarFlow 2400 AC+",
    "solarflow2400pro": "SolarFlow 2400 Pro",
    "solarflow3000mixac": "SolarFlow 3000 Mix AC",
    "solarflow3000mixacplus": "SolarFlow 3000 Mix AC+",
    # The 4000 Mix models launched in April 2026. Zendure's own integration
    # names the AC+ `solarflow4000ac+`, without "Mix"; both spellings are kept
    # because the local `product` field has not been seen on this hardware.
    "solarflow4000acplus": "SolarFlow 4000 Mix AC+",
    "solarflow4000mixacplus": "SolarFlow 4000 Mix AC+",
    "solarflow4000pro": "SolarFlow 4000 Mix Pro",
    "solarflow4000mixpro": "SolarFlow 4000 Mix Pro",
    # Reports no meterType; recognised as a meter by its fields (issue #7).
    "smartmeter3ct": "Smart Meter 3CT",
    "hyper2000": "Hyper 2000",
    "aio2400": "AIO 2400",
    "hub1200": "Hub 1200",
    "hub2000": "Hub 2000",
    "ace1500": "Ace 1500",
}

# meterType -> display name. Type 3 is the three-phase P1 meter confirmed on
# live hardware; the other values are placeholders pending confirmation.
METER_TYPES: dict[int, str] = {
    3: "P1 Meter (3-phase)",
}

# Keys only a battery reports. A meter's report is flat and has none of them.
BATTERY_KEYS = ("product", "sn", "properties", "packData")
SMART_METER_3CT = "smartmeter3ct"


def is_meter_report(payload: dict) -> bool:
    """Whether a report comes from a Zendure meter rather than a battery.

    The P1 meter carries `meterType`, which decides first. The Smart Meter 3CT
    reports no `meterType` (issue #7): it has the P1 meter's flat fields, with
    `total_power` and none of the battery's keys.
    """
    props = payload.get("properties")
    props = props if isinstance(props, dict) else {}
    if payload.get("meterType") is not None or props.get("meterType") is not None:
        return True
    return "total_power" in payload and not any(key in payload for key in BATTERY_KEYS)

_SERVICE_RE = re.compile(r"^zendure[-_]([^-_]+)(?:[-_](.+))?$", re.IGNORECASE)


def normalise(token: str) -> str:
    """Reduce a model token to its comparable form."""
    token = token.lower()
    token = token.replace("+", "plus")
    return re.sub(r"[^a-z0-9]", "", token)


def parse_service_name(service_name: str) -> tuple[str | None, str | None]:
    """Split an mDNS service name into (model token, serial)."""
    name = service_name.split(".")[0].strip()
    match = _SERVICE_RE.match(name)
    if not match:
        return None, None
    return match.group(1), match.group(2)


def model_name(token: str | None) -> str:
    """Resolve a model token to a display name."""
    if not token:
        return UNKNOWN_MODEL
    return MODEL_NAMES.get(normalise(token), UNKNOWN_MODEL)


def meter_name(meter_type: object) -> str:
    """Resolve a meterType value to a display name."""
    try:
        return METER_TYPES.get(int(meter_type), UNKNOWN_METER)
    except (TypeError, ValueError):
        return UNKNOWN_METER


def is_known_model(token: str | None) -> bool:
    """Whether the model token appears in the registry."""
    return bool(token) and normalise(token) in MODEL_NAMES


# ── Battery packs ────────────────────────────────────────────────────────
# A pack reports a serial number and a packType, not a model name. The first
# letter of the serial identifies the family, and in two cases a second
# character or packType is needed to tell packs apart that share a prefix.
#
# The table follows Zendure's own integration (MIT licence),
# devices/device.py ZendureBattery.get_battery_type, as of
# October 2026. Verified here on one point only: a 3000 Mix AC+ reports a
# serial starting with B and packType 70 for its internal 8 kWh pack.
# Everything else is taken on Zendure's word.

PACK_TYPE_MIX_INTERNAL = 70


def pack_model(serial: object, pack_type: object = None) -> tuple[str, float | None]:
    """Model name and nominal capacity in kWh for one battery pack.

    Returns ("unknown", None) for a serial this table does not cover, so the
    entity still exists and says so rather than guessing.
    """
    if not isinstance(serial, str) or len(serial) < 4:
        return "unknown", None
    try:
        kind = int(pack_type) if pack_type is not None else None
    except (TypeError, ValueError):
        kind = None

    first, fourth = serial[0].upper(), serial[3].upper()
    if first == "A":
        return ("AIO2400", 2.4) if fourth == "3" else ("AB1000", 0.96)
    if first == "B":
        # The Mix series' internal pack shares its prefix with the AB1000S.
        if kind == PACK_TYPE_MIX_INTERNAL:
            return "I8000 (Mix internal)", 8.0
        return "AB1000S", 0.96
    if first == "C":
        suffix = "S" if fourth == "F" else "X" if fourth == "E" else ""
        return f"AB2000{suffix}", 1.92
    if first == "F":
        return "AB3000", 2.88
    if first == "G":
        return "AB3000L", 2.88
    if first == "J":
        # Internal pack of the 2400 Pro (JO2A) and 2400 AC+ (JO4A).
        return "I2400 (internal)", 2.4
    return "unknown", None
