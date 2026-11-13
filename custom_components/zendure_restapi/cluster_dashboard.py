"""The cluster dashboard: a Cluster tab and a tab per battery (v1.7.1).

A dashboard of its own per kind (v1.7.3): `/zendure-cluster/zendure-cluster`
for the battery cluster, *Cluster*, and `/virtual-cluster/virtual-cluster` for
the Virtual cluster, *Virtual Cluster*. Dashboard and first tab share the name,
like the battery dashboard `/zendure-restapi/zendure-restapi`. Each kind
creates, rebuilds and removes only its own. Until v1.7.2 both used
`/zendure-cluster`; one a Virtual cluster made there stays until the user
removes it, and a notification says so.

* Cluster — since v1.7.2 in the layout of the battery dashboard: tiles under
  the title, the operation mode with one status text, gauges, a row per battery
  with role, direction, limit and power, the group's settings as sliders, and
  a chart of today and tomorrow (ApexCharts Card, else a history graph). A
  Virtual cluster adds its Virtual P1 meter, its test settings (Clones, Load
  factor), and in the chart the real battery and its day plan. Price Control
  and the device itself stay on the battery dashboard;
* a battery cluster: one tab per member, built from the first page of the
  battery dashboard (dashboard.py, the same template and the same rules for
  missing entities), headed with the member's name;
* a Virtual cluster: one tab per virtual battery (Virtual copy 1, 2, 3), with
  its own settings as sliders (Max share, SoC offset, extra response time). The real battery is no member and keeps its own
  dashboard; it gets no tab here and shows on the Cluster tab only, for
  comparison.

Every tab carries an icon and its title (v1.7.3): the Cluster tab the
house with a battery of its heading (mdi:home-battery-outline), the batteries
and copies a numbered one,
mdi:numeric-1-box-outline to mdi:numeric-4-box-outline, so tabs that show
only their icon can still be told apart; the title is the tooltip. Every
virtual battery is called by its device name (Virtual copy n) throughout.

Made once, when the group is set up and no cluster dashboard exists yet. After
that only on request, with the Rebuild dashboard button: a change in the group
brings a notification, never a silent overwrite.
"""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import (
    CONF_BATTERY_ENTRY, CONF_DEVICE_TYPE, DEVICE_TYPE_CLUSTER, DEVICE_TYPE_PRICE, DEVICE_TYPE_VIRTUAL, DOMAIN,
    SUBENTRY_BATTERY,
)
from .dashboard import (
    CARD_NAMING,
    CREATED,
    EXISTS,
    EXTERNAL_PV_DASH,
    EXTERNAL_PV_NAME,
    FAILED,
    PV_ENERGY,
    PV_POWER,
    REPLACED,
    _collection,
    _custom_cards,
    _dashboards,
    _load_template,
    build_config,
)

_LOGGER = logging.getLogger(__name__)

CLUSTER_URL = "zendure-cluster"
VIRTUAL_URL = "virtual-cluster"
CLUSTER_TITLE = "Cluster"
VIRTUAL_TITLE = "Virtual Cluster"
# Home Assistant wants a hyphen in the path of a dashboard; both stay clear of
# dashboards of other integrations.
CLUSTER_ICON = "mdi:battery-sync"
CLUSTER_NOTIFICATION = "zendure_restapi_cluster_dashboard"
VIRTUAL_NOTIFICATION = "zendure_restapi_virtual_cluster_dashboard"
OLD_VIRTUAL_NOTIFICATION = "zendure_restapi_old_virtual_dashboard"
# The real battery next to a Virtual cluster, on the Cluster tab.
REAL = "Real"
# The template heading of a battery tab; replaced by the battery's own name.
TEMPLATE_HEADING = "Zendure Home Battery"
CLONE_ICON = "mdi:home-battery-outline"
# A battery of the cluster is still being set up (reloaded when it joins):
# its dashboard tab cannot be built yet (v1.7.3).
NOT_READY = "not_ready"


class MembersNotReady(Exception):
    """A battery of the cluster has no coordinator yet."""


def cluster_url(virtual: bool) -> str:
    """The path of the dashboard, and of its first tab (v1.7.3)."""
    return VIRTUAL_URL if virtual else CLUSTER_URL


def _notification(virtual: bool) -> str:
    return VIRTUAL_NOTIFICATION if virtual else CLUSTER_NOTIFICATION


def tab_icon(index: int) -> str:
    """The numbered icon of the tab of battery or copy `index` (v1.7.3)."""
    return f"mdi:numeric-{max(1, min(9, index))}-box-outline"


def cluster_title(virtual: bool) -> str:
    """The dashboard title: *Virtual Cluster* for a Virtual cluster, else *Cluster*."""
    return VIRTUAL_TITLE if virtual else CLUSTER_TITLE


def _is_virtual(entry: ConfigEntry) -> bool:
    return entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_VIRTUAL


def _entity(registry: er.EntityRegistry, platform: str, unique_id: str) -> str | None:
    return registry.async_get_entity_id(platform, DOMAIN, unique_id)


def _tile(entity_id: str | None, **extra: Any) -> dict[str, Any] | None:
    if not entity_id:
        return None
    card = {"type": "tile", "entity": entity_id, **extra}
    if CARD_NAMING and "name" not in card:
        card["name"] = {"type": "entity"}
    return card


