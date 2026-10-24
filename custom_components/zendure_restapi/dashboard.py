"""The Zendure RestAPI dashboard: created on request, never overwritten.

The template is dashboard/zendure_restapi.yaml — three views, Zendure
RestAPI, Price Control and History — shipped inside the integration as
dashboard_zendure_restapi.json because HACS installs only this folder. Before
it is saved, the entity ids of the reference device are swapped for those of
the devices actually set up, the power gauges get the device's own AC limit,
and cards for entities that do not exist here are left out. The charts need
apexcharts-card from HACS and appear only when it is installed; without it a
notice in their place says so (v1.6.6).

A few template cards carry a `zendure` key with hints for this module, never
saved: `only_without` keeps a card only when that custom card is missing (the
notices), `plan_optional` keeps a chart whose plan series has dropped out,
`needs` ties a text card to the entity it is about.

Home Assistant has no public call for an integration to add a dashboard. The
dashboards collection is reached through the handler of the websocket command
the frontend itself uses to create one. If that route is not there, the user
gets a notification with the manual route and nothing else changes; setting
up the battery never depends on it.
"""

from __future__ import annotations

import copy
import inspect
import json
import math
import logging
import re
from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.const import MAJOR_VERSION, MINOR_VERSION
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

DASHBOARD_URL = "zendure-restapi"
DASHBOARD_TITLE = "Zendure RestAPI"
DASHBOARD_ICON = "mdi:home-battery"
TEMPLATE = Path(__file__).with_name("dashboard_zendure_restapi.json")
YAML_URL = (
    "https://github.com/windsurf/hassio-zendure-restapi/blob/main/dashboard/zendure_restapi.yaml"
)
NOTIFICATION_ID = "zendure_restapi_dashboard"

# Entity id prefixes of the devices the template was built on.
TEMPLATE_BATTERY = "zendure_solarflow_3000_mix_ac_"
TEMPLATE_P1 = "zendure_p1_meter_3_phase_"
# The Price Control device; its main switch carries the bare device name.
TEMPLATE_PRICE = "price_control_"
TEMPLATE_PRICE_SWITCH = "switch.price_control"
EXTERNAL = "external_p1_meter_"
TEMPLATE_CEILING = 3000
# The section about the Zendure P1 meter itself; dropped when there is none.
P1_SECTION_HEADING = "Zendure P1 Meter"
# Without a Zendure P1 meter, the grid figures come from the external meter.
P1_TO_EXTERNAL = {
    "grid_power_total": "power",
    "energy_imported": "energy_imported",
    "energy_exported": "energy_exported",
}

# The key on a template card with hints for build_config (v1.6.6).
HINTS = "zendure"

# Tiles and gauges show the entity name only, not "<device> <entity>" (v1.6.4).
# Card naming objects arrived in Home Assistant 2025.11; on an older release
# the cards keep the friendly name, as before.
NAME_ENTITY = {"type": "entity"}
NAMED_CARDS = ("tile", "gauge")
CARD_NAMING = (MAJOR_VERSION, MINOR_VERSION) >= (2025, 11)

# The template has one battery pack; a device with more gets a section per pack
# (v1.6.4). Pack entities are `<battery>battery_<n>_<name>`.
PACK_HEADING = "Pack 1"
PACK_TEMPLATE = "pack_1_"

# Solar from outside the device (v1.6.6). The template's PV series read the
# device's own PV; an external sensor replaces them when the device's PV is not
# in use, and joins them otherwise: apexcharts cannot add two series up.
PV_POWER = "pv_input_power"
PV_ENERGY = "pv_energy"
EXTERNAL_PV_NAME = "PV external"
EXTERNAL_PV_DASH = 4
EXTERNAL_PV_OPACITY = 0.25
# The template's grid figures (gauge, tile, charts) carry this placeholder; the
# builder puts the sensor of the meter that steers in its place, with that
# sensor's own history (v1.6.6; v1.6.5 had an entity of its own for it).
GRID_PLACEHOLDER = "grid_power_in_control"
# The tile for solar from outside the battery, under Battery and grid; it goes
# when no such sensor is chosen (v1.6.6).
SOLAR_PLACEHOLDER = "external_solar_power"

