"""What a Smart Meter 3CT entry keeps from when it was set up as a battery (v1.6.10).

Before v1.6.7 the 3CT was added as a battery: a controller, an operation mode and
the settings of one. Once the entry is a meter those entities read "unavailable".
They are removed; the meter's own entities, live or disabled on purpose, stay.
"""

from types import SimpleNamespace

from custom_components.zendure_restapi import battery_leftovers


def entity(name: str, key: str, disabled_by: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(entity_id=name, unique_id=f"3ct01_{key}", disabled_by=disabled_by)


METER_LIVE = [
    entity("sensor.zendure_meter_grid_power_total", "total_power"),
    entity("sensor.zendure_meter_energy_imported", "energy_imported"),
]
METER_OFF = [
    entity("sensor.zendure_meter_report_timestamp", "timestamp", "integration"),
    entity("sensor.zendure_meter_message_id", "messageId", "integration"),
]
BATTERY_ONLY = [
    entity("select.zendure_meter_operation_mode", "operation_mode"),
    entity("number.zendure_meter_charge_buffer", "charge_buffer"),
    entity("sensor.zendure_meter_controller_status", "controller_status"),
    entity("switch.zendure_meter_soc_protection", "soc_protection"),
]
LIVE = {e.entity_id for e in METER_LIVE}


def test_the_battery_part_is_removed_and_the_meter_stays():
    gone = battery_leftovers(METER_LIVE + METER_OFF + BATTERY_ONLY, LIVE)
    assert sorted(gone) == sorted(e.entity_id for e in BATTERY_ONLY)


def test_a_meter_that_was_never_a_battery_loses_nothing():
    """No operation mode select: nothing marks it as a former battery, even with an entity not live."""
    quiet = METER_LIVE + METER_OFF + [entity("sensor.zendure_meter_voltage", "voltage")]
    assert battery_leftovers(quiet, LIVE) == []


def test_a_live_entity_is_never_removed():
    everything_live = {e.entity_id for e in METER_LIVE + BATTERY_ONLY}
    assert battery_leftovers(METER_LIVE + BATTERY_ONLY, everything_live) == []


def test_a_disabled_entity_of_the_battery_part_stays_too():
    off = entity("number.zendure_meter_trim_strength", "trim_factor", "user")
    gone = battery_leftovers(METER_LIVE + BATTERY_ONLY + [off], LIVE)
    assert off.entity_id not in gone