def _section(heading: str, icon: str, cards: list[Any], span: int = 2,
             badges: list[str] | None = None) -> dict[str, Any] | None:
    cards = [c for c in cards if c]
    if not cards:
        return None
    head: dict[str, Any] = {"type": "heading", "heading": heading, "heading_style": "title", "icon": icon}
    if badges:
        head["badges"] = [{"type": "entity", "entity": b} for b in badges]
    return {"type": "grid", "column_span": span, "cards": [head, *cards]}


def _markdown(content: str) -> dict[str, Any]:
    return {"type": "markdown", "content": content, "grid_options": {"columns": "full", "rows": "auto"}}


def distribution_table(status: str, powers: dict[str, str]) -> str:
    """The Distribution table: what the cluster asks of each battery, and what it does.

    A secondary holds the limit the cluster gives it. The primary runs its own
    loop on the meter and gets no limit from the cluster, so its direction is
    read from its measured power (more than 25 W either way) and its limit reads
    *own loop* (v1.7.2). `powers` maps a battery's name to its power sensor.
    """
    return (
        "| Battery | Role | Direction | Limit | Power |\n|---|---|---|---:|---:|\n"
        "{% set b = state_attr('" + status + "', 'batteries') or {} %}"
        "{% set p = " + json.dumps(powers) + " %}"
        "{% for name, v in b.items() %}"
        "{% set w = states(p[name]) | float(none) if name in p else none %}"
        "{% if v.role == 'primary' %}"
        "{% set d = 'none' if w is none or (w | abs) <= 25 else ('discharge' if w > 0 else 'charge') %}"
        "| {{ name }} | primary | {{ d }} | own loop |"
        "{% else %}"
        "| {{ name }} | {{ v.role }} | {{ v.direction }} | {{ v.limit }} W |"
        "{% endif %}"
        " {{ (w | round(0) | int) ~ ' W' if w is not none else '–' }} |\n{% endfor %}"
    )


APEX = "custom:apexcharts-card"
STEP_W = 500                    # one grid line per 500 W per battery in the chart
POWER_SEV = {"green": -1500, "yellow": 0, "red": 1500}
SOC_SEV = {"green": 70, "yellow": 30, "red": 0}


def _gauge(entity_id: str | None, lo: int, hi: int, severity: dict[str, int] | None = None) -> dict[str, Any] | None:
    """A gauge as on the battery dashboard: named after its entity, six columns wide."""
    if not entity_id:
        return None
    card: dict[str, Any] = {"type": "gauge", "entity": entity_id, "min": lo, "max": hi, "needle": True,
                            "grid_options": {"columns": 6, "rows": "auto"}}
    if severity:
        card["severity"] = severity
    if CARD_NAMING:
        card["name"] = {"type": "entity"}
    return card


def _control(entity_id: str | None, style: str = "slider", wide: bool = False, **extra: Any) -> dict[str, Any] | None:
    """A setting as on the battery dashboard: a tile with a slider (or buttons)."""
    card = _tile(entity_id, features_position="bottom", features=[{"type": "numeric-input", "style": style}], **extra)
    if card:
        # Six columns stated, not left to Home Assistant: once a copy's
        # sliders came out 8 wide (v1.7.3). Wide ones take the full row.
        card["grid_options"] = {"columns": 12 if wide else 6, "rows": 2}
    return card


def status_text(group: dict[str, Any], virtual: bool) -> str:
    """One status text under Operation mode: status and reason, primary, how many
    run, demand, the grid the last distribution was decided on; for a Virtual
    cluster the virtual P1 as the sum it is, and the spread of the copies (v1.7.2)."""
    s, soc = str(group.get("status")), str(group.get("soc"))
    text = (
        "{% set s = '" + s + "' %}{% set b = state_attr(s, 'batteries') or {} %}\n"
        "**Status** {{ states(s) }} – {{ state_attr(s, 'reason') }}\n\n"
        "**Primary** {{ state_attr(s, 'primary') or '–' }} | **Running** "
        "{{ b.values() | selectattr('limit', 'gt', 0) | list | count + (1 if state_attr(s, 'primary') else 0) }} of {{ b | count }}"
        " | **Demand** {{ state_attr(s, 'demand') }} W | **Grid at last distribution** {{ state_attr(s, 'grid') }} W\n"
    )
    if virtual and group.get("virtual_grid") and group.get("house_load"):
        lf = group.get("load_factor")
        text += (
            "\n{% set p1 = states('" + group["virtual_grid"] + "') | float(0) %}"
            "{% set house = states('" + group["house_load"] + "') | float(0) %}"
            "{% set lf = " + (f"states('{lf}') | float(1)" if lf else "1") + " %}"
            "**Virtual P1** {{ p1 | round(0) | int }} W = house {{ house | round(0) | int }} W × {{ lf }}"
            " − copies {{ (house * lf - p1) | round(0) | int }} W"
            " | **SoC spread** {{ state_attr('" + soc + "', 'spread') }} points\n"
        )
    return text


def _series(entity_id: str, name: str, color: str, kind: str = "line", axis: str = "power", **extra: Any) -> dict[str, Any]:
    serie: dict[str, Any] = {
        "entity": entity_id, "name": name, "type": kind, "unit": "%" if axis == "soc" else "W", "color": color,
        "stroke_width": 1 if kind == "area" else 1.5, "curve": "monotoneCubic", "float_precision": 0,
        "extend_to": "now", "show": {"in_header": False, "legend_value": False}, "yaxis_id": axis,
        "statistics": {"type": "mean", "period": "5minute", "align": "middle"},
    }
    if kind == "area":
        serie["opacity"] = 0.3
    serie.update(extra)
    return serie


