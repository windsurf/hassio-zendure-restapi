"""The cluster dashboard (v1.7.1; layout of the battery dashboard since v1.7.2).

A Virtual cluster gets the title *Virtual Cluster*, a Cluster tab and a tab per
virtual battery, no tab for the real battery (it keeps its own dashboard), and
calls every virtual battery by its device name. A battery cluster keeps a tab
per member, headed with the member's name. Since v1.7.3 each kind has a
dashboard of its own, with the first tab named like the dashboard, and
every tab carries an icon, numbered for the batteries and copies. The
entity ids are those of v1.7.3.
"""

import json
import re

from custom_components.zendure_restapi import cluster_dashboard as CD
from custom_components.zendure_restapi import dashboard as D

CLONES = [(1, "Virtual copy 1"), (2, "Virtual copy 2"), (3, "Virtual copy 3")]
REAL_POWER = "sensor.zendure_solarflow_3000_mix_ac_battery_power"
REAL_SOC = "sensor.zendure_solarflow_3000_mix_ac_state_of_charge"


def group() -> dict:
    g = {
        "mode": "select.virtual_cluster_operation_mode",
        "status": "sensor.virtual_cluster_status",
        "power": "sensor.virtual_cluster_battery_power",
        "soc": "sensor.virtual_cluster_state_of_charge",
        "virtual_grid": "sensor.virtual_p1_meter_grid_power",
        "house_load": "sensor.virtual_p1_meter_house_load",
        "real_grid": "sensor.virtual_p1_meter_real_grid_power",
        "reset": "button.virtual_cluster_reset",
        "rebuild": "button.virtual_cluster_rebuild_dashboard",
        "trace": "switch.virtual_cluster_trace_recording",
        "soc_protection": "select.virtual_cluster_soc_protection",
    }
    # The group's own settings, as Home Assistant has them.
    for key, object_id in (("clones", "copies"), ("load_factor", "load_factor"), ("charge_buffer", "charge_buffer"),
                           ("discharge_buffer", "discharge_buffer"), ("start_charge_at", "start_charging_at_export"),
                           ("start_discharge_at", "start_discharging_at_import"),
                           ("stop_charge_below", "stop_charging_below"),
                           ("stop_discharge_below", "stop_discharging_below"), ("stop_after", "stop_after"),
                           ("start_after", "start_after"),
                           ("concentrate_below", "concentrate_below"), ("redistribute_every", "redistribute_every")):
        g[key] = f"number.virtual_cluster_{object_id}"
    # The settings of each copy, on the copy's device (v1.7.3).
    for n in (1, 2, 3):
        g[f"max_share_clone_{n}"] = f"number.virtual_copy_{n}_max_share"
        g[f"soc_offset_{n}"] = f"number.virtual_copy_{n}_soc_offset"
        g[f"response_offset_{n}"] = f"number.virtual_copy_{n}_extra_response_time"
    return g


def known() -> set:
    return {
        f"sensor.virtual_copy_{n}_{key}"
        for n in (1, 2, 3)
        for key in ("state_of_charge", "battery_power", "controller_status", "charge_limit", "discharge_limit",
                    "response_time")
    }


def virtual_views() -> list:
    batteries = [{"name": CD.REAL, "power": REAL_POWER, "soc": REAL_SOC}] + [
        {"name": name, "power": f"sensor.virtual_copy_{n}_battery_power",
         "soc": f"sensor.virtual_copy_{n}_state_of_charge"}
        for n, name in CLONES
    ]
    grids = [{"entity": "sensor.virtual_p1_meter_grid_power", "name": "Virtual P1"}]
    cluster = CD.cluster_view(group(), batteries, grids, True, CLONES)
    clones = [CD.clone_view(f"virtual_copy_{n}_", n, name, group(), known()) for n, name in CLONES]
    return [cluster, *clones]


def cards(view: dict) -> list:
    return [c for s in view["sections"] for c in s["cards"]]


def text(view: dict) -> str:
    return json.dumps(view)


