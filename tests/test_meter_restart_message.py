"""The restart warning of a meter (v1.7.6).

A Zendure P1 meter that restarted was logged with the control state of a
battery: "it reports acMode None, inputLimit None, outputLimit None, smartMode
None", which read as if values had been lost. A meter has no control state:
its warning names the cause and the time it was unreachable only.
"""

import logging
from collections import deque

from custom_components.zendure_restapi import coordinator as CO
from custom_components.zendure_restapi.const import DEVICE_TYPE_BATTERY, DEVICE_TYPE_METER


def device(kind):
    c = CO.ZendureCoordinator.__new__(CO.ZendureCoordinator)
    c.restart_seq = 0
    c.restarts = deque(maxlen=10)
    c.title = "Zendure P1 Meter (3-phase)" if kind == DEVICE_TYPE_METER else "Zendure SolarFlow"
    c.device_id = "TEST00000000A1234"
    c.device_type = kind
    return c


def test_a_meter_restart_names_no_battery_fields(caplog):
    c = device(DEVICE_TYPE_METER)
    with caplog.at_level(logging.WARNING):
        c.note_restart("messageId", {"messageId": 900}, {"messageId": 3}, unreachable=4)
    (record,) = caplog.records
    assert record.levelno == logging.WARNING
    assert record.getMessage() == "Zendure device Zendure P1 Meter (3-phase) restarted (messageId, unreachable for 4 s)"
    assert c.restart_seq == 1 and len(c.restarts) == 1


def test_a_battery_restart_still_lists_its_control_state(caplog):
    c = device(DEVICE_TYPE_BATTERY)
    with caplog.at_level(logging.WARNING):
        c.note_restart("messageId", {}, {"acMode": 1, "inputLimit": 0, "outputLimit": 0, "smartMode": 0}, unreachable=12)
    assert "it reports acMode 1, inputLimit 0, outputLimit 0, smartMode 0" in caplog.records[0].getMessage()