PLAN_GENERATOR = (
    "const a = entity.attributes;\nconst now = Date.now();\nconst pts = [[now, 0, 0]];\n"
    "[...(a.charge_periods || []), ...(a.discharge_periods || [])].forEach(r => {\n"
    "  pts.push([Math.max(new Date(r.start).getTime(), now), r.power, 1]);\n"
    "  pts.push([new Date(r.end).getTime(), 0, 0]);\n});\n"
    "if (a.plan_until) pts.push([new Date(a.plan_until).getTime(), 0, 0]);\n"
    "pts.sort((x, y) => x[0] - y[0] || x[2] - y[2]);\nreturn pts.map(p => [p[0], p[1]]);\n"
)
# The day plan of the cluster's own Price Control (v1.8.0): grey and solid,
# as the plan on the battery dashboard, so it shows on a dark theme as well.
# Next to it the real battery's plan behind a Virtual cluster turns light grey
# and dashed, so the two can be told apart.
GROUP_PLAN_COLOR = "#757575"
REAL_PLAN_COLOR = "#BDBDBD"
REAL_PLAN_DASH = 4


def _plan_series(entity_id: str, name: str, color: str, dash: int | None = None) -> dict[str, Any]:
    serie: dict[str, Any] = {
        "entity": entity_id, "name": name, "type": "line", "unit": "W", "stroke_width": 1.5,
        "curve": "stepline", "float_precision": 0, "color": color,
        "show": {"in_header": False, "legend_value": False}, "yaxis_id": "power",
        "data_generator": PLAN_GENERATOR,
    }
    if dash:
        serie["stroke_dash"] = dash
    return serie


# The solar power in the cluster's charts (v1.8.0), in the colour and shape of
# the battery dashboard's PV series. One entry per source: a battery's own PV,
# or the external solar sensor chosen under Configure for that battery.
PV_COLOR = "#FFB300"


def solar_sources(name: str, prefix: str, args: dict[str, Any], known: set[str]) -> list[dict[str, Any]]:
    """The solar sources of one battery, as on its own dashboard.

    The external solar sensor when one is chosen; the device's own PV when it
    has produced something. With both, the external one follows as a series
    of its own. Without either, nothing.
    """
    sources: list[dict[str, Any]] = []
    own = f"sensor.{prefix}{PV_POWER}"
    if args.get("own_pv") and own in known:
        energy = f"sensor.{prefix}{PV_ENERGY}"
        sources.append({"power": own, "energy": energy if energy in known else None,
                        "battery": name, "external": False})
    if args.get("solar_power"):
        sources.append({"power": args["solar_power"], "energy": args.get("solar_energy") or None,
                        "battery": name, "external": True})
    return sources


def add_solar_sources(solars: list[dict[str, Any]], sources: list[dict[str, Any]]) -> None:
    """Add a battery's sources; an external sensor chosen for more batteries shows once."""
    for source in sources:
        if all(s["power"] != source["power"] for s in solars):
            solars.append(source)


def _solar_name(source: dict[str, Any], solars: list[dict[str, Any]]) -> str:
    """PV for a single source; with more, the battery's name, external ones marked."""
    if len(solars) == 1:
        return "PV"
    if source["external"]:
        externals = [s for s in solars if s["external"]]
        return EXTERNAL_PV_NAME if len(externals) == 1 else f"{EXTERNAL_PV_NAME} {source['battery']}"
    owns = [s for s in solars if not s["external"]]
    return "PV" if len(owns) == 1 else f"PV {source['battery']}"


def _solar_series(group: dict[str, Any]) -> list[dict[str, Any]]:
    """A PV power series per source; an external one next to another source dashed."""
    solars = group.get("solar") or []
    series = []
    for source in solars:
        extra: dict[str, Any] = {}
        if source["external"] and len(solars) > 1:
            extra["stroke_dash"] = EXTERNAL_PV_DASH
        series.append(_series(source["power"], _solar_name(source, solars), PV_COLOR, "area", **extra))
    return series