def test_title_names_the_kind_of_cluster():
    assert CD.cluster_title(True) == "Virtual Cluster"
    assert CD.cluster_title(False) == "Cluster"


def test_tabs_carry_titles_and_numbered_icons():
    views = virtual_views()
    assert [v["title"] for v in views] == ["Cluster", "Virtual copy 1", "Virtual copy 2", "Virtual copy 3"]
    assert [v["icon"] for v in views] == [
        "mdi:home-battery-outline", "mdi:numeric-1-box-outline", "mdi:numeric-2-box-outline", "mdi:numeric-3-box-outline"]
    assert CD.tab_icon(4) == "mdi:numeric-4-box-outline"


def test_a_dashboard_per_kind_with_the_first_tab_named_alike():
    assert CD.cluster_url(False) == "zendure-cluster" and CD.cluster_url(True) == "virtual-cluster"
    assert virtual_views()[0]["path"] == "virtual-cluster"
    assert CD.cluster_view(group(), [], [], False)["path"] == "zendure-cluster"
    # Copies keep their own path.
    assert [v["path"] for v in virtual_views()[1:]] == ["virtual_copy_1", "virtual_copy_2", "virtual_copy_3"]


def test_real_battery_shows_on_the_cluster_tab_only():
    cluster, *clones = virtual_views()
    assert REAL_POWER in text(cluster) and REAL_SOC in text(cluster)
    assert all(REAL_POWER not in text(v) for v in clones)


def test_virtual_batteries_are_named_by_their_device_name():
    views = virtual_views()
    visible = [c.get("heading") or c.get("name") or c.get("content") for v in views for c in cards(v)]
    visible = [s for s in visible if isinstance(s, str)]
    # Entity ids keep "clone"; nothing a user reads does.
    assert not any("clone" in s.lower().replace("_clone_", "") or "twin" in s.lower() for s in visible)
    for v in views[1:]:
        assert cards(v)[0]["heading"] == f"{v['title']} (simulated)"


def test_offsets_on_the_tab_of_every_copy():
    _, first, second, third = virtual_views()
    assert "virtual_copy_2_soc_offset" not in text(first)
    for view, n in ((first, 1), (second, 2), (third, 3)):
        settings = next(s for s in view["sections"] if s["cards"][0].get("heading") == "Copy settings")
        entities = [c.get("entity") for c in settings["cards"]]
        assert f"number.virtual_copy_{n}_soc_offset" in entities
        assert f"number.virtual_copy_{n}_extra_response_time" in entities
        assert all(c["features"] == [{"type": "numeric-input", "style": "slider"}] for c in settings["cards"][1:])


def test_clone_tab_shows_the_virtual_meter_and_no_real_installation():
    for view in virtual_views()[1:]:
        body = text(view)
        assert "Virtual P1" in body
        assert "integration_version" not in body and "Foreign writes" not in body and "meter_active" not in body


def test_clone_tab_layout():
    _, first, second, _ = virtual_views()
    for view, n in ((first, 1), (second, 2)):
        assert [c["cards"][0]["heading"] for c in view["sections"]] == [
            f"Virtual copy {n} (simulated)", "Operation mode", "Overview", "Copy settings", "Measured"]
        top = [c.get("entity") for c in view["sections"][0]["cards"][1:]]
        assert top == [f"sensor.virtual_copy_{n}_{k}" for k in ("state_of_charge", "battery_power", "controller_status",
                                                                "response_time")]
        gauges = [c["entity"] for c in view["sections"][2]["cards"][1:]]
        assert gauges == ["sensor.virtual_p1_meter_grid_power", f"sensor.virtual_copy_{n}_state_of_charge",
                          f"sensor.virtual_copy_{n}_charge_limit", f"sensor.virtual_copy_{n}_discharge_limit"]
    # Copy settings: Max share, SoC offset and extra response time for every copy (copy 1 since v1.7.2).
    assert len(first["sections"][3]["cards"]) == 4 and len(second["sections"][3]["cards"]) == 4
    assert first["sections"][3]["cards"][1]["entity"] == "number.virtual_copy_1_max_share"


