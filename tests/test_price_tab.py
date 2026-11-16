"""The Price Control tab shows what the chosen Decision uses (v1.8.2).

One Price Control section, four columns wide, on one row: the switch, Status,
Decision, Net metering, Plan and Expected margin; the Day plan section and its
badges are gone. Plan and
Expected margin, and the plan text, only with the day plan; the two prices and the heading Mode per band only with
Price bands; the fixed tariff section only with the fixed tariff, its
numbers as sliders; the formula settings and Electricity price not with the
fixed tariff; Minimum margin and Expected house load only with the day plan.
"""

import json
from pathlib import Path

from custom_components.zendure_restapi import cluster_dashboard as C

TEMPLATE = (Path(__file__).parent.parent / "custom_components" / "zendure_restapi" / "templates"
            / "dashboard_zendure_restapi.json")
DEC = "select.price_control_decision"


def price_tab() -> dict:
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    return next(v for v in template["views"] if v.get("path") == "price-control")


def section(view: dict, heading: str) -> dict:
    return next(s for s in view["sections"] if any(c.get("heading") == heading for c in s["cards"]))


def card(sec: dict, entity: str) -> dict:
    return next(c for c in sec["cards"] if c.get("entity") == entity)


def test_the_day_plan_cards_join_the_price_control_section_on_one_row():
    view = price_tab()
    assert not any(c.get("heading") == "Day plan" for s in view["sections"] for c in s["cards"])
    block = section(view, "Price Control")
    assert block["column_span"] == 4
    assert [(c.get("entity"), c.get("grid_options", {}).get("columns", 6)) for c in block["cards"][1:]] == [
        ("switch.price_control", 6), ("sensor.price_control_status", 6), ("select.price_control_decision", 12),
        ("switch.price_control_net_metering", 12), ("sensor.price_control_plan", 6),
        ("sensor.price_control_expected_margin", 6)]
    assert "visibility" not in block and "visibility" not in block["cards"][1]
    only_plan = [{"condition": "state", "entity": DEC, "state": "day_plan"}]
    for entity in ("sensor.price_control_plan", "sensor.price_control_expected_margin"):
        assert card(block, entity)["visibility"] == only_plan
    # No badges of the day plan left: the heading keeps the status and the mode.
    assert [b["entity"] for b in block["cards"][0]["badges"]] == [
        "sensor.price_control_status", "select.zendure_solarflow_3000_mix_ac_operation_mode"]


def test_each_decision_shows_its_own_cards():
    view = price_tab()
    modes = section(view, "Pricing modes")
    for entity in ("number.price_control_cheap_price", "number.price_control_expensive_price"):
        assert card(modes, entity)["visibility"] == [{"condition": "state", "entity": DEC, "state": "price_bands"}]
    heading = next(c for c in modes["cards"] if c.get("heading") == "Mode per band")
    assert heading["visibility"] == [{"condition": "state", "entity": DEC, "state": "price_bands"}]
    tariff = section(view, "Fixed tariff")
    assert tariff["visibility"] == [{"condition": "state", "entity": DEC, "state": "fixed_tariff"}]
    for entity in ("number.price_control_fixed_import_price", "number.price_control_fixed_export_price",
                   "number.price_control_sunrise_offset", "number.price_control_sunset_offset"):
        assert card(tariff, entity)["features"] == [{"type": "numeric-input", "style": "slider"}]
    # Under net metering export is the import price: no export price to set.
    assert card(tariff, "number.price_control_fixed_export_price")["visibility"] == [
        {"condition": "state", "entity": "switch.price_control_net_metering", "state": "off"}]
    heading = next(c for c in tariff["cards"] if c.get("heading") == "Fixed tariff")
    assert [b["state_content"] for b in heading["badges"]] == ["day_from", "day_until"]
    formulas = section(view, "Tariff")
    for entity in ("number.price_control_import_factor", "number.price_control_import_surcharge",
                   "number.price_control_export_factor", "number.price_control_export_surcharge"):
        assert {"condition": "state", "entity": DEC, "state_not": "fixed_tariff"} in card(formulas, entity)["visibility"]
    losses = section(view, "Losses and margin")
    assert card(losses, "sensor.price_control_electricity_price")["visibility"] == [
        {"condition": "state", "entity": DEC, "state_not": "fixed_tariff"}]
    for entity in ("number.price_control_minimum_margin", "number.price_control_expected_house_load"):
        assert card(losses, entity)["visibility"] == [{"condition": "state", "entity": DEC, "state": "day_plan"}]


def test_the_plan_text_only_with_the_day_plan_and_without_the_old_name():
    view = price_tab()
    text = next(c for s in view["sections"] for c in s["cards"]
                if c.get("type") == "markdown" and "sensor.price_control_plan" in c.get("content", ""))
    assert text["visibility"] == [{"condition": "state", "entity": DEC, "state": "day_plan"}]
    assert "Fixed prices" not in text["content"] and "No day plan yet" in text["content"]


def test_the_cluster_tab_follows_with_its_own_entities():
    template = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    price = "price_control_virtual_cluster_"
    known = {f"{d}.{price}{k}" for d, k in (
        ("select", "decision"), ("sensor", "status"), ("sensor", "plan"), ("switch", "net_metering"),
        ("number", "fixed_import_price"), ("number", "cheap_price"), ("sensor", "import_price"),
        ("number", "minimum_margin"))} | {"switch.price_control_virtual_cluster"}
    group = {"mode": "select.virtual_cluster_operation_mode", "power": "sensor.virtual_cluster_battery_power"}
    view = C.price_view(template, price, group, known, None, "sensor.virtual_p1_meter_grid_power", 3, True)
    text = json.dumps(view)
    assert "select.price_control_decision" not in text
    assert f'"entity": "select.{price}decision", "state": "fixed_tariff"' in text
    assert view["sections"][0]["cards"][4]["entity"] == f"switch.{price}net_metering"