def day_chart(group: dict[str, Any], grid: str | None, real: dict[str, str | None] | None,
              plan: str | None, count: int, group_plan: str | None = None) -> dict[str, Any]:
    """The chart of today and tomorrow, as the day plan chart of the battery
    dashboard: the grid, the cluster discharging and charging, its state of
    charge; for a Virtual cluster also the real battery and its plan. One grid
    line per 500 W per battery, a label every second line (v1.7.2). Since
    v1.8.0 also the day plan of the cluster's own Price Control and the solar
    power."""
    step = STEP_W * max(1, count)
    series: list[dict[str, Any]] = []
    if grid:
        series.append(_series(grid, "Virtual P1" if real else "Grid", "#1E88E5", "area"))
    if group.get("power"):
        series.append(_series(group["power"], "Discharging", "#4CAF50", "area", transform="return x > 0 ? x : 0;"))
        series.append(_series(group["power"], "Charging", "#E53935", "area", transform="return x < 0 ? x : 0;"))
    series.extend(_solar_series(group))
    if real and real.get("power"):
        series.append(_series(real["power"], "Real battery", "#757575"))
    if plan:
        # Grey as before; light grey and dashed once the cluster has a plan of its own.
        if group_plan:
            series.append(_plan_series(plan, "Plan (real battery)", REAL_PLAN_COLOR, REAL_PLAN_DASH))
        else:
            series.append(_plan_series(plan, "Plan (real battery)", "#757575"))
    if group_plan:
        series.append(_plan_series(group_plan, "Plan (cluster)" if real else "Plan", GROUP_PLAN_COLOR))
    if group.get("soc"):
        series.append(_series(group["soc"], "SoC cluster", "#9575CD", axis="soc", stroke_width=2, stroke_dash=4))
    if real and real.get("soc"):
        series.append(_series(real["soc"], "SoC real", "#757575", axis="soc", stroke_dash=2))
    return {
        "type": APEX, "graph_span": "48h", "span": {"start": "day"}, "now": {"show": True, "label": "Now"},
        "header": {"show": False},
        "yaxis": [
            {"id": "power", "min": "~-3000", "decimals": 0, "align_to": step, "apex_config": {
                "stepSize": step,
                "labels": {"formatter": "EVAL:function (v) { return Math.round(v) % "
                           + str(2 * step) + " === 0 ? Math.round(v) : \"\"; }"}}},
            {"id": "soc", "opposite": True, "min": 0, "max": 100, "decimals": 0,
             "apex_config": {"tickAmount": 4, "labels": {"style": {"fontSize": "10px"}}}},
        ],
        "apex_config": {"chart": {"height": 240}, "legend": {"show": True}, "tooltip": {"x": {"format": "dd-MM HH:mm"}},
                        "grid": {"borderColor": "rgba(127, 127, 127, 0.25)", "strokeDashArray": 0},
                        "fill": {"type": "solid"}},
        "series": series, "grid_options": {"columns": "full", "rows": "auto"}, "hours_12": False,
    }


def cluster_view(
    group: dict[str, Any],
    batteries: list[dict[str, str | None]],
    grids: list[dict[str, str | None]],
    virtual: bool,
    clones: list[tuple[int, str]] | None = None,
    custom_cards: set[str] | None = None,
    plan: str | None = None,
    group_plan: str | None = None,
) -> dict[str, Any]:
    """The Cluster tab, in the layout of the battery dashboard (v1.7.2): a row
    of tiles under the title, the operation mode with one status text, gauges,
    the distribution, the settings of the group as sliders, and the chart of
    today and tomorrow. What is set per battery lives on its own tab; Price
    Control and the device itself stay on the battery dashboard.

    `group` holds the entity ids of the group's own entities, `batteries` per
    battery name, power and soc (a Virtual cluster's real battery first, named
    REAL), `grids` the grid sensors with their name, `clones` index and name of
    each virtual battery, `custom_cards` the custom cards installed (None: all),
    `plan` the day plan of the real battery behind a Virtual cluster,
    `group_plan` the day plan of the cluster's own Price Control (v1.8.0).
    """
    clones = clones or []
    real = next((b for b in batteries if b.get("name") == REAL), None) if virtual else None
    members = [b for b in batteries if b is not real]
    count = len(clones) if virtual else len(members)
    grid = group.get("virtual_grid") if virtual else next((g["entity"] for g in grids if g.get("entity")), None)
    real_grid = next((g["entity"] for g in grids if g.get("entity") and g.get("entity") != grid), None) if virtual else None
    powers = {b["name"]: b["power"] for b in batteries if b.get("power")}
    cap = 3000 * max(1, count)

    if custom_cards is None or APEX in custom_cards:
        chart: dict[str, Any] = day_chart(group, grid, real, plan if virtual else None, count, group_plan)
    else:
        chart = {"type": "history-graph", "hours_to_show": 24, "grid_options": {"columns": "full", "rows": 6},
                 "entities": [{"entity": e} for e in (grid, group.get("power"), *(b.get("power") for b in batteries),
                                                      *(s["power"] for s in group.get("solar") or [])) if e]}

    sections = [
        # A Virtual cluster says it is simulated, as its copies do (v1.7.3).
        _section(f"{VIRTUAL_TITLE} (simulated)" if virtual else cluster_title(virtual), CLONE_ICON, [
            _tile(group.get("soc"), icon="mdi:battery-charging"),
            _tile(group.get("power"), icon="mdi:flash"),
            _tile(group.get("members"), icon="mdi:battery-sync-outline"),
            _tile(grid, icon="mdi:transmission-tower"),
            _tile(group.get("house_load"), icon="mdi:home-lightning-bolt") if virtual else None,
            _tile(group.get("real_grid"), icon="mdi:transmission-tower-import") if virtual else None,
            # v1.8.0: the cluster's own Price Control, so the top row shows who sets the mode.
            _tile(group.get("price_status"), name="Price Control", icon="mdi:cash-clock"),
            # v1.7.3: which version runs, as on the battery dashboard.
            _tile(group.get("status"), name="Integration", icon="mdi:tag-outline",
                  state_content="integration_version"),
        ], span=4, badges=[e for e in (group.get("soc"), group.get("status"), group.get("mode")) if e]),
        _section("Operation mode", "mdi:state-machine", [
            _markdown(status_text(group, virtual)) if group.get("status") else None,
            _tile(group.get("mode"), icon="mdi:state-machine", features_position="bottom",
                  features=[{"type": "select-options"}], grid_options={"columns": 12, "rows": 2}),
            # v1.7.5: the cluster's trace, one CSV row per decision.
            _tile(group.get("trace"), icon="mdi:record-rec"),
            # Reset and Rebuild dashboard are under Configure since v1.7.3: a
            # deliberate step rather than a tile one tap away.
        ], badges=[e for e in (group.get("mode"), group.get("status")) if e]),
        _section("Overview", "mdi:gauge", [
            _gauge(grid, -3000, 3000, POWER_SEV),
            _gauge(group.get("soc"), 0, 100, SOC_SEV),
            _gauge(group.get("power"), -cap, cap),
            _gauge(real_grid, -3000, 3000, POWER_SEV),
        ]),
        _section("Distribution", "mdi:call-split", [
            _markdown(distribution_table(str(group.get("status")), powers)) if group.get("status") else None,
        ]),
        _section("Cluster settings", "mdi:tune", [
            # v1.7.3: the meter source of a battery cluster, as on a battery.
            _tile(group.get("meter_source"), icon="mdi:meter-electric-outline", features_position="bottom",
                  features=[{"type": "select-options"}], grid_options={"columns": 12, "rows": 2}),
            # v1.7.6: SOC protection of the group.
            _tile(group.get("soc_protection"), icon="mdi:battery-lock", features_position="bottom",
                  features=[{"type": "select-options"}], grid_options={"columns": 12, "rows": 2}),
            _control(group.get("clones"), icon="mdi:content-copy"),
            _control(group.get("load_factor"), icon="mdi:multiplication"),
            *(_control(group.get(k)) for k in (
                "charge_buffer", "discharge_buffer", "start_charge_at", "start_discharge_at",
                "stop_charge_below", "stop_discharge_below", "stop_after", "start_after",
                "concentrate_below", "redistribute_every",
            )),
        ]),
        _section("Today and tomorrow", "mdi:chart-line", [chart], span=4),
    ]
    return {
        "title": "Cluster",
        "path": cluster_url(virtual),
        "icon": CLONE_ICON,
        "type": "sections",
        "max_columns": 4,
        "sections": [s for s in sections if s],
    }