def test_cluster_status_names_when_the_grid_was_read():
    cluster = virtual_views()[0]
    summary = next(c["content"] for c in cards(cluster) if c.get("type") == "markdown" and "Primary" in c["content"])
    assert "**Grid at last distribution**" in summary and "**Grid**" not in summary
    # One status text: the virtual P1 as the sum it is, and the spread of the copies.
    assert "**Virtual P1**" in summary and "copies" in summary and "**SoC spread**" in summary


def test_battery_cluster_member_tab_is_headed_with_its_name():
    raw = D.TEMPLATE.read_text()
    template = json.loads(raw)
    args = {"known": set(re.findall(r"\b[a-z_]+\.[a-z0-9_]+", raw)), "battery": D.TEMPLATE_BATTERY, "meter": D.TEMPLATE_P1, "external": False,
            "ceiling": D.TEMPLATE_CEILING}
    view = CD.battery_view(template, args, "Mix north", "mix_north", None, index=2)
    assert view["title"] == "Mix north" and view["icon"] == "mdi:numeric-2-box-outline"
    assert view["sections"][0]["cards"][0]["heading"] == "Mix north"


def test_cluster_tab_order():
    cluster = virtual_views()[0]
    assert [s["cards"][0]["heading"] for s in cluster["sections"]] == [
        "Virtual Cluster (simulated)", "Operation mode", "Overview", "Distribution", "Cluster settings",
        "Today and tomorrow"]
    real = CD.cluster_view(group(), [], [], False)
    assert real["sections"][0]["cards"][0]["heading"] == "Cluster"


def test_copy_sliders_are_six_columns_wide():
    for view in virtual_views()[1:]:
        settings = next(s for s in view["sections"] if s["cards"][0].get("heading") == "Copy settings")
        assert all(c["grid_options"] == {"columns": 6, "rows": 2} for c in settings["cards"][1:])


def test_settings_are_sliders():
    cluster = virtual_views()[0]
    settings = next(s for s in cluster["sections"] if s["cards"][0].get("heading") == "Cluster settings")
    numbers = [c for c in settings["cards"][1:] if c["entity"].startswith("number.")]
    styles = {c["entity"]: c["features"][0]["style"] for c in numbers}
    assert "number.virtual_cluster_copies" in styles and "number.virtual_cluster_load_factor" in styles
    assert set(styles.values()) == {"slider"}
    # SOC protection of the group (v1.7.6): a select, 12 wide, with its options.
    protection = next(c for c in settings["cards"] if c.get("entity") == "select.virtual_cluster_soc_protection")
    assert protection["features"] == [{"type": "select-options"}]
    assert protection["grid_options"] == {"columns": 12, "rows": 2}


def test_gauges_as_on_the_battery_dashboard():
    for view in virtual_views():
        for card in cards(view):
            if card.get("type") == "gauge":
                assert card["grid_options"] == {"columns": 6, "rows": "auto"}
                assert card["name"] == {"type": "entity"}


def chart(view: dict) -> dict:
    return next(c for c in cards(view) if c.get("type") == "custom:apexcharts-card")


def test_chart_grid_lines_per_battery():
    axis = chart(virtual_views()[0])["yaxis"][0]
    assert axis["apex_config"]["stepSize"] == 1500 and "% 3000" in axis["apex_config"]["labels"]["formatter"]


def test_chart_plan_only_when_given():
    batteries = [{"name": CD.REAL, "power": REAL_POWER, "soc": REAL_SOC}]
    grids = [{"entity": "sensor.virtual_p1_meter_grid_power", "name": "Virtual P1"}]
    without = chart(CD.cluster_view(group(), batteries, grids, True, CLONES))
    with_plan = chart(CD.cluster_view(group(), batteries, grids, True, CLONES, None, "sensor.price_control_plan"))
    assert "Plan (real battery)" not in [s["name"] for s in without["series"]]
    assert "Plan (real battery)" in [s["name"] for s in with_plan["series"]]


