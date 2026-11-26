"""What a new entry gets when it is added: a battery, a meter, a Price Control.

The flow runs without Home Assistant: the probe, the unique id and the entry
creation are stand-ins, so the test reads what the flow would store.
"""

import asyncio
from types import SimpleNamespace

from custom_components.zendure_restapi import config_flow as F
from custom_components.zendure_restapi.const import (
    CONF_DEVICE_TYPE,
    CONF_MODEL,
    CONF_SN,
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_METER,
    DEVICE_TYPE_PRICE,
    NEW_PRICE_CONTAINS,
    OPT_CREATE_DASHBOARD,
    OPT_DASHBOARD_OFFERED,
    OPT_PRICE_CONTAINS,
    OPT_PRICE_ENTITY,
)

BATTERY = {
    "sn": "TESTSERIAL0001",
    "product": "solarFlow3000MixAC+",
    "properties": {"electricLevel": 50, "outputHomePower": 0},
    "packData": [{"sn": "TESTPACK0001", "socLevel": 50}],
}
METER = {"deviceId": "p1device01", "meterType": 3, "a_aprt_power": 1, "b_aprt_power": 2,
         "c_aprt_power": 3, "total_power": 6}


def _flow(monkeypatch, report=None, entries=()):
    flow = F.ZendureConfigFlow()
    flow.hass = SimpleNamespace()
    flow.context = {}

    async def probe(hass, host, port):
        return F.ProbeResult(report)

    async def unique(identity, **kwargs):
        flow.unique = identity

    monkeypatch.setattr(F, "_probe", probe)
    flow.async_set_unique_id = unique
    flow._abort_if_unique_id_configured = lambda **kwargs: None
    flow._async_current_entries = lambda include_ignore=False: list(entries)
    flow.async_create_entry = lambda **kwargs: kwargs
    return flow


def test_a_battery_gets_its_data_and_the_dashboard_request(monkeypatch):
    flow = _flow(monkeypatch, BATTERY)
    entry = asyncio.run(flow.async_step_zendure({"host": "battery.local"}))
    assert entry["title"] == "Zendure SolarFlow 3000 Mix AC+"
    assert entry["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_BATTERY
    assert entry["data"][CONF_SN] == "TESTSERIAL0001"
    assert entry["options"][OPT_CREATE_DASHBOARD] is True
    assert flow.unique == "TESTSERIAL0001"


def test_a_battery_without_the_dashboard_is_never_offered_one(monkeypatch):
    flow = _flow(monkeypatch, BATTERY)
    entry = asyncio.run(flow.async_step_zendure({"host": "battery.local", OPT_CREATE_DASHBOARD: False}))
    assert OPT_CREATE_DASHBOARD not in entry["options"]
    assert entry["options"][OPT_DASHBOARD_OFFERED] is True


def test_a_meter_is_added_as_a_meter(monkeypatch):
    flow = _flow(monkeypatch, METER)
    entry = asyncio.run(flow.async_step_zendure({"host": "meter.local"}))
    assert entry["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_METER
    assert entry["data"][CONF_MODEL] == "P1 Meter (3-phase)"
    assert OPT_CREATE_DASHBOARD not in entry["options"] and OPT_DASHBOARD_OFFERED not in entry["options"]


def test_a_price_control_expects_the_spot_price_excl_vat(monkeypatch):
    battery = SimpleNamespace(entry_id="b1", title="Zendure SolarFlow 3000 Mix AC+", data={CONF_DEVICE_TYPE: DEVICE_TYPE_BATTERY})
    flow = _flow(monkeypatch, entries=[battery])
    flow._price_targets = lambda: [battery]
    entry = asyncio.run(flow.async_step_price_control({"battery_entry": "b1"}))
    assert entry["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_PRICE
    assert entry["options"][OPT_PRICE_CONTAINS] == NEW_PRICE_CONTAINS == "spot"
    assert entry["options"][OPT_PRICE_ENTITY] == ""