def battery_view(template: dict[str, Any], args: dict[str, Any], title: str, path: str,
                 custom_cards: set[str] | None, index: int = 1) -> dict[str, Any] | None:
    """The first page of the battery dashboard for one member of a battery
    cluster, headed with its name; the tab carries a numbered icon (v1.7.3)."""
    config = build_config(copy.deepcopy(template), custom_cards=custom_cards, card_naming=CARD_NAMING, **args)
    views = config.get("views") or []
    if not views:
        return None
    view = views[0]
    view.pop("icon", None)
    for section in view.get("sections", [])[:1]:
        for card in section.get("cards", []):
            if card.get("type") == "heading" and card.get("heading") == TEMPLATE_HEADING:
                card["heading"] = title
                break
    view["title"] = title
    view["path"] = path
    view["icon"] = tab_icon(index)
    return view


# The Price Control tab of the battery dashboard, the source of the cluster's
# (v1.8.0), and a battery prefix that matches nothing: the battery's own
# cards drop out of it by the usual rule for entities that do not exist.
PRICE_PATH = "price-control"
NO_BATTERY = "cluster_has_no_battery_"


def price_view(template: dict[str, Any], price: str, group: dict[str, Any], known: set[str],
               custom_cards: set[str] | None, grid: str | None, count: int, virtual: bool) -> dict[str, Any] | None:
    """The Price Control tab of a cluster with a Price Control of its own (v1.8.0).

    The tab of the battery dashboard, fitted by the same builder to the
    cluster's Price Control (`price`, its entity id prefix): the switch, the
    decision, the day plan, the bands and the tariff. The battery's own cards
    and series drop out; the heading carries the cluster's Operation mode, and
    the chart is the cluster's: the grid, the cluster charging and discharging,
    its state of charge and its plan.
    """
    source = next((v for v in template.get("views", []) if v.get("path") == PRICE_PATH), None)
    if source is None:
        return None
    config = build_config(
        {"views": [copy.deepcopy(source)]}, known=known, battery=NO_BATTERY, meter=None, external=False,
        ceiling=3000, price=price, custom_cards=custom_cards, card_naming=CARD_NAMING,
    )
    views = config.get("views") or []
    if not views:
        return None
    view = views[0]
    plan = f"sensor.{price}plan"
    for section in view.get("sections", []):
        cards = section.get("cards", [])
        for index, card in enumerate(cards):
            if card.get("type") == "heading" and card.get("heading") == "Price Control" and group.get("mode"):
                card["badges"] = [*card.get("badges", []), {"type": "entity", "entity": group["mode"]}]
            if card.get("type") == APEX:
                chart = day_chart(group, grid, None, None, count, plan if plan in known else None)
                if virtual and chart["series"] and chart["series"][0]["entity"] == grid:
                    chart["series"][0]["name"] = "Virtual P1"
                cards[index] = chart
    view["title"] = "Price Control"
    view["path"] = PRICE_PATH
    view["icon"] = "mdi:cash-clock"
    return view


