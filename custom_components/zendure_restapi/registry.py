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
    "solarflow1600ac": "SolarFlow 1600 AC",
    "solarflow1600acplus": "SolarFlow 1600 AC+",
    "solarflow2400ac": "SolarFlow 2400 AC",
    "solarflow2400acplus": "SolarFlow 2400 AC+",
    "solarflow2400pro": "SolarFlow 2400 Pro",
    "solarflow3000mixac": "SolarFlow 3000 Mix AC",
    "solarflow3000mixacplus": "SolarFlow 3000 Mix AC+",
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
