"""The cluster dashboard (v1.7.1): tabs by name, one tab per virtual battery.

A Virtual cluster gets the title *Virtual Cluster*, a Cluster tab and a tab per
virtual battery, no tab for the real battery (it keeps its own dashboard), and
calls every virtual battery by its device name. A battery cluster keeps a tab
per member, headed with the member's name. No tab carries an icon: Home
Assistant then shows the icon only, and the tabs cannot be told apart.
"""

import json
import re

from custom_components.zendure_restapi import cluster_dashboard as CD
from custom_components.zendure_restapi import dashboard as D

CLONES = [(1, "Virtual Mix 1"), (2, "Virtual Mix 2"), (3, "Virtual Mix 3")]
REAL_POWER = "sensor.zendure_solarflow_3000_mix_ac_battery_power"
REAL_SOC = "sensor.zendure_solarflow_3000_mix_ac_state_of_charge"


def group() -> dict:
    g = {
        "mode": "select.virtual_cluster_operation_mode",
        "status": "sensor.virtual_cluster_status",
        "power": "sensor.virtual_cluster_group_power",
        "soc": "sensor.virtual_cluster_group_state_of_charge",
        "virtual_grid": "sensor.virtual_p1_meter_power",
        "house_load": "sensor.virtual_p1_meter_house_load",
        "real_grid": "sensor.virtual_p1_meter_real_grid",
        "reset": "button.virtual_cluster_reset",
        "rebuild": "button.virtual_cluster_rebuild_dashboard",
    }
    for n in (2, 3):
        g[f"soc_offset_{n}"] = f"number.virtual_cluster_soc_offset_clone_{n}"
        g[f"response_offset_{n}"] = f"number.virtual_cluster_response_offset_clone_{n}"
    return g


def known() -> set:
    return {
        f"sensor.virtual_mix_{n}_{key}"
        for n in (1, 2, 3)
        for key in ("state_of_charge", "battery_power", "controller_status", "charge_limit", "discharge_limit",
                    "response_time")
    }


def virtual_views() -> list:
    batteries = [{"name": CD.REAL, "power": REAL_POWER, "soc": REAL_SOC}] + [
        {"name": name, "power": f"sensor.virtual_mix_{n}_battery_power",
         "soc": f"sensor.virtual_mix_{n}_state_of_charge"}
        for n, name in CLONES
    ]
    grids = [{"entity": "sensor.virtual_p1_meter_power", "name": "Virtual P1"}]
    cluster = CD.cluster_view(group(), batteries, grids, True, CLONES)
    clones = [CD.clone_view(f"virtual_mix_{n}_", n, name, group(), known()) for n, name in CLONES]
    return [cluster, *clones]


def cards(view: dict) -> list:
    return [c for s in view["sections"] for c in s["cards"]]


def text(view: dict) -> str:
    return json.dumps(view)


def test_title_names_the_kind_of_cluster():
    assert CD.cluster_title(True) == "Virtual Cluster"
    assert CD.cluster_title(False) == "Cluster"


def test_tabs_carry_titles_not_icons():
    views = virtual_views()
    assert [v["title"] for v in views] == ["Cluster", "Virtual Mix 1", "Virtual Mix 2", "Virtual Mix 3"]
    assert not any("icon" in v for v in views)


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


def test_offsets_on_the_tab_of_the_second_and_third():
    _, first, second, third = virtual_views()
    assert "soc_offset_clone" not in text(first)
    for view, n in ((second, 2), (third, 3)):
        entities = [c.get("entity") for c in view["sections"][0]["cards"]]
        assert f"number.virtual_cluster_soc_offset_clone_{n}" in entities
        assert f"number.virtual_cluster_response_offset_clone_{n}" in entities


def test_clone_tab_shows_the_virtual_meter_and_no_real_installation():
    for view in virtual_views()[1:]:
        body = text(view)
        assert "Virtual P1" in body
        assert "integration_version" not in body and "Foreign writes" not in body and "meter_active" not in body


def test_clone_tab_layout():
    _, first, second, _ = virtual_views()
    for view, n in ((first, 1), (second, 2)):
        assert [c["cards"][0]["heading"] for c in view["sections"]] == [
            f"Virtual Mix {n} (simulated)", "Operation mode", "Overview"]
        top = [c.get("entity") for c in view["sections"][0]["cards"][1:]]
        assert top[-1] == f"sensor.virtual_mix_{n}_response_time"
        gauges = [c["name"] for c in view["sections"][2]["cards"][1:]]
        assert gauges == ["Virtual P1", "State of charge", "Charge limit", "Discharge limit"]
    assert len(first["sections"][0]["cards"]) == 5 and len(second["sections"][0]["cards"]) == 7


def test_cluster_status_names_when_the_grid_was_read():
    cluster = virtual_views()[0]
    summary = next(c["content"] for c in cards(cluster) if c.get("type") == "markdown" and "Primary" in c["content"])
    assert "**Grid at last distribution**" in summary and "**Grid**" not in summary


def test_battery_cluster_member_tab_is_headed_with_its_name():
    raw = D.TEMPLATE.read_text()
    template = json.loads(raw)
    args = {"known": set(re.findall(r"\b[a-z_]+\.[a-z0-9_]+", raw)), "battery": D.TEMPLATE_BATTERY, "meter": D.TEMPLATE_P1, "external": False,
            "ceiling": D.TEMPLATE_CEILING}
    view = CD.battery_view(template, args, "Mix north", "mix_north", None)
    assert view["title"] == "Mix north" and "icon" not in view
    assert view["sections"][0]["cards"][0]["heading"] == "Mix north"


def test_cluster_tab_order():
    cluster = virtual_views()[0]
    assert [s["cards"][0]["heading"] for s in cluster["sections"]] == [
        "Status", "Virtual P1 meter", "Distribution", "Settings", "Last 24 hours"]
