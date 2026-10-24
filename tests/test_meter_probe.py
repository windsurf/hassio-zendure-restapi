"""A Zendure meter is recognised by its report (issue #7).

The P1 meter carries `meterType`. The Smart Meter 3CT reports no `meterType`,
only the P1 meter's flat fields; a battery reports `sn`, `product`,
`properties` and `packData`.
"""

from custom_components.zendure_restapi.config_flow import ProbeResult
from custom_components.zendure_restapi.const import DEVICE_TYPE_BATTERY, DEVICE_TYPE_METER
from custom_components.zendure_restapi.registry import UNKNOWN_METER, is_meter_report

METER_3CT = {
    "timestamp": 1761206400,
    "messageId": 206,
    "deviceId": "3ctdevice01",
    "a_aprt_power": 120,
    "b_aprt_power": -40,
    "c_aprt_power": 15,
    "total_power": 95,
}

METER_P1 = {**METER_3CT, "deviceId": "p1device01", "meterType": 3}

BATTERY = {
    "timestamp": 1761206400,
    "messageId": 12,
    "sn": "TESTSERIAL0001",
    "product": "solarFlow3000MixAC+",
    "properties": {"electricLevel": 50, "outputHomePower": 0},
    "packData": [{"sn": "TESTPACK0001", "socLevel": 50}],
}


def test_the_3ct_report_is_a_meter():
    result = ProbeResult(METER_3CT)
    assert is_meter_report(METER_3CT)
    assert result.is_meter and result.device_type == DEVICE_TYPE_METER
    assert result.meter_type is None and result.model == UNKNOWN_METER
    assert result.identity == "3ctdevice01"


def test_the_p1_report_is_unchanged():
    result = ProbeResult(METER_P1)
    assert result.is_meter and result.device_type == DEVICE_TYPE_METER
    assert result.model == "P1 Meter (3-phase)"


def test_a_battery_report_is_a_battery():
    result = ProbeResult(BATTERY)
    assert not is_meter_report(BATTERY)
    assert not result.is_meter and result.device_type == DEVICE_TYPE_BATTERY


def test_a_battery_without_product_is_still_a_battery():
    report = {k: v for k, v in BATTERY.items() if k != "product"}
    report["properties"] = {**BATTERY["properties"], "total_power": 0}
    assert not is_meter_report(report)
    assert ProbeResult(report).device_type == DEVICE_TYPE_BATTERY
