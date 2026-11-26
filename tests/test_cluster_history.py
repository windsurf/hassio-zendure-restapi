"""The History tab of a cluster (v1.8.7): the spans of a battery's History, for the group.

The grid, the group discharging and charging, the group's state of charge and
each battery's, over 30 minutes, 4 hours, 24 hours and 30 days, in the colours
of the battery dashboard. A Virtual cluster's real
battery stays out: it keeps its own dashboard. Without ApexCharts Card a
history graph per span.
"""

from custom_components.zendure_restapi import cluster_dashboard as CD

from .test_cluster_dashboard import REAL_POWER, REAL_SOC, group

MEMBERS = [
    {"name": CD.REAL, "power": REAL_POWER, "soc": REAL_SOC},
    {"name": "Virtual copy 1", "power": "sensor.virtual_copy_1_battery_power", "soc": None},
    {"name": "Virtual copy 2", "power": "sensor.virtual_copy_2_battery_power", "soc": None},
]
GRIDS = [{"entity": "sensor.virtual_p1_meter_grid_power", "name": "Virtual P1"}]


def _charts(view):
    return [c for s in view["sections"] for c in s["cards"] if c.get("type") != "heading"]


def test_four_spans_with_the_group_and_its_batteries():
    view = CD.history_view(group(), MEMBERS, GRIDS, True)
    assert (view["title"], view["path"]) == ("History", "history")
    charts = _charts(view)
    assert [c["graph_span"] for c in charts] == ["30min", "4h", "24h", "30d"]
    names = [s["name"] for s in charts[0]["series"]]
    assert names == ["Virtual P1", "Discharging", "Charging", "SoC cluster"]
    assert "statistics" not in charts[0]["series"][0]
    assert charts[3]["series"][0]["statistics"]["period"] == "hour"
    assert all(REAL_POWER not in str(c) for c in charts)


def test_without_apexcharts_a_history_graph_per_span():
    charts = _charts(CD.history_view(group(), MEMBERS, GRIDS, True, custom_cards=set()))
    assert [c["type"] for c in charts] == ["history-graph"] * 4
    assert [c["hours_to_show"] for c in charts] == [0.5, 4, 24, 720]


def test_nothing_to_show_no_tab():
    assert CD.history_view({}, [], [], False) is None


def test_colours_of_the_battery_dashboard_and_a_shade_per_battery():
    members = [dict(m, soc=f"sensor.virtual_copy_{i}_state_of_charge") for i, m in enumerate(MEMBERS) if m["name"] != CD.REAL]
    series = _charts(CD.history_view(group(), members, GRIDS, True))[0]["series"]
    colours = {s["name"]: s["color"] for s in series}
    assert colours["Virtual P1"] == "#1E88E5" and colours["Discharging"] == "#4CAF50"
    assert colours["Charging"] == "#E53935" and colours["SoC cluster"] == "#9575CD"
    shades = [colours[f"SoC {m['name']}"] for m in members]
    assert shades == list(CD.SOC_SHADES[:2]) and len(set(shades)) == 2