def clone_view(prefix: str, index: int, name: str, group: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """The tab of one virtual battery (v1.7.2): what its
    controller does on the model of the device, gauges, its own settings as
    sliders (Max share, SoC offset, extra response time; copy 1 too since
    v1.7.2) and its measured response time."""
    status = f"sensor.{prefix}controller_status"

    def own(key: str) -> str | None:
        entity = f"sensor.{prefix}{key}"
        return entity if entity in known else None

    card = (
        "{% set c = '" + status + "' %}\n**Status** {{ states(c) }} - {{ state_attr(c, 'reason') }}\n\n"
        "**Direction** {{ state_attr(c, 'direction') }} | **Target** {{ state_attr(c, 'target_power') }} W\n\n"
        "**Virtual P1** {{ state_attr(c, 'meter_power') }} W | **Battery** {{ state_attr(c, 'battery_power') }} W\n\n"
        "**Trim writes** {{ state_attr(c, 'trim_writes') }}\n"
    )
    response = own("response_time")
    sections = [
        _section(f"{name} (simulated)", CLONE_ICON, [
            _tile(own("state_of_charge"), icon="mdi:battery-charging"),
            _tile(own("battery_power"), icon="mdi:flash"),
            _tile(status if status in known else None, icon="mdi:cog-sync-outline"),
            _tile(response),
        ], span=4, badges=[e for e in (own("state_of_charge"), status if status in known else None) if e]),
        _section("Operation mode", "mdi:state-machine", [_markdown(card)] if status in known else []),
        _section("Overview", "mdi:gauge", [
            _gauge(group.get("virtual_grid"), -3000, 3000, POWER_SEV),
            _gauge(own("state_of_charge"), 0, 100, SOC_SEV),
            _gauge(own("charge_limit"), 0, 3000),
            _gauge(own("discharge_limit"), 0, 3000),
        ]),
        _section("Copy settings", "mdi:tune-variant", [
            # Six columns, as every slider elsewhere (v1.7.3).
            _control(group.get(f"max_share_clone_{index}"), icon="mdi:arrow-collapse-vertical"),
            _control(group.get(f"soc_offset_{index}"), icon="mdi:battery-arrow-up-outline"),
            _control(group.get(f"response_offset_{index}"), icon="mdi:timer-sand"),
        ]),
        _section("Measured", "mdi:stethoscope", [
            _tile(response, icon="mdi:timer-outline", features_position="bottom",
                  features=[{"type": "trend-graph", "hours_to_show": 24}], grid_options={"columns": 12, "rows": 2}),
        ]),
    ]
    return {
        "title": name,
        "path": slugify(name) or f"virtual_{index}",
        "icon": tab_icon(index),
        "type": "sections",
        "max_columns": 4,
        "sections": [s for s in sections if s],
    }


def cluster_config(hass: HomeAssistant, entry: ConfigEntry, template: dict[str, Any],
                 custom_cards: set[str] | None) -> dict[str, Any]:
    """The whole cluster dashboard for a cluster or a Virtual cluster entry."""
    from . import dashboard_args  # the battery dashboard's own arguments

    registry = er.async_get(hass)
    known = {e.entity_id for e in registry.entities.values() if e.platform == DOMAIN and e.disabled_by is None}
    virtual = _is_virtual(entry)
    eid = entry.entry_id
    group: dict[str, Any] = {
        "mode": _entity(registry, "select", f"{eid}_operation_mode"),
        "status": _entity(registry, "sensor", f"{eid}_cluster_status"),
        "power": _entity(registry, "sensor", f"{eid}_group_power"),
        "soc": _entity(registry, "sensor", f"{eid}_group_state_of_charge"),
        "members": _entity(registry, "sensor", f"{eid}_members"),
        "virtual_grid": _entity(registry, "sensor", f"{eid}_virtual_p1_power"),
        "house_load": _entity(registry, "sensor", f"{eid}_virtual_p1_house_load"),
        "real_grid": _entity(registry, "sensor", f"{eid}_virtual_p1_real_grid"),
        "reset": _entity(registry, "button", f"{eid}_reset"),
        "rebuild": _entity(registry, "button", f"{eid}_rebuild_dashboard"),
        # v1.7.3: the meter the group steers on; a battery cluster only.
        "meter_source": _entity(registry, "select", f"{eid}_meter_source"),
        # v1.7.5: Trace recording of the cluster.
        "trace": _entity(registry, "switch", f"{eid}_trace_recording"),
        # v1.7.6: SOC protection of the group.
        "soc_protection": _entity(registry, "select", f"{eid}_soc_protection"),
    }
    for key in ("clones", "load_factor", "soc_offset_1", "soc_offset_2", "soc_offset_3",
                "response_offset_1", "response_offset_2", "response_offset_3",
                "max_share_clone_1", "max_share_clone_2", "max_share_clone_3", "charge_buffer", "discharge_buffer",
                "start_charge_at", "start_discharge_at", "stop_charge_below", "stop_discharge_below",
                "stop_after", "start_after", "concentrate_below", "redistribute_every"):
        group[key] = _entity(registry, "number", f"{eid}_{key}")

    views: list[dict[str, Any]] = []
    batteries: list[dict[str, str | None]] = []
    grids: list[dict[str, str | None]] = []
    clones: list[tuple[int, str]] = []
    plan: str | None = None
    # The solar sources of the batteries, for the PV series.
    solars: list[dict[str, Any]] = []
    group["solar"] = solars
    store = hass.data.get(DOMAIN, {})

    def add_battery(battery_entry: ConfigEntry, name: str, grid_name: str | None) -> dict[str, Any]:
        """Power and SoC of a real battery for the Cluster tab, and its grid sensor for the chart."""
        if store.get(battery_entry.entry_id) is None:
            # Joining a cluster reloads the battery (v1.7.3).
            raise MembersNotReady(battery_entry.title)
        args = dashboard_args(hass, battery_entry)
        prefix = args["battery"]
        batteries.append({
            "name": name,
            "power": f"sensor.{prefix}battery_power" if f"sensor.{prefix}battery_power" in known else None,
            "soc": f"sensor.{prefix}state_of_charge" if f"sensor.{prefix}state_of_charge" in known else None,
        })
        if args.get("grid") and all(g["entity"] != args["grid"] for g in grids):
            grids.append({"entity": args["grid"], "name": grid_name})
        # The copies of a Virtual cluster have no PV: the real battery's is the cluster's.
        add_solar_sources(solars, solar_sources(battery_entry.title if name == REAL else name, prefix, args, known))
        return args

    if virtual:
        vc = store.get(eid)
        source = hass.config_entries.async_get_entry(getattr(vc, "source_id", "") or "")
        if group["virtual_grid"]:
            grids.append({"entity": group["virtual_grid"], "name": "Virtual P1"})
        if source is not None:
            # The real battery is shown for comparison, not as a member, and
            # keeps its own dashboard: no tab here.
            args = add_battery(source, REAL, "Real P1")
            # The real battery's day plan: the copies follow it in follow_source.
            price = args.get("price")
            if price and f"sensor.{price}plan" in known:
                plan = f"sensor.{price}plan"
        for clone in getattr(vc, "clones", []):
            prefix = None
            status = _entity(registry, "sensor", f"{clone.device_id}_controller_status")
            if status:
                prefix = status.split(".", 1)[1][: -len("controller_status")]
            if not prefix:
                continue
            index = getattr(clone, "index", len(clones) + 1)
            clones.append((index, clone.name))
            batteries.append({
                "name": clone.name,
                "power": f"sensor.{prefix}battery_power",
                "soc": f"sensor.{prefix}state_of_charge",
            })
            views.append(clone_view(prefix, index, clone.name, group, known))
    else:
        for sub in entry.subentries.values():
            if sub.subentry_type != SUBENTRY_BATTERY:
                continue
            battery_entry = hass.config_entries.async_get_entry(str(sub.data.get(CONF_BATTERY_ENTRY) or ""))
            if battery_entry is None:
                continue
            args = add_battery(battery_entry, battery_entry.title, None)
            view = battery_view(template, args, battery_entry.title, slugify(battery_entry.title) or "battery",
                                custom_cards, index=len(views) + 1)
            if view:
                views.append(view)

    # The cluster's own Price Control (v1.8.0): its plan in the chart, and a tab after Cluster.
    price = group_price(hass, registry, eid)
    group_plan = f"sensor.{price}plan" if price and f"sensor.{price}plan" in known else None
    if price and f"sensor.{price}status" in known:
        group["price_status"] = f"sensor.{price}status"
    cluster = cluster_view(group, batteries, grids, virtual, clones, custom_cards, plan, group_plan)
    tabs = [cluster]
    if price:
        count = len(clones) if virtual else len(batteries)
        grid = group.get("virtual_grid") if virtual else next((g["entity"] for g in grids if g.get("entity")), None)
        view = price_view(template, price, group, known, custom_cards, grid, count, virtual)
        if view:
            tabs.append(view)
    return {"title": cluster_title(virtual), "views": [*tabs, *views]}


def group_price(hass: HomeAssistant, registry: er.EntityRegistry, cluster_id: str) -> str | None:
    """The entity id prefix of the Price Control that steers this cluster (v1.8.0), or None."""
    for other in hass.config_entries.async_entries(DOMAIN):
        if other.data.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_PRICE or other.data.get(CONF_BATTERY_ENTRY) != cluster_id:
            continue
        status = _entity(registry, "sensor", f"{other.entry_id}_price_control_status")
        if status:
            object_id = status.split(".", 1)[1]
            if object_id.endswith("status"):
                return object_id[: -len("status")]
    return None


async def _sync_title(collection: Any, title: str, url: str = CLUSTER_URL) -> None:
    """Give the sidebar the title of this kind of cluster on a rebuild. A title
    the user chose stays: only one of the two standard titles is replaced."""
    for item_id, item in list(collection.data.items()):
        if item.get("url_path") != url:
            continue
        if item.get("title") != title and item.get("title") in (CLUSTER_TITLE, VIRTUAL_TITLE):
            await collection.async_update_item(item_id, {"title": title})
        return


async def async_cluster_dashboard(hass: HomeAssistant, entry: ConfigEntry, replace: bool) -> str:
    """Create the cluster dashboard, or with `replace` give the existing one a fresh configuration."""
    try:
        dashboards = _dashboards(hass)
        if dashboards is None:
            return FAILED
        virtual = _is_virtual(entry)
        url = cluster_url(virtual)
        existing = dashboards.get(url)
        if existing is not None and not replace:
            return EXISTS
        collection = None if existing is not None else _collection(hass)
        if existing is None and collection is None:
            return FAILED
        title = cluster_title(virtual)
        template = await hass.async_add_executor_job(_load_template)
        try:
            config = cluster_config(hass, entry, template, await _custom_cards(hass))
        except MembersNotReady as err:
            _LOGGER.info("Cluster dashboard waits for %s, which is still being set up", err)
            return NOT_READY
        if existing is not None:
            await existing.async_save(config)
            items = _collection(hass)
            if items is not None:
                await _sync_title(items, title, url)
            result = REPLACED
        else:
            await collection.async_create_item({
                "url_path": url, "title": title, "icon": CLUSTER_ICON,
                "show_in_sidebar": True, "require_admin": False, "mode": "storage",
            })
            store = dashboards.get(url)
            if store is None:
                return FAILED
            await store.async_save(config)
            result = CREATED
    except Exception:  # noqa: BLE001 - never let the dashboard break the setup
        _LOGGER.warning("Could not create the Cluster dashboard", exc_info=True)
        return FAILED
    _notify(hass, result, entry.title, title, virtual)
    return result


async def async_rebuild(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Rebuild on request — the button or the options of the entry (v1.7.3).

    Says so when a battery of the cluster is still being set up.
    """
    result = await async_cluster_dashboard(hass, entry, replace=True)
    if result == NOT_READY:
        virtual = _is_virtual(entry)
        _notify(hass, result, entry.title, cluster_title(virtual), virtual)
    return result


def cluster_dashboard_exists(hass: HomeAssistant, virtual: bool = False) -> bool:
    dashboards = _dashboards(hass)
    return dashboards is not None and dashboards.get(cluster_url(virtual)) is not None


def old_virtual_dashboard(hass: HomeAssistant) -> bool:
    """Whether `/zendure-cluster` is still the Virtual cluster's of v1.7.1/v1.7.2.

    It is when it carries the Virtual title and no battery cluster exists to
    own it. It is left alone: edits in it are the user's.
    """
    has_cluster = any(
        e.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_CLUSTER for e in hass.config_entries.async_entries(DOMAIN)
    )
    if has_cluster or not cluster_dashboard_exists(hass, virtual=False):
        return False
    collection = _collection(hass)
    items = collection.data.values() if collection is not None else ()
    return any(i.get("url_path") == CLUSTER_URL and i.get("title") == VIRTUAL_TITLE for i in items)


def notify_old_virtual_dashboard(hass: HomeAssistant) -> None:
    """Point to the dashboard a Virtual cluster made under v1.7.2."""
    persistent_notification.async_create(
        hass,
        f"The Virtual cluster has a dashboard of its own since v1.7.3: **{VIRTUAL_TITLE}** at "
        f"`/{VIRTUAL_URL}/{VIRTUAL_URL}`. The one it made before, at `/{CLUSTER_URL}`, is left as it is, "
        "edits included. Remove it under Settings > Dashboards once you no longer need it.",
        title=VIRTUAL_TITLE,
        notification_id=OLD_VIRTUAL_NOTIFICATION,
    )


async def async_remove_cluster_dashboard(hass: HomeAssistant, virtual: bool = False) -> bool:
    """Remove the dashboard of one kind of cluster and its notification (v1.7.1, per kind v1.7.3).

    Called when the last cluster of that kind goes, so Home Assistant is left
    as it was before one was added. Edits made to the dashboard go with it.
    """
    persistent_notification.async_dismiss(hass, _notification(virtual))
    url = cluster_url(virtual)
    try:
        collection = _collection(hass)
        if collection is None:
            return False
        for item_id, item in list(collection.data.items()):
            if item.get("url_path") == url:
                await collection.async_delete_item(item_id)
                return True
    except Exception:  # noqa: BLE001 - removing an entry must never fail on its dashboard
        _LOGGER.warning("Could not remove the Cluster dashboard", exc_info=True)
    return False


def notify_changed(hass: HomeAssistant, entry: ConfigEntry, what: str | None = None) -> None:
    """The group changed: offer the rebuild, never do it silently.

    `what` says what changed; by default the batteries. Since v1.8.0 also a
    Price Control of the cluster's own that came or went: its tab and its plan
    in the chart come with the rebuild.
    """
    virtual = _is_virtual(entry)
    if not cluster_dashboard_exists(hass, virtual):
        return
    title = cluster_title(virtual)
    what = what or f"The batteries of **{entry.title}** changed."
    persistent_notification.async_create(
        hass,
        f"{what} To bring the dashboard **{title}** up to date, "
        f"press *Rebuild dashboard* on {entry.title}; it replaces that dashboard, edits included.",
        title=title,
        notification_id=_notification(virtual),
    )


def _notify(hass: HomeAssistant, result: str, name: str, title: str = CLUSTER_TITLE, virtual: bool = False) -> None:
    if result == NOT_READY:
        message = (f"A battery of {name} is still being set up, so the dashboard **{title}** was not built. "
                   "Press *Rebuild dashboard* again in a moment.")
    elif result == CREATED:
        message = f"The dashboard **{title}** for {name} is in the sidebar: a Cluster tab and a tab per battery."
    elif result == REPLACED:
        message = f"The dashboard **{title}** was rebuilt for {name}. Refresh the browser to see it."
    else:
        return
    persistent_notification.async_create(hass, message, title=title, notification_id=_notification(virtual))
