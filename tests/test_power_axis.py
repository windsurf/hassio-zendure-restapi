"""The power axis of every chart has zero as its soft lower bound (v1.8.2).

Until v1.8.1 every power axis reached at least a full charge below zero
("~-3000", "~-800" for a small battery), so a quiet chart was half empty. Now
the zero line is always in view and the axis goes below it only as far as
charging or export reaches; it still follows the data in whole steps.
"""

import json

from custom_components.zendure_restapi import cluster_dashboard as C
from custom_components.zendure_restapi import dashboard as D


def power_axes(config: dict) -> list[dict]:
    return [axis for view in config["views"] for section in view.get("sections", [])
            for card in section["cards"] for axis in card.get("yaxis", []) if axis.get("id") == "power"]


def test_every_power_axis_of_the_template_starts_at_zero():
    axes = power_axes(json.loads(D.TEMPLATE.read_text(encoding="utf-8")))
    assert len(axes) == 4
    assert all(axis["min"] == "~0" and "max" not in axis for axis in axes)


def test_a_small_battery_keeps_zero_with_a_smaller_step():
    card = {"yaxis": [{"id": "power", "min": "~0", "align_to": 500, "apex_config": {"stepSize": 500}}]}
    D._scale_power_axis(card, 800)
    axis = card["yaxis"][0]
    assert axis["min"] == "~0"
    assert axis["align_to"] == 200 and axis["apex_config"]["stepSize"] == 200


def test_the_cluster_chart_starts_at_zero():
    group = {"mode": "select.virtual_cluster_operation_mode", "power": "sensor.virtual_cluster_battery_power"}
    chart = C.day_chart(group, "sensor.virtual_p1_meter_grid_power", None, None, 3)
    power = next(axis for axis in chart["yaxis"] if axis["id"] == "power")
    assert power["min"] == "~0"
