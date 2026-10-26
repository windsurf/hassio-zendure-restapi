"""The dashboard template and the version the integration reports (v1.6.9).

The first tab shows the version that runs, read from Controller status; the
status card names the controller entity once.
"""

import json

from custom_components.zendure_restapi import dashboard as D
from custom_components.zendure_restapi.const import INTEGRATION_VERSION
from custom_components.zendure_restapi.sensor import ZendureControllerSensor

CONTROLLER = f"sensor.{D.TEMPLATE_BATTERY}controller_status"


def first_section():
    template = json.loads(D.TEMPLATE.read_text())
    return template["views"][0]["sections"][0]["cards"]


def test_version_tile_follows_the_error_tile():
    cards = first_section()
    entities = [c.get("entity") for c in cards]
    error = entities.index(f"binary_sensor.{D.TEMPLATE_BATTERY}error")
    tile = cards[error + 1]
    assert tile["entity"] == CONTROLLER and tile["state_content"] == "integration_version"


def test_status_card_names_the_controller_once():
    template = json.loads(D.TEMPLATE.read_text())
    cards = [c for v in template["views"] for s in v["sections"] for c in s["cards"]]
    status = [c for c in cards if c.get("type") == "markdown" and "controller_status" in c["content"]]
    assert len(status) == 1 and status[0]["content"].count(CONTROLLER) == 1


def test_controller_status_reports_the_version():
    sensor = ZendureControllerSensor.__new__(ZendureControllerSensor)
    sensor._controller = type("C", (), {"state": type("S", (), {"attributes": {"reason": "idle"}})()})()
    attributes = sensor.extra_state_attributes
    assert attributes["integration_version"] == INTEGRATION_VERSION and attributes["reason"] == "idle"
    assert "integration_version" in ZendureControllerSensor._unrecorded_attributes
