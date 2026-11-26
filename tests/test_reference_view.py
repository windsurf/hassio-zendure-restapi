"""History shows the reference next to this battery, and the profiles (v1.8.7).

A table of reference figures for the model (3000 Mix AC+ measured; – for a
model without a measurement) beside the battery's own measured diagnostics,
the efficiency per band of power, and tiles for the house load and usage
profiles. Without the measured diagnostics (no pack data) the section goes.
"""

import json

from custom_components.zendure_restapi import dashboard as D

BATTERY = D.TEMPLATE_BATTERY


def _history(known):
    template = json.loads(D.TEMPLATE.read_text())
    config = D.build_config(template, known=known, battery=BATTERY, meter=None, external=False, ceiling=3000)
    return next(v for v in config["views"] if v.get("title") == "History")


def _all():
    template = json.loads(D.TEMPLATE.read_text())
    return {
        c.get("entity") for v in template["views"] for s in v["sections"] for c in s["cards"] if c.get("entity")
    } | {f"sensor.{BATTERY}measured_charge_efficiency"}


def test_the_reference_section():
    view = _history(_all())
    section = next(s for s in view["sections"] if any(c.get("heading") == "Reference and profiles" for c in s["cards"]))
    texts = [c["content"] for c in section["cards"] if c.get("type") == "markdown"]
    assert any("SolarFlow 3000 Mix AC+" in t and "86.2 %" in t and "| Reference | This battery |" in t for t in texts)
    assert any("state_attr" in t and "'curve'" in t for t in texts)
    tiles = {c.get("entity") for c in section["cards"] if c.get("type") == "tile"}
    assert tiles == {f"sensor.{BATTERY}house_load_profile", f"sensor.{BATTERY}usage_profile"}


def test_without_measured_diagnostics_the_section_goes():
    known = {e for e in _all() if "measured" not in e and "profile" not in e}
    view = _history(known)
    assert not any(c.get("heading") == "Reference and profiles" for s in view["sections"] for c in s["cards"])
