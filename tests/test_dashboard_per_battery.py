"""The battery dashboard's address and title (v1.8.4).

One battery: /zendure-restapi, titled Zendure RestAPI, as before. More
than one: each its own /zendure-restapi-<last four of the serial>, titled with
its name and the last four, as a cluster names its members. A dashboard keeps
the address it was made at; when batteries are added or removed only its title
follows, and only a title the integration gave it, not one the user chose.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import custom_components.zendure_restapi as I
from custom_components.zendure_restapi import dashboard as D
from custom_components.zendure_restapi.const import (
    CONF_DEVICE_TYPE,
    CONF_SN,
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_CLUSTER,
    DEVICE_TYPE_METER,
    OPT_CREATE_DASHBOARD,
    OPT_DASHBOARD_OFFERED,
    OPT_DASHBOARD_URL,
)

NAME = "Zendure SolarFlow 3000 Mix AC+"


def entry(entry_id, title, sn=None, kind=DEVICE_TYPE_BATTERY, options=None):
    data = {CONF_DEVICE_TYPE: kind}
    if sn:
        data[CONF_SN] = sn
    return SimpleNamespace(entry_id=entry_id, title=title, data=data, options=options or {})


def hass_with(entries, dashboards=None):
    return SimpleNamespace(
        config_entries=SimpleNamespace(async_entries=lambda domain: entries),
        data={"lovelace": SimpleNamespace(dashboards=dashboards if dashboards is not None else {})},
    )


class Collection:
    """The dashboards collection: items by id, with url_path and title."""

    def __init__(self, items=None):
        self.data = dict(items or {})
        self.made = []

    async def async_update_item(self, item_id, updates):
        self.data[item_id] = {**self.data[item_id], **updates}
        return self.data[item_id]


def test_the_address_takes_the_last_four_of_the_serial():
    assert D.dashboard_url("SF00XX00000A1B2", "entry") == "zendure-restapi-a1b2"
    assert D.dashboard_url(None, "01JABCDEF9XYZ") == "zendure-restapi-9xyz"


def test_one_battery_has_the_dashboard_of_before():
    first = entry("e1", NAME, "AAAA1164")
    meter = entry("m1", "Zendure P1 Meter", kind=DEVICE_TYPE_METER)
    cluster = entry("c1", "Battery cluster", kind=DEVICE_TYPE_CLUSTER)
    hass = hass_with([first, meter, cluster])
    assert I._dashboard_address(hass, first) == ("zendure-restapi", "Zendure RestAPI")


def test_more_batteries_get_their_own_address_and_the_last_four():
    first = entry("e1", NAME, "AAAA1164")
    second = entry("e2", "Zendure SolarFlow 800 Plus", "BBBB2c3d")
    hass = hass_with([first, second])
    assert I._dashboard_address(hass, first) == ("zendure-restapi-1164", f"{NAME} (…1164)")
    assert I._dashboard_address(hass, second) == (
        "zendure-restapi-2c3d", "Zendure SolarFlow 800 Plus (…2c3d)")


def test_a_dashboard_keeps_its_address_when_a_battery_is_added():
    first = entry("e1", NAME, "AAAA1164", options={OPT_DASHBOARD_URL: "zendure-restapi"})
    second = entry("e2", NAME, "BBBB2c3d")
    hass = hass_with([first, second])
    assert I._dashboard_address(hass, first) == ("zendure-restapi", f"{NAME} (…1164)")
    # Back to one battery: the title of before, the address stays.
    kept = entry("e2", NAME, "BBBB2c3d", options={OPT_DASHBOARD_URL: "zendure-restapi-2c3d"})
    assert I._dashboard_address(hass_with([kept]), kept) == ("zendure-restapi-2c3d", "Zendure RestAPI")


def test_the_titles_follow_the_number_of_batteries(monkeypatch):
    first = entry("e1", NAME, "AAAA1164", options={OPT_DASHBOARD_URL: "zendure-restapi"})
    second = entry("e2", NAME, "BBBB2c3d", options={OPT_DASHBOARD_URL: "zendure-restapi-2c3d"})
    collection = Collection({
        "a": {"url_path": "zendure-restapi", "title": "Zendure RestAPI"},
        "b": {"url_path": "zendure-restapi-2c3d", "title": "Zendure RestAPI"},
        "c": {"url_path": "something-else", "title": "Zendure RestAPI"},
    })
    monkeypatch.setattr(D, "_collection", lambda hass: collection)
    asyncio.run(I._async_dashboard_titles(hass_with([first, second])))
    assert collection.data["a"]["title"] == f"{NAME} (…1164)"
    assert collection.data["b"]["title"] == f"{NAME} (…2c3d)"
    assert collection.data["c"]["title"] == "Zendure RestAPI"


def test_a_title_the_user_chose_is_left_alone(monkeypatch):
    first = entry("e1", NAME, "AAAA1164", options={OPT_DASHBOARD_URL: "zendure-restapi"})
    second = entry("e2", NAME, "BBBB2c3d")
    collection = Collection({"a": {"url_path": "zendure-restapi", "title": "Living room battery"}})
    monkeypatch.setattr(D, "_collection", lambda hass: collection)
    asyncio.run(I._async_dashboard_titles(hass_with([first, second])))
    assert collection.data["a"]["title"] == "Living room battery"
    # The battery's name alone counts as a title the integration gave too.
    collection.data["a"]["title"] = NAME
    asyncio.run(I._async_dashboard_titles(hass_with([first, second])))
    assert collection.data["a"]["title"] == f"{NAME} (…1164)"


def test_the_dashboard_is_made_at_its_address(monkeypatch):
    stores = {}
    made = []

    class Creating:
        async def async_create_item(self, item):
            made.append(item)
            store = MagicMock()

            async def save(config):
                store.saved = config

            store.async_save = save
            stores[item["url_path"]] = store

    monkeypatch.setattr(D, "_collection", lambda hass: Creating())

    async def no_cards(hass):
        return set()

    monkeypatch.setattr(D, "_custom_cards", no_cards)
    hass = hass_with([], stores)

    async def run():
        return await D.async_create_dashboard(
            hass, known=set(), battery=D.TEMPLATE_BATTERY, meter=None, external=False, ceiling=3000,
        )

    hass.async_add_executor_job = lambda fn: _done(fn())
    assert asyncio.run(run()) == D.CREATED
    assert made[0]["url_path"] == "zendure-restapi"
    assert made[0]["title"] == "Zendure RestAPI"


async def _done(value):
    return value


def test_the_notification_names_the_dashboard(monkeypatch):
    sent = []
    monkeypatch.setattr(D.persistent_notification, "async_create",
                        lambda hass, message, title, notification_id: sent.append((message, title, notification_id)))
    D.notify(None, D.CREATED, "x_", url="zendure-restapi-1164", title=f"{NAME} (…1164)")
    message, title, notification_id = sent[0]
    assert "/zendure-restapi-1164" in message
    assert title == f"{NAME} (…1164)"
    assert notification_id == "zendure_restapi_dashboard_zendure-restapi-1164"


def test_wanted_once_unless_asked():
    assert I._dashboard_wanted(entry("e1", "B"))
    assert not I._dashboard_wanted(entry("e1", "B", options={OPT_DASHBOARD_OFFERED: True}))
    assert I._dashboard_wanted(entry("e1", "B", options={OPT_DASHBOARD_OFFERED: True, OPT_CREATE_DASHBOARD: True}))


def test_the_address_is_remembered(monkeypatch):
    first = entry("e1", NAME, "AAAA1164", options={OPT_CREATE_DASHBOARD: True})
    coordinator = MagicMock(spec=I.ZendureCoordinator)
    coordinator.device_id = None
    updated = []
    hass = hass_with([first], {})
    hass.data[I.DOMAIN] = {"e1": coordinator}
    hass.config_entries.async_update_entry = lambda e, options: updated.append(options)
    monkeypatch.setattr(I, "dashboard_args", lambda hass, e: {"battery": "x_"})
    monkeypatch.setattr(I, "notify", lambda *a, **k: None)
    created = []

    async def create(hass, **kwargs):
        created.append(kwargs)
        return D.CREATED

    monkeypatch.setattr(I, "async_create_dashboard", create)
    asyncio.run(I._async_dashboard(hass, first))
    assert created[0]["url"] == "zendure-restapi" and created[0]["title"] == "Zendure RestAPI"
    assert updated[0][OPT_DASHBOARD_URL] == "zendure-restapi"
    assert updated[0][OPT_DASHBOARD_OFFERED] is True