CREATED = "created"
REPLACED = "replaced"
EXISTS = "exists"
FAILED = "failed"


def build_config(
    template: dict[str, Any],
    *,
    known: set[str],
    battery: str,
    meter: str | None,
    external: bool,
    ceiling: int,
    price: str | None = None,
    custom_cards: set[str] | None = None,
    card_naming: bool = True,
    external_ids: dict[str, str] | None = None,
    solar_power: str | None = None,
    solar_energy: str | None = None,
    own_pv: bool = False,
    grid: str | None = None,
) -> dict[str, Any]:
    """The template, fitted to the devices of this installation.

    `known` holds the entity ids in the registry, `battery`, `meter` and
    `price` the entity id prefixes of the battery, the Zendure P1 meter and
    the battery's Price Control (None when there is none), `external` whether
    an external meter is set up. `custom_cards` holds the custom card types
    installed as dashboard resources; a custom card that is not installed is
    left out. None keeps them all. `card_naming` gives every tile and gauge
    without a name of its own the entity name only; off for releases before
    2025.11, which do not know the naming object. `external_ids` maps the
    external meter's sensor keys (power, status, ...) to its real entity ids;
    a user's language or a renamed device can give it other ids than the
    template's `sensor.external_p1_meter_*` (v1.6.4). `solar_power` and
    `solar_energy` are sensors of solar from outside the device; `own_pv`
    says whether the device's own PV is in use (v1.6.6). `grid` is the
    sensor of the meter that steers; without one the grid figures go.
    """
    text = json.dumps(template)
    if price and price != TEMPLATE_PRICE:
        # Prefix first: the bare switch id has no trailing underscore and is
        # untouched by it, while the new switch id would be hit again.
        text = text.replace(f".{TEMPLATE_PRICE}", f".{price}")
        text = text.replace(f'"{TEMPLATE_PRICE_SWITCH}"', f'"switch.{price[:-1]}"')
    text = text.replace(f".{TEMPLATE_BATTERY}", f".{battery}")
    if meter:
        text = text.replace(f".{TEMPLATE_P1}", f".{meter}")
    elif external:
        for old, new in P1_TO_EXTERNAL.items():
            text = text.replace(f"sensor.{TEMPLATE_P1}{old}", f"sensor.{EXTERNAL}{new}")
    external_ids = external_ids or {}
    for key, entity_id in external_ids.items():
        text = text.replace(f'"sensor.{EXTERNAL}{key}"', f'"{entity_id}"')
    # Without a meter the placeholder stays: no such entity, so its cards go.
    if grid:
        text = text.replace(f'"sensor.{battery}{GRID_PLACEHOLDER}"', f'"{grid}"')
    if solar_power:
        text = text.replace(f'"sensor.{battery}{SOLAR_PLACEHOLDER}"', f'"{solar_power}"')
    config = json.loads(text)
    _add_packs(config, battery, known)
    _add_solar(config, battery, solar_power, solar_energy, own_pv)

    # The external meter's own section is hidden by its visibility condition
    # and is left alone; only the grid figures swapped in above must exist.
    swapped = (
        {external_ids.get(new, f"sensor.{EXTERNAL}{new}") for new in P1_TO_EXTERNAL.values()}
        if not meter and external
        else set()
    )
    prefixes = [p for p in (battery, TEMPLATE_P1, meter, TEMPLATE_PRICE, price) if p]
    switches = {TEMPLATE_PRICE_SWITCH} | ({f"switch.{price[:-1]}"} if price else set())

    def ours(entity_id: str) -> bool:
        object_id = entity_id.split(".", 1)[-1]
        return (
            entity_id in swapped
            or entity_id in switches
            or any(object_id.startswith(p) for p in prefixes)
        )

    def present(entity_id: str | None) -> bool:
        return not entity_id or not ours(entity_id) or entity_id in known

    for view in config.get("views", []):
        sections = []
        for section in view.get("sections", []):
            headings = {c.get("heading") for c in section.get("cards", [])}
            if not meter and P1_SECTION_HEADING in headings:
                continue
            # A section whose entity cards are all gone goes too, with the
            # text that explains them.
            had = any(_has_entity(c) for c in section.get("cards", []))
            cards = []
            anchored = False
            for card in section.get("cards", []):
                hints = card.pop(HINTS, None) or {}
                if "only_without" in hints and (
                    custom_cards is None or hints["only_without"] in custom_cards
                ):
                    continue
                # A text about an entity, without an entity field of its own:
                # it goes with that entity and keeps its section like a tile.
                if "needs" in hints:
                    if not present(hints["needs"]):
                        continue
                    anchored = True
                if not present(card.get("entity")):
                    continue
                kind = str(card.get("type", ""))
                if kind.startswith("custom:") and custom_cards is not None and kind not in custom_cards:
                    continue
                if "series" in card:
                    # A chart: series of missing entities go; without the
                    # generated series, which is what the chart is for, all of it,
                    # unless the plan is only one series among others (History).
                    kept = [x for x in card["series"] if present(x.get("entity"))]
                    if (
                        not hints.get("plan_optional")
                        and any("data_generator" in x for x in card["series"])
                        and not any("data_generator" in x for x in kept)
                    ):
                        continue
                    card["series"] = kept
                    if not kept:
                        continue
                if "badges" in card:
                    card["badges"] = [b for b in card["badges"] if present(b.get("entity"))]
                if "entities" in card:
                    card["entities"] = [
                        row for row in card["entities"]
                        if present(row if isinstance(row, str) else row.get("entity"))
                    ]
                    if not card["entities"]:
                        continue
                if card_naming and card.get("type") in NAMED_CARDS and "name" not in card:
                    card["name"] = dict(NAME_ENTITY)
                if "yaxis" in card and ceiling != TEMPLATE_CEILING:
                    _scale_power_axis(card, ceiling)
                if card.get("type") == "gauge":
                    if card.get("max") == TEMPLATE_CEILING:
                        card["max"] = ceiling
                    if card.get("min") == -TEMPLATE_CEILING:
                        card["min"] = -ceiling
                cards.append(card)
            if any(c.get("type") != "heading" for c in cards) and (
                not had or anchored or any(_has_entity(c) for c in cards)
            ):
                section["cards"] = cards
                sections.append(section)
        view["sections"] = sections
    return config


