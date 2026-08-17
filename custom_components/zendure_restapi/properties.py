"""Value conversion and enumeration maps for Zendure properties.

Conversions are taken from the zenSDK SolarFlow Generic property document and
then cross-checked against live hardware, because the document is wrong or
incomplete in several places.

Verified against a SolarFlow 3000 Mix AC+ (firmware version 3):

    totalVol 2688 -> 26.88 V   and   batcur 228 -> 22.8 A
    26.88 V * 22.8 A = 613 W   against a reported pack power of 612 W

That agreement is what pins down both scales. The document lists totalVol in
whole volts, which would have produced 2688 V.
"""

from __future__ import annotations

from typing import Any

from .const import KELVIN_OFFSET_DECIKELVIN


def conv_identity(value: Any) -> Any:
    """Return the value unchanged."""
    return value


def conv_decikelvin(value: Any) -> float | None:
    """Convert 0.1 Kelvin storage to degrees Celsius."""
    try:
        return round((float(value) - KELVIN_OFFSET_DECIKELVIN) / 10.0, 1)
    except (TypeError, ValueError):
        return None


def conv_centi(value: Any) -> float | None:
    """Convert a 0.01 unit value to its base unit."""
    try:
        return round(float(value) / 100.0, 2)
    except (TypeError, ValueError):
        return None


def conv_deci(value: Any) -> float | None:
    """Convert a 0.1 unit value to its base unit."""
    try:
        return round(float(value) / 10.0, 1)
    except (TypeError, ValueError):
        return None


def conv_signed_deci_amp(value: Any) -> float | None:
    """Convert a 16-bit two's complement deciampere reading to amperes.

    The sign distinguishes charging from discharging at pack level. Read as an
    unsigned integer, every discharge becomes roughly 6500 A.
    """
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return None
    if raw > 0x7FFF:
        raw -= 0x10000
    return round(raw / 10.0, 1)


CONVERTERS = {
    "identity": conv_identity,
    "decikelvin": conv_decikelvin,
    "centi": conv_centi,
    "deci": conv_deci,
    "signed_deci_amp": conv_signed_deci_amp,
}


def convert(name: str, value: Any) -> Any:
    """Apply a named converter."""
    return CONVERTERS.get(name, conv_identity)(value)


# ── Enumerations ─────────────────────────────────────────────────────────

PACK_STATE_MAP = {0: "standby", 1: "charging", 2: "discharging"}
DC_STATUS_MAP = {0: "off", 1: "input", 2: "output"}
AC_STATUS_MAP = {0: "off", 1: "input", 2: "output"}
PV_STATUS_MAP = {0: "off", 1: "on"}
SOC_LIMIT_MAP = {0: "none", 1: "upper_limit", 2: "lower_limit"}
SOC_STATUS_MAP = {0: "uncalibrated", 1: "calibrated"}

AC_MODE_MAP = {1: "charge", 2: "discharge"}
# Corrected against the community zenSDK automation, which writes 2 to forbid
# PV export and 1 to allow it. The previous "auto" label for 2 was wrong and
# read as permissive on a device that was actually blocking export. Value 0 is
# not exercised by that automation and remains unconfirmed.
GRID_REVERSE_MAP = {0: "off", 1: "allowed", 2: "forbidden"}
GRID_OFF_MODE_MAP = {0: "standard", 1: "economic", 2: "closed"}
FAN_SPEED_MAP = {0: "auto", 1: "gear_1", 2: "gear_2"}

GRID_STANDARD_MAP = {
    0: "germany",
    1: "france",
    2: "austria",
    3: "switzerland",
    4: "netherlands",
    5: "spain",
    6: "belgium",
    7: "greece",
    8: "denmark",
    9: "italy",
}


def map_enum(mapping: dict[int, str], value: Any) -> str | None:
    """Translate a numeric enum to its label, or None when unknown."""
    try:
        return mapping.get(int(value))
    except (TypeError, ValueError):
        return None


def unmap_enum(mapping: dict[int, str], label: str) -> int | None:
    """Translate a label back to its numeric value."""
    for number, name in mapping.items():
        if name == label:
            return number
    return None


# ── AC coupling bit field ────────────────────────────────────────────────
# Bits 0-3 are documented. Live hardware also sets bit 15, which the document
# does not describe, so undocumented bits are surfaced by number rather than
# silently dropped.

AC_COUPLING_BITS = {
    0: "ac_coupled_input_present",
    1: "ac_input_present",
    2: "ac_coupled_overload",
    3: "excess_ac_input_power",
}


def decode_ac_coupling(value: Any) -> list[str]:
    """Decode the acCouplingState bit field into active flag names."""
    try:
        raw = int(value)
    except (TypeError, ValueError):
        return []
    flags: list[str] = []
    for bit in range(16):
        if raw & (1 << bit):
            flags.append(AC_COUPLING_BITS.get(bit, f"bit{bit}"))
    return flags


# ── Per-pack keys ────────────────────────────────────────────────────────
PACK_KEYS = (
    "sn",
    "packType",
    "socLevel",
    "state",
    "power",
    "maxTemp",
    "totalVol",
    "batcur",
    "maxVol",
    "minVol",
    "softVersion",
    "heatState",
)
