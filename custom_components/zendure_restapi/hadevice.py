"""Grid power from a Home Assistant device: which of its sensors make the total.

Since v1.6.5 an external meter can be a device of another integration —
a Shelly, a DSMR reader, an inverter's grid meter — rather than one sensor.
The user picks the device; this module picks its power sensors and works out
how they add up to the net grid power, import positive. The idea follows the
P1 meter of Zendure's own integration (v2): names and units decide, and a few
integrations that sign the grid their own way get a profile.

Pure functions on plain data, so the rules can be tested without Home
Assistant. The config flow collects the candidates from the entity registry
and the state machine and shows the result for confirmation before anything
is stored.

**Which sensors.** Every sensor of the device in W or kW (by its state's unit,
or the registry's when the state has none yet). Apparent and reactive power
have other units and drop out by themselves.

**Phases.** A name with phase_1/2/3, phase1, _l1/2/3 or phase_a/b/c is a phase
reading. Phases are recorded beside the total, and summed into the total when
the device has no total of its own (a 3CT clamp, some Shelly models).

**Total.** In this order: a profile for the integration; else a net reading
(a name without consumed/produced words), preferring one with total, net,
grid or usage in its name; else every consumed reading minus every produced
one; else the sum of the phases; else, as a last resort, an average.

**Sign.** Import positive. Huawei's grid meter reads export as positive, so
its readings are inverted; the confirm step shows the reading of that moment
so the sign can be checked against the sun.

**Speed.** A Home Assistant sensor is only as fast as its integration. Some
readers can be polled directly, several times a second (FAST_ROUTES); the
config flow offers that instead when it recognises one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

POWER_UNITS = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}

# Entity ids follow the language the device was named in, so Dutch and German
# words count as well.
PRODUCED = ("produc", "return", "export", "inject", "feed_in", "feedin", "supplied",
            "teruglever", "productie", "einspeis", "erzeug")
CONSUMED = ("consum", "import", "absorb", "verbruik", "afname", "verbrauch", "bezug")
TOTAL_HINTS = ("total", "net", "grid", "usage")
# An average is not a momentary reading: used for the total only when the
# device has nothing else. The Zonneplan Connect's "Elektriciteitsgemiddelde"
# read -277 W where consumption minus production was -136 W (v1.6.5).
AVERAGE = ("average", "gemiddeld", "durchschnitt", "mittel", "_avg", "avg_", "mean")

# Per integration (entity registry platform): how its grid total is made.
# "terms": (name fragments, sign) summed, the first fragment found per term;
# "direction": an unsigned power with a separate sensor whose state says export.
PROFILES: dict[str, dict[str, Any]] = {
    "enphase_envoy": {"terms": [(("net_power_consumption", "net_consumption"), 1)]},
    "sma": {"terms": [("metering_power_absorbed", 1), ("metering_power_supplied", -1)]},
    "solaredge": {"direction": ("grid_power", "grid_flow_direction", "export")},
}
# Integrations whose grid meter reads export as positive.
INVERTED = {"huawei_solar"}

# Readers this integration can poll directly over HTTP, faster than through
# their Home Assistant integration: platform -> (external meter type, keys in
# the integration's config entry data that hold the address, model check).
FAST_ROUTES: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "youless": ("youless", ("host",), ()),
    "homewizard": ("homewizard_v1", ("ip_address", "host"), ("p1", "hwe-p1")),
    "shelly": ("shelly_pro_3em", ("host",), ("pro 3em", "spem-003")),
}

_PHASE = re.compile(r"(?:phase_?([123abc])(?![a-z0-9])|_l([123])(?![0-9]))")


@dataclass(frozen=True)
class Candidate:
    """One power sensor of the device."""

    entity_id: str
    unit: str | None
    name: str = ""      # the registry's original name, if any


@dataclass
class MeterPlan:
    """How the device's sensors add up to the net grid power."""

    platform: str = ""
    terms: list[tuple[str, int]] = field(default_factory=list)
    phases: list[tuple[str, int, int]] = field(default_factory=list)  # entity, sign, phase 1-3
    direction: tuple[str, str] | None = None  # entity, state that means export
    invert: bool = False
    how: str = ""   # net, pair, phases, profile, direction

    @property
    def ok(self) -> bool:
        return bool(self.terms or self.phases)

    def total_terms(self) -> list[tuple[str, int]]:
        """The terms of the total: its own, or the phases summed."""
        if self.terms:
            return list(self.terms)
        return [(entity_id, sign) for entity_id, sign, _ in self.phases]

    def as_data(self) -> dict[str, Any]:
        """What goes into the config entry."""
        data: dict[str, Any] = {
            "terms": [[e, s] for e, s in self.terms],
            "phases": [[e, s, p] for e, s, p in self.phases],
            "invert": self.invert,
            "how": self.how,
            "platform": self.platform,
        }
        if self.direction:
            data["direction"] = list(self.direction)
        return data

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> MeterPlan:
        direction = data.get("direction")
        return cls(
            platform=str(data.get("platform") or ""),
            terms=[(str(e), int(s)) for e, s in data.get("terms") or []],
            phases=[(str(e), int(s), int(p)) for e, s, p in data.get("phases") or []],
            direction=(str(direction[0]), str(direction[1])) if direction else None,
            invert=bool(data.get("invert")),
            how=str(data.get("how") or ""),
        )