def test_without_apexcharts_a_history_graph():
    cluster = CD.cluster_view(group(), [], [], True, CLONES, set())
    assert not any(c.get("type") == "custom:apexcharts-card" for c in cards(cluster))
    assert any(c.get("type") == "history-graph" for c in cards(cluster))


def test_integration_version_and_meter_source_on_the_cluster_tab():
    g = {**group(), "meter_source": "select.battery_cluster_meter_source"}
    real = CD.cluster_view(g, [], [], False)
    top = real["sections"][0]["cards"]
    version = next(c for c in top if c.get("name") == "Integration")
    assert version["entity"] == g["status"] and version["state_content"] == "integration_version"
    settings = next(s for s in real["sections"] if s["cards"][0].get("heading") == "Cluster settings")
    meter = settings["cards"][1]
    assert meter["entity"] == "select.battery_cluster_meter_source"
    assert meter["features"] == [{"type": "select-options"}] and meter["grid_options"] == {"columns": 12, "rows": 2}
    # A Virtual cluster has no meter source of its own.
    assert "meter_source" not in text(virtual_views()[0])


def test_reset_and_rebuild_are_not_on_the_dashboard():
    # Under Configure since v1.7.3: a deliberate step, not a tile.
    body = text(virtual_views()[0])
    assert "button.virtual_cluster_reset" not in body and "button.virtual_cluster_rebuild_dashboard" not in body


# The sample dashboard/virtual_cluster.yaml is checked in test_dashboard_yaml.py:
# since the rebuild of 1/11 it is generated with cluster_config itself.


# ── Price Control on the cluster (v1.8.0) ────────────────────────────────

GROUP_PRICE = "price_control_virtual_cluster_"


def price_known() -> set:
    """Every entity of the cluster's Price Control, as the template names them."""
    pc = next(v for v in D._load_template()["views"] if v.get("path") == CD.PRICE_PATH)
    found = set(re.findall(r"\b(?:sensor|switch|number|select)\.price_control\w*", json.dumps(pc)))
    renamed = set()
    for entity_id in found:
        if entity_id == "switch.price_control":
            renamed.add(f"switch.{GROUP_PRICE[:-1]}")
        else:
            renamed.add(entity_id.replace(".price_control_", f".{GROUP_PRICE}"))
    return renamed


def group_price_view() -> dict:
    g = group()
    return CD.price_view(D._load_template(), GROUP_PRICE, g, price_known() | known(), None,
                         g["virtual_grid"], 3, True)


def test_the_cluster_plan_in_the_chart_when_the_cluster_has_price_control():
    batteries = [{"name": CD.REAL, "power": REAL_POWER, "soc": REAL_SOC}]
    grids = [{"entity": "sensor.virtual_p1_meter_grid_power", "name": "Virtual P1"}]
    view = CD.cluster_view(group(), batteries, grids, True, CLONES, None, "sensor.price_control_plan",
                           f"sensor.{GROUP_PRICE}plan")
    names = [s["name"] for s in chart(view)["series"]]
    assert "Plan (real battery)" in names and "Plan (cluster)" in names
    real = chart(CD.cluster_view(group(), [], [], False, None, None, None, "sensor.price_control_plan"))
    assert "Plan" in [s["name"] for s in real["series"]]


def test_the_price_control_tab_of_the_cluster():
    view = group_price_view()
    assert view["title"] == "Price Control" and view["path"] == "price-control" and view["icon"] == "mdi:cash-clock"
    body = text(view)
    # Its own Price Control, the cluster's mode, nothing of a battery.
    assert f"switch.{GROUP_PRICE[:-1]}" in body and f"sensor.{GROUP_PRICE}plan" in body
    assert "select.virtual_cluster_operation_mode" in body
    assert "zendure_solarflow" not in body and CD.NO_BATTERY not in body
    assert '"sensor.price_control_status"' not in body


