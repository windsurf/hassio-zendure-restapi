"""The cluster dashboard: a Cluster tab and a tab per battery (v1.7.1).

One dashboard, `/zendure-cluster`, for the battery cluster or the Virtual
cluster (the one it is made from names it): *Cluster* or *Virtual Cluster*.

* Cluster — the group's mode and status, who is primary, a row per battery
  with role, direction and limit, power and state of charge per battery, a
  24 h chart of the grid against the group and every battery, and the
  group's settings. A Virtual cluster adds its Virtual P1 meter, and its
  settings name each offset after the virtual battery it belongs to;
* a battery cluster: one tab per member, built from the first page of the
  battery dashboard (dashboard.py, the same template and the same rules for
  missing entities), headed with the member's name;
* a Virtual cluster: one tab per virtual battery (Virtual Mix 1, 2, 3). The
  real battery is no member and keeps its own dashboard; it gets no tab here
  and shows on the Cluster tab only, for comparison.

Tabs carry their title, not an icon, so they can be told apart. Every virtual
battery is called by its device name (Virtual Mix n) throughout.

Made once, when the group is set up and no cluster dashboard exists yet. After
that only on request, with the Rebuild dashboard button: a change in the group
brings a notification, never a silent overwrite.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .const import CONF_BATTERY_ENTRY, CONF_DEVICE_TYPE, DEVICE_TYPE_VIRTUAL, DOMAIN, SUBENTRY_BATTERY
from .dashboard import (
    CARD_NAMING,
    CREATED,
    EXISTS,
    FAILED,
    REPLACED,
    _collection,
    _custom_cards,
    _dashboards,
    _load_template,
    build_config,
)

_LOGGER = logging.getLogger(__name__)

CLUSTER_URL = "zendure-cluster"
CLUSTER_TITLE = "Cluster"
VIRTUAL_TITLE = "Virtual Cluster"
# The path keeps the prefix: Home Assistant wants a hyphen in it, and it stays
# clear of dashboards of other integrations.
CLUSTER_ICON = "mdi:battery-sync"
CLUSTER_NOTIFICATION = "zendure_restapi_cluster_dashboard"
# The real battery next to a Virtual cluster, on the Cluster tab.
REAL = "Real"
# The template heading of a battery tab; replaced by the battery's own name.
TEMPLATE_HEADING = "Zendure Home Battery"
CLONE_ICON = "mdi:home-battery-outline"


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


def cluster_view(
    group: dict[str, Any],
    batteries: list[dict[str, str | None]],
    grids: list[dict[str, str | None]],
    virtual: bool,
    clones: list[tuple[int, str]] | None = None,
) -> dict[str, Any]:
    """The Cluster tab. `group` holds the entity ids of the group's own
    entities, `batteries` per battery name, power and soc, `grids` the grid
    sensors for the chart with their name, `clones` index and name of each
    virtual battery. Order: Status, Virtual P1 meter, Distribution, Settings,
    Last 24 hours."""
    clones = clones or []
    names = dict(clones)
    status = group.get("status")
    table = (
        "| Battery | Role | Direction | Limit |\n|---|---|---|---:|\n"
        "{% set b = state_attr('" + str(status) + "', 'batteries') or {} %}"
        "{% for name, v in b.items() %}| {{ name }} | {{ v.role }} | {{ v.direction }} | {{ v.limit }} W |\n{% endfor %}"
    )
    # `grid` is the reading the last distribution was decided on, once per
    # Redistribute every; the P1 sensors follow every telegram.
    summary = (
        "{% set s = '" + str(status) + "' %}"
        "**Status** {{ states(s) }} – {{ state_attr(s, 'reason') }}\n\n"
        "**Primary** {{ state_attr(s, 'primary') or '–' }} | **Demand** {{ state_attr(s, 'demand') }} W"
        " | **Grid at last distribution** {{ state_attr(s, 'grid') }} W"
    )
    if virtual and group.get("virtual_grid"):
        summary += (
            "\n\n**Virtual P1** {{ states('" + group["virtual_grid"] + "') }} W"
            " | **House** {{ states('" + str(group.get("house_load")) + "') }} W"
            " | **Real P1** {{ states('" + str(group.get("real_grid")) + "') }} W"
        )

    def offset_tile(key: str, index: int, label: str) -> dict[str, Any] | None:
        name = names.get(index)
        return _tile(group.get(key), name=f"{name} {label}") if name else None

    sections = [
        _section("Status", "mdi:battery-sync", [
            _tile(group.get("mode"), features_position="bottom", features=[{"type": "select-options"}],
                  grid_options={"columns": 12, "rows": 2}),
            _markdown(summary),
            _tile(group.get("status")),
            _tile(group.get("power")),
            _tile(group.get("soc")),
        ]),
        _section("Virtual P1 meter", "mdi:meter-electric-outline", [
            _tile(group.get("virtual_grid")),
            _tile(group.get("house_load")),
            _tile(group.get("real_grid")),
            _tile(group.get("load_factor")),
        ]) if virtual else None,
        _section("Distribution", "mdi:call-split", [
            _markdown(table),
            *[card for b in batteries for card in (
                _tile(b.get("power"), name=f"{b['name']} power"),
                _tile(b.get("soc"), name=f"{b['name']} SoC"),
            )],
        ]),
        _section("Settings", "mdi:tune-variant", [
            *(_tile(group.get(k)) for k in ("clones", "load_factor")),
            offset_tile("soc_offset_2", 2, "SoC offset"),
            offset_tile("soc_offset_3", 3, "SoC offset"),
            offset_tile("response_offset_2", 2, "extra response time"),
            offset_tile("response_offset_3", 3, "extra response time"),
            *(_tile(group.get(k)) for k in (
                "charge_buffer", "discharge_buffer", "start_charge_at", "start_discharge_at",
                "concentrate_below", "redistribute_every",
            )),
            _tile(group.get("reset")),
            _tile(group.get("rebuild")),
        ]),
        {
            "type": "grid",
            "column_span": 4,
            "cards": [
                {"type": "heading", "heading": "Last 24 hours", "heading_style": "title", "icon": "mdi:chart-line"},
                {
                    "type": "history-graph",
                    "hours_to_show": 24,
                    "grid_options": {"columns": "full", "rows": 6},
                    "entities": [
                        *({"entity": g["entity"], **({"name": g["name"]} if g.get("name") else {})} for g in grids),
                        *({"entity": e, **({"name": "Group power"} if virtual else {})}
                          for e in (group.get("power"),) if e),
                        *({"entity": b["power"], "name": b["name"]} for b in batteries if b.get("power")),
                    ],
                },
                {
                    "type": "history-graph",
                    "hours_to_show": 24,
                    "grid_options": {"columns": "full", "rows": 4},
                    "entities": [{"entity": b["soc"], "name": b["name"]} for b in batteries if b.get("soc")],
                },
            ],
        },
    ]
    return {
        "title": "Cluster",
        "path": "cluster",
        "type": "sections",
        "max_columns": 4,
        "sections": [s for s in sections if s],
    }


def battery_view(template: dict[str, Any], args: dict[str, Any], title: str, path: str,
                 custom_cards: set[str] | None) -> dict[str, Any] | None:
    """The first page of the battery dashboard for one member of a battery
    cluster, headed with its name; the tab shows the title, not an icon."""
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
    return view


def clone_view(prefix: str, index: int, name: str, group: dict[str, Any], known: set[str]) -> dict[str, Any]:
    """The tab of one virtual battery: what its controller does on the model
    of the device, headed with its name and, from the second on, its offsets;
    the meter it steers on, its state of charge and its limits as gauges."""
    status = f"sensor.{prefix}controller_status"

    def own(key: str) -> str | None:
        entity = f"sensor.{prefix}{key}"
        return entity if entity in known else None

    soc, delay = group.get(f"soc_offset_{index}"), group.get(f"response_offset_{index}")
    first = index == 1 or not (soc and delay)
    card = (
        "{% set c = '" + status + "' %}\n**Status** {{ states(c) }} - {{ state_attr(c, 'reason') }}\n\n"
        "**Direction** {{ state_attr(c, 'direction') }} | **Target** {{ state_attr(c, 'target_power') }} W\n\n"
        "**Virtual P1** {{ state_attr(c, 'meter_power') }} W | **Battery** {{ state_attr(c, 'battery_power') }} W\n\n"
        "**Trim writes** {{ state_attr(c, 'trim_writes') }}\n"
    )
    gauge = {"needle": True, "grid_options": {"columns": 6, "rows": "auto"}}
    sections = [
        _section(f"{name} (simulated)", CLONE_ICON, [
            _tile(own("state_of_charge"), icon="mdi:battery-charging"),
            _tile(own("battery_power"), icon="mdi:flash"),
            _tile(status if status in known else None, icon="mdi:cog-sync-outline"),
            None if first else _tile(soc, name="SoC offset"),
            None if first else _tile(delay, name="Extra response time"),
            _tile(own("response_time")),
        ], span=4),
        _section("Operation mode", "mdi:state-machine", [_markdown(card)] if status in known else []),
        _section("Overview", "mdi:gauge", [
            *({"type": "gauge", "entity": e, "name": n, "min": lo, "max": hi, **gauge,
               **({"severity": sev} if sev else {})}
              for e, n, lo, hi, sev in (
                  (group.get("virtual_grid"), "Virtual P1", -3000, 3000, {"green": -1500, "yellow": 0, "red": 1500}),
                  (own("state_of_charge"), "State of charge", 0, 100, {"green": 70, "yellow": 30, "red": 0}),
                  (own("charge_limit"), "Charge limit", 0, 3000, None),
                  (own("discharge_limit"), "Discharge limit", 0, 3000, None),
              ) if e),
        ]),
    ]
    return {
        "title": name,
        "path": slugify(name) or f"virtual_{index}",
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
        "virtual_grid": _entity(registry, "sensor", f"{eid}_virtual_p1_power"),
        "house_load": _entity(registry, "sensor", f"{eid}_virtual_p1_house_load"),
        "real_grid": _entity(registry, "sensor", f"{eid}_virtual_p1_real_grid"),
        "reset": _entity(registry, "button", f"{eid}_reset"),
        "rebuild": _entity(registry, "button", f"{eid}_rebuild_dashboard"),
    }
    for key in ("clones", "load_factor", "soc_offset_2", "soc_offset_3", "response_offset_2", "response_offset_3", "charge_buffer", "discharge_buffer",
                "start_charge_at", "start_discharge_at", "concentrate_below", "redistribute_every"):
        group[key] = _entity(registry, "number", f"{eid}_{key}")

    views: list[dict[str, Any]] = []
    batteries: list[dict[str, str | None]] = []
    grids: list[dict[str, str | None]] = []
    clones: list[tuple[int, str]] = []
    store = hass.data.get(DOMAIN, {})

    def add_battery(battery_entry: ConfigEntry, name: str, grid_name: str | None) -> dict[str, Any]:
        """Power and SoC of a real battery for the Cluster tab, and its grid sensor for the chart."""
        args = dashboard_args(hass, battery_entry)
        prefix = args["battery"]
        batteries.append({
            "name": name,
            "power": f"sensor.{prefix}battery_power" if f"sensor.{prefix}battery_power" in known else None,
            "soc": f"sensor.{prefix}state_of_charge" if f"sensor.{prefix}state_of_charge" in known else None,
        })
        if args.get("grid") and all(g["entity"] != args["grid"] for g in grids):
            grids.append({"entity": args["grid"], "name": grid_name})
        return args

    if virtual:
        vc = store.get(eid)
        source = hass.config_entries.async_get_entry(getattr(vc, "source_id", "") or "")
        if group["virtual_grid"]:
            grids.append({"entity": group["virtual_grid"], "name": "Virtual P1"})
        if source is not None:
            # The real battery is shown for comparison, not as a member, and
            # keeps its own dashboard: no tab here.
            add_battery(source, REAL, "Real P1")
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
                                custom_cards)
            if view:
                views.append(view)

    return {"title": cluster_title(virtual), "views": [cluster_view(group, batteries, grids, virtual, clones), *views]}


async def _sync_title(collection: Any, title: str) -> None:
    """Give the sidebar the title of this kind of cluster on a rebuild. A title
    the user chose stays: only one of the two standard titles is replaced."""
    for item_id, item in list(collection.data.items()):
        if item.get("url_path") != CLUSTER_URL:
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
        existing = dashboards.get(CLUSTER_URL)
        if existing is not None and not replace:
            return EXISTS
        collection = None if existing is not None else _collection(hass)
        if existing is None and collection is None:
            return FAILED
        title = cluster_title(_is_virtual(entry))
        template = await hass.async_add_executor_job(_load_template)
        config = cluster_config(hass, entry, template, await _custom_cards(hass))
        if existing is not None:
            await existing.async_save(config)
            items = _collection(hass)
            if items is not None:
                await _sync_title(items, title)
            result = REPLACED
        else:
            await collection.async_create_item({
                "url_path": CLUSTER_URL, "title": title, "icon": CLUSTER_ICON,
                "show_in_sidebar": True, "require_admin": False, "mode": "storage",
            })
            store = dashboards.get(CLUSTER_URL)
            if store is None:
                return FAILED
            await store.async_save(config)
            result = CREATED
    except Exception:  # noqa: BLE001 - never let the dashboard break the setup
        _LOGGER.warning("Could not create the Cluster dashboard", exc_info=True)
        return FAILED
    _notify(hass, result, entry.title, title)
    return result


def cluster_dashboard_exists(hass: HomeAssistant) -> bool:
    dashboards = _dashboards(hass)
    return dashboards is not None and dashboards.get(CLUSTER_URL) is not None


async def async_remove_cluster_dashboard(hass: HomeAssistant) -> bool:
    """Remove the Cluster dashboard and its notification (v1.7.1).

    Called when the last cluster or Virtual cluster goes, so Home Assistant is
    left as it was before one was added. Edits made to the dashboard go with it.
    """
    persistent_notification.async_dismiss(hass, CLUSTER_NOTIFICATION)
    try:
        collection = _collection(hass)
        if collection is None:
            return False
        for item_id, item in list(collection.data.items()):
            if item.get("url_path") == CLUSTER_URL:
                await collection.async_delete_item(item_id)
                return True
    except Exception:  # noqa: BLE001 - removing an entry must never fail on its dashboard
        _LOGGER.warning("Could not remove the Cluster dashboard", exc_info=True)
    return False


def notify_changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """The group changed: offer the rebuild, never do it silently."""
    if not cluster_dashboard_exists(hass):
        return
    title = cluster_title(_is_virtual(entry))
    persistent_notification.async_create(
        hass,
        f"The batteries of **{entry.title}** changed. To bring the dashboard **{title}** up to date, "
        f"press *Rebuild dashboard* on {entry.title}; it replaces that dashboard, edits included.",
        title=title,
        notification_id=CLUSTER_NOTIFICATION,
    )


def _notify(hass: HomeAssistant, result: str, name: str, title: str = CLUSTER_TITLE) -> None:
    if result == CREATED:
        message = f"The dashboard **{title}** for {name} is in the sidebar: a Cluster tab and a tab per battery."
    elif result == REPLACED:
        message = f"The dashboard **{title}** was rebuilt for {name}. Refresh the browser to see it."
    else:
        return
    persistent_notification.async_create(hass, message, title=title, notification_id=CLUSTER_NOTIFICATION)