def power_labels(every: int) -> str:
    """A y-axis label on every other grid line: "0", "1000", "2000" ...

    The grid follows the data in steps of 500 W; a label on each of them
    crowds a 240 px chart (v1.6.7). apexcharts-card runs an "EVAL:" string
    as JavaScript; the tooltip keeps the card's own format.
    """
    return (
        "EVAL:function (v) { return Math.round(v) %% %d === 0 ? Math.round(v) : \"\"; }" % every
    )


def _scale_power_axis(card: dict[str, Any], ceiling: int) -> None:
    """The chart's power axis for another device than the template's 3000 W.

    Every power axis follows the data, rounded to a whole step, and reaches
    at least a full charge below zero (v1.6.7): "~-3000" is a soft bound,
    so a larger export moves the axis one step down instead of cutting the
    line off. A smaller device gets its own limit and a smaller step, so an
    800 W battery does not draw a flat line.
    """
    step = 500 if ceiling >= 1000 else 200
    for axis in card.get("yaxis") or []:
        if axis.get("id") != "power" or "align_to" not in axis:
            continue
        axis["align_to"] = step
        apex = axis.setdefault("apex_config", {})
        apex["stepSize"] = step
        apex.setdefault("labels", {})["formatter"] = power_labels(2 * step)
        if isinstance(axis.get("min"), str) and axis["min"].startswith("~"):
            axis["min"] = f"~{-math.ceil(ceiling / step) * step}"