def test_the_price_control_tab_charts_the_cluster():
    apex = [c for c in cards(group_price_view()) if c.get("type") == "custom:apexcharts-card"]
    assert len(apex) == 1
    names = [s["name"] for s in apex[0]["series"]]
    assert names[0] == "Virtual P1" and "Plan" in names and "SoC cluster" in names
    assert "Real battery" not in names and "Plan (real battery)" not in names


def test_without_price_control_the_dashboard_is_as_before():
    # No Price Control for the cluster: the same tabs as in v1.7.6.
    assert [v["title"] for v in virtual_views()] == ["Cluster", "Virtual copy 1", "Virtual copy 2", "Virtual copy 3"]
    assert "Plan (cluster)" not in [s["name"] for s in chart(virtual_views()[0])["series"]]


def test_the_cluster_tab_shows_who_sets_the_mode():
    # v1.8.0: in the top row, named Price Control, after the grid tiles and before Integration;
    # not under Operation mode, neither as a tile nor as a badge.
    g = {**group(), "price_status": f"sensor.{GROUP_PRICE}status"}
    view = CD.cluster_view(g, [], [], True, CLONES)
    top = view["sections"][0]["cards"]
    tile = next(c for c in top if c.get("entity") == f"sensor.{GROUP_PRICE}status")
    assert tile["name"] == "Price Control" and tile["icon"] == "mdi:cash-clock"
    names = [c.get("name") for c in top]
    assert names.index("Price Control") == names.index("Integration") - 1
    section = next(s for s in view["sections"] if s["cards"][0].get("heading") == "Operation mode")
    assert f"sensor.{GROUP_PRICE}status" not in [c.get("entity") for c in section["cards"]]
    assert f"sensor.{GROUP_PRICE}status" not in [b["entity"] for b in section["cards"][0]["badges"]]
    # Without a Price Control of its own: as before.
    assert "price_control" not in text(virtual_views()[0])


def test_the_cluster_plan_shows_on_a_dark_theme():
    # v1.8.0: grey as on the battery dashboard, not near-black; the real battery's plan light grey and dashed next to it.
    plan = next(s for s in chart(group_price_view())["series"] if s["name"] == "Plan")
    assert plan["color"] == "#757575"
    batteries = [{"name": CD.REAL, "power": REAL_POWER, "soc": REAL_SOC}]
    view = CD.cluster_view(group(), batteries, [], True, CLONES, None, "sensor.price_control_plan",
                           f"sensor.{GROUP_PRICE}plan")
    series = {s["name"]: s for s in chart(view)["series"]}
    assert series["Plan (cluster)"]["color"] == "#757575"
    assert series["Plan (real battery)"]["color"] == "#BDBDBD" and series["Plan (real battery)"]["stroke_dash"] == 4
    alone = CD.cluster_view(group(), batteries, [], True, CLONES, None, "sensor.price_control_plan")
    assert next(s for s in chart(alone)["series"] if s["name"] == "Plan (real battery)")["color"] == "#757575"


def test_a_fault_of_a_battery_shows_on_the_cluster_tab():
    # v1.8.1: a badge per battery for Error and Second controller, only while on.
    members = [{"name": "Mix A", "power": None, "soc": None,
                "error": "binary_sensor.mix_a_error", "second_controller": "binary_sensor.mix_a_second_controller"},
               {"name": "Mix B", "power": None, "soc": None, "error": None, "second_controller": None}]
    view = CD.cluster_view(group(), members, [], False)
    badges = view["sections"][0]["cards"][0]["badges"]
    faults = [b for b in badges if "visibility" in b]
    assert [b["name"] for b in faults] == ["Mix A: Error", "Mix A: Second controller"]
    assert faults[0]["visibility"] == [{"condition": "state", "entity": "binary_sensor.mix_a_error", "state": "on"}]
    # A Virtual cluster: the copies have none, so nothing changes.
    assert not any("visibility" in b for b in virtual_views()[0]["sections"][0]["cards"][0]["badges"])
