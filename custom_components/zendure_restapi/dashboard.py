"""The Zendure RestAPI dashboard: created on request, never overwritten.

The template is dashboard/zendure_restapi.yaml — two views, Zendure RestAPI
and Price Control — shipped inside the integration as
dashboard_zendure_restapi.json because HACS installs only this folder. Before
it is saved, the entity ids of the reference device are swapped for those of
the devices actually set up, the power gauges get the device's own AC limit,
and cards for entities that do not exist here are left out. The day plan
chart needs apexcharts-card from HACS and appears only when it is installed.

Home Assistant has no public call for an integration to add a dashboard. The
dashboards collection is reached through the handler of the websocket command
the frontend itself uses to create one. If that route is not there, the user
gets a notification with the manual route and nothing else changes; setting
up the battery never depends on it.
"""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
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

CREATED = "created"
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
) -> dict[str, Any]:
    """The template, fitted to the devices of this installation.

    `known` holds the entity ids in the registry, `battery`, `meter` and
    `price` the entity id prefixes of the battery, the Zendure P1 meter and
    the battery's Price Control (None when there is none), `external` whether
    an external meter is set up. `custom_cards` holds the custom card types
    installed as dashboard resources; a custom card that is not installed is
    left out. None keeps them all.
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
    config = json.loads(text)

    # The external meter's own section is hidden by its visibility condition
    # and is left alone; only the grid figures swapped in above must exist.
    swapped = (
        {f"sensor.{EXTERNAL}{new}" for new in P1_TO_EXTERNAL.values()}
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
            for card in section.get("cards", []):
                if not present(card.get("entity")):
                    continue
                kind = str(card.get("type", ""))
                if kind.startswith("custom:") and custom_cards is not None and kind not in custom_cards:
                    continue
                if "series" in card:
                    # A chart: series of missing entities go; without the
                    # generated series, which is what the chart is for, all of it.
                    kept = [x for x in card["series"] if present(x.get("entity"))]
                    if any("data_generator" in x for x in card["series"]) and not any(
                        "data_generator" in x for x in kept
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
                if card.get("type") == "gauge":
                    if card.get("max") == TEMPLATE_CEILING:
                        card["max"] = ceiling
                    if card.get("min") == -TEMPLATE_CEILING:
                        card["min"] = -ceiling
                cards.append(card)
            if any(c.get("type") != "heading" for c in cards) and (
                not had or any(_has_entity(c) for c in cards)
            ):
                section["cards"] = cards
                sections.append(section)
        view["sections"] = sections
    return config


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
) -> str:
    """Create the dashboard unless one exists at its address."""
    try:
        dashboards = _dashboards(hass)
        if dashboards is None:
            return FAILED
        if DASHBOARD_URL in dashboards:
            return EXISTS
        collection = _collection(hass)
        if collection is None:
            return FAILED
        template = await hass.async_add_executor_job(_load_template)
        config = build_config(
            template, known=known, battery=battery, meter=meter,
            external=external, ceiling=ceiling, price=price,
            custom_cards=await _custom_cards(hass),
        )
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
    elif result == EXISTS:
        message = (
            f"A dashboard at `/{DASHBOARD_URL}` already exists and was left as it is. "
            "To rebuild it, delete it and tick the option again."
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