def _text(c: Candidate) -> str:
    return f"{c.entity_id.split('.', 1)[-1]} {c.name}".lower().replace(" ", "_")


def phase_of(c: Candidate) -> int:
    """1, 2 or 3 for a phase reading, 0 otherwise."""
    match = _PHASE.search(_text(c))
    if not match:
        return 0
    token = match.group(1) or match.group(2)
    return {"1": 1, "2": 2, "3": 3, "a": 1, "b": 2, "c": 3}[token]


def sign_of(c: Candidate) -> int:
    """-1 for a produced (export) reading, 1 for consumed or net."""
    text = _text(c)
    return -1 if any(word in text for word in PRODUCED) else 1


def is_net(c: Candidate) -> bool:
    text = _text(c)
    return not any(word in text for word in PRODUCED + CONSUMED)


def plan(platform: str, candidates: list[Candidate]) -> MeterPlan:
    """The meter plan for a device; `ok` is False when nothing usable was found."""
    result = MeterPlan(platform=platform, invert=platform in INVERTED)
    power = [c for c in candidates if c.unit in POWER_UNITS]
    totals: list[Candidate] = []
    for c in sorted(power, key=lambda c: c.entity_id):
        phase = phase_of(c)
        if phase:
            result.phases.append((c.entity_id, sign_of(c), phase))
        else:
            totals.append(c)

    profile = PROFILES.get(platform)
    if profile and "terms" in profile:
        for fragments, sign in profile["terms"]:
            if isinstance(fragments, str):
                fragments = (fragments,)
            match = next(
                (c for fragment in fragments for c in totals if fragment in _text(c)), None
            )
            if match is not None:
                result.terms.append((match.entity_id, sign))
        if result.terms:
            result.how = "profile"
            return result
    if profile and "direction" in profile:
        power_fragment, direction_fragment, export = profile["direction"]
        magnitude = next((c for c in totals if power_fragment in _text(c)), None)
        direction = next(
            (c for c in candidates if direction_fragment in _text(c) and c.unit not in POWER_UNITS),
            None,
        )
        if magnitude and direction:
            result.terms = [(magnitude.entity_id, 1)]
            result.direction = (direction.entity_id, export)
            result.how = "direction"
            return result

    averages = [c for c in totals if any(word in _text(c) for word in AVERAGE)]
    totals = [c for c in totals if c not in averages]
    nets = [c for c in totals if is_net(c)]
    if nets:
        hinted = [c for c in nets if any(h in _text(c) for h in TOTAL_HINTS)]
        best = (hinted or nets)[0]
        result.terms = [(best.entity_id, 1)]
        result.how = "net"
        return result
    pair = [c for c in totals if not is_net(c)]
    if pair:
        result.terms = [(c.entity_id, sign_of(c)) for c in pair]
        result.how = "pair"
        return result
    if result.phases:
        result.how = "phases"
        return result
    if averages:
        result.terms = [(averages[0].entity_id, sign_of(averages[0]))]
        result.how = "average"
    return result


def fast_route(platform: str, model: str | None, entry_data: dict[str, Any] | None) -> tuple[str, str] | None:
    """(external meter type, address) when this reader can be polled directly."""
    route = FAST_ROUTES.get(platform)
    if route is None or not entry_data:
        return None
    kind, keys, models = route
    if models and not any(m in (model or "").lower() for m in models):
        return None
    host = next((str(entry_data[k]) for k in keys if entry_data.get(k)), None)
    return (kind, host) if host else None


def summary(plan_: MeterPlan, names: dict[str, str] | None = None) -> str:
    """One line per sensor used, for the confirm step."""
    names = names or {}

    def label(entity_id: str) -> str:
        return f"{names.get(entity_id) or entity_id} (`{entity_id}`)"

    lines: list[str] = []
    if plan_.how == "phases":
        lines.append("Total: the sum of the phases")
    for entity_id, sign in plan_.terms:
        lines.append(f"{'+' if sign > 0 else '−'} {label(entity_id)}")
    if plan_.direction:
        lines.append(f"sign from {label(plan_.direction[0])} (`{plan_.direction[1]}` = export)")
    for entity_id, sign, phase in plan_.phases:
        lines.append(f"phase {phase}: {'+' if sign > 0 else '−'} {label(entity_id)}")
    return "\n".join(f"- {line}" for line in lines)