def _add_solar(
    config: dict[str, Any],
    battery: str,
    power: str | None,
    energy: str | None,
    own_pv: bool,
) -> None:
    """Point the PV series of every chart at the external solar sensors.

    Without the device's own PV the external sensor takes the series over,
    name and colour included; with it, the external one follows as a series
    of its own: dashed in the power charts, a paler column in the totals.
    """
    swap = {f"sensor.{battery}{PV_POWER}": power, f"sensor.{battery}{PV_ENERGY}": energy}
    for view in config.get("views", []):
        for section in view.get("sections", []):
            for card in section.get("cards", []):
                if "series" not in card:
                    continue
                series = []
                for item in card["series"]:
                    target = swap.get(str(item.get("entity", "")))
                    if not target:
                        series.append(item)
                        continue
                    external = {**copy.deepcopy(item), "entity": target}
                    if not own_pv:
                        series.append(external)
                        continue
                    external["name"] = EXTERNAL_PV_NAME
                    if item.get("type") == "column":
                        external["opacity"] = EXTERNAL_PV_OPACITY
                    else:
                        external["stroke_dash"] = EXTERNAL_PV_DASH
                    series.extend([item, external])
                card["series"] = series


def _pack_numbers(battery: str, known: set[str]) -> list[int]:
    """Pack numbers above 1 that have entities in the registry."""
    pattern = re.compile(rf"^[a-z_]+\.{re.escape(battery)}pack_(\d+)_")
    found = {int(m.group(1)) for e in known if (m := pattern.match(e))}
    return sorted(n for n in found if n > 1)


def _for_pack(card: dict[str, Any], number: int) -> dict[str, Any]:
    text = json.dumps(card).replace(PACK_TEMPLATE, f"pack_{number}_")
    return json.loads(text.replace(f'"{PACK_HEADING}"', f'"Pack {number}"'))


def _add_packs(config: dict[str, Any], battery: str, known: set[str]) -> None:
    """A section per extra battery pack, and its tile next to pack 1's elsewhere.

    The section *Pack 1* is copied once per further pack, right after the
    last one; a pack-1 tile in another section (Power flow) gets its siblings
    right after it. The usual pass that drops missing entities runs after this.
    """
    numbers = _pack_numbers(battery, known)
    if not numbers:
        return
    for view in config.get("views", []):
        sections = []
        for section in view.get("sections", []):
            headings = {c.get("heading") for c in section.get("cards", [])}
            if PACK_HEADING in headings:
                sections.append(section)
                sections.extend(_for_pack(copy.deepcopy(section), n) for n in numbers)
                continue
            cards = []
            for card in section.get("cards", []):
                cards.append(card)
                if PACK_TEMPLATE in str(card.get("entity", "")):
                    cards.extend(_for_pack(copy.deepcopy(card), n) for n in numbers)
            section["cards"] = cards
            sections.append(section)
        view["sections"] = sections


def _has_entity(card: dict[str, Any]) -> bool:
    return bool(
        card.get("entity") or card.get("entities") or card.get("series") or card.get("badges")
    )


# Custom cards the template may use, found by the file name of their resource.
CUSTOM_CARDS = {"apexcharts-card": "custom:apexcharts-card"}


async def _custom_cards(hass: HomeAssistant) -> set[str]:
    """The custom cards from CUSTOM_CARDS that are installed as resources."""
    resources = getattr(hass.data.get("lovelace"), "resources", None)
    if resources is None:
        return set()
    try:
        await resources.async_get_info()  # loads the stored resources
        urls = [str(item.get("url", "")) for item in resources.async_items()]
    except Exception:  # noqa: BLE001 - no resources means no custom cards
        return set()
    return {card for name, card in CUSTOM_CARDS.items() if any(name in url for url in urls)}


def _collection(hass: HomeAssistant) -> Any:
    """The live dashboards collection, through the frontend's create command."""
    handlers = hass.data.get("websocket_api") or {}
    entry = handlers.get("lovelace/dashboards/create")
    if not entry:
        return None
    handler = inspect.unwrap(entry[0])
    owner = getattr(handler, "__self__", None)
    return getattr(owner, "storage_collection", None)


def _dashboards(hass: HomeAssistant) -> dict[str, Any] | None:
    data = hass.data.get("lovelace")
    dashboards = getattr(data, "dashboards", None)
    if dashboards is None and isinstance(data, dict):
        dashboards = data.get("dashboards")
    return dashboards


def _load_template() -> dict[str, Any]:
    return json.loads(TEMPLATE.read_text(encoding="utf-8"))


async def async_create_dashboard(
    hass: HomeAssistant,
    *,
    known: set[str],
    battery: str,
    meter: str | None,
    external: bool,
    ceiling: int,
    price: str | None = None,
    external_ids: dict[str, str] | None = None,
    solar_power: str | None = None,
    solar_energy: str | None = None,
    own_pv: bool = False,
    replace: bool = False,
    grid: str | None = None,
) -> str:
    """Create the dashboard unless one exists at its address.

    With `replace`, an existing one gets a fresh configuration instead: the
    user asked for it in the options, which say that their edits are lost.
    """
    try:
        dashboards = _dashboards(hass)
        if dashboards is None:
            return FAILED
        existing = dashboards.get(DASHBOARD_URL)
        if existing is not None and not replace:
            return EXISTS
        collection = None if existing is not None else _collection(hass)
        if existing is None and collection is None:
            return FAILED
        template = await hass.async_add_executor_job(_load_template)
        config = build_config(
            template, known=known, battery=battery, meter=meter,
            external=external, ceiling=ceiling, price=price,
            custom_cards=await _custom_cards(hass),
            card_naming=CARD_NAMING,
            external_ids=external_ids,
            solar_power=solar_power,
            solar_energy=solar_energy,
            own_pv=own_pv,
            grid=grid,
        )
        if existing is not None:
            await existing.async_save(config)
            return REPLACED
        await collection.async_create_item(
            {
                "url_path": DASHBOARD_URL,
                "title": DASHBOARD_TITLE,
                "icon": DASHBOARD_ICON,
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
            }
        )
        store = dashboards.get(DASHBOARD_URL)
        if store is None:
            return FAILED
        await store.async_save(config)
    except Exception:  # noqa: BLE001 - never let the dashboard break the setup
        _LOGGER.warning("Could not create the Zendure RestAPI dashboard", exc_info=True)
        return FAILED
    return CREATED


def notify(hass: HomeAssistant, result: str, battery: str) -> None:
    """Tell the user what happened, in one notification."""
    if result == CREATED:
        message = (
            f"The dashboard **{DASHBOARD_TITLE}** is in the sidebar. It is an ordinary "
            "dashboard: edit it as you like; the integration will not touch it again."
        )
    elif result == REPLACED:
        message = (
            f"The dashboard **{DASHBOARD_TITLE}** was replaced by a fresh one. "
            "Refresh the browser to see it."
        )
    elif result == EXISTS:
        message = (
            f"A dashboard at `/{DASHBOARD_URL}` already exists and was left as it is. "
            "To rebuild it, tick *Replace the existing dashboard* in the battery's options."
        )
    else:
        message = (
            "The dashboard could not be created automatically. Add it by hand: create a "
            f"dashboard, paste [dashboard/zendure_restapi.yaml]({YAML_URL}) into its raw "
            f"configuration editor and replace `{TEMPLATE_BATTERY}` with `{battery}`."
        )
    persistent_notification.async_create(
        hass, message, title=DASHBOARD_TITLE, notification_id=NOTIFICATION_ID
    )
