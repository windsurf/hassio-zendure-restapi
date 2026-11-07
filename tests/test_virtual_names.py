"""Names of the Virtual cluster's entities (v1.7.3).

Copies are Virtual copy n whatever the model (was Virtual Mix n or Virtual
battery n). The settings of a copy sit on the copy's device and drop "clone n",
Response offset is Extra response time, Clones is Copies, the Load
factor of the meter is an attribute, not a sensor, and the Virtual P1
reads Grid power and Real grid power. The unique ids stay those of
v1.7.2, and an existing installation has its entity ids moved once.
"""

from types import SimpleNamespace

import homeassistant.helpers.entity_registry as er

from custom_components.zendure_restapi import cluster_entities as CE
from custom_components.zendure_restapi.const import OPT_MAX_SHARE_CLONES
from custom_components.zendure_restapi.virtual import entities as E


def test_copy_settings_keep_their_keys_and_lose_the_clone_number():
    for n in (1, 2, 3):
        numbers = E.copy_numbers(n)
        assert [d.key for d, _k, _t in numbers] == [OPT_MAX_SHARE_CLONES[n - 1], f"soc_offset_{n}",
                                                    f"response_offset_{n}"]
        assert [d.name for d, _k, _t in numbers] == ["Max share", "SoC offset", "Extra response time"]
        assert [d.translation_key for d, _k, _t in numbers] == ["copy_max_share", "copy_soc_offset",
                                                                "copy_response_offset"]


def test_group_and_meter_names():
    assert [d.name for d, _k in E.TEST_NUMBERS] == ["Copies", "Load factor"]
    assert [d.name for d, _f in E.VIRTUAL_P1_DESCRIPTIONS] == ["Grid power", "House load", "Real grid power"]
    assert all(d.key != "virtual_p1_load_factor" for d, _f in E.VIRTUAL_P1_DESCRIPTIONS)


class Registry:
    def __init__(self, ids: dict) -> None:
        self.ids = dict(ids)  # (platform, unique_id) -> entity_id
        self.removed: list = []

    def async_get_entity_id(self, platform, _domain, unique_id):
        return self.ids.get((platform, unique_id))

    def async_is_registered(self, entity_id):
        return entity_id in self.ids.values()

    def async_update_entity(self, entity_id, new_entity_id):
        key = next(k for k, v in self.ids.items() if v == entity_id)
        self.ids[key] = new_entity_id

    @property
    def entities(self):
        return {v: SimpleNamespace(platform="zendure_restapi", unique_id=k[1], entity_id=v) for k, v in self.ids.items()}

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.ids = {k: v for k, v in self.ids.items() if v != entity_id}


def setup(monkeypatch, ids: dict):
    registry = Registry(ids)
    monkeypatch.setattr(er, "async_get", lambda _hass: registry)
    entry = SimpleNamespace(entry_id="E", data={"device_name": "Virtual cluster"})
    clones = [SimpleNamespace(index=n, name=f"Virtual copy {n}") for n in (1, 2)]
    hass = SimpleNamespace(states=SimpleNamespace(get=lambda _e: None))
    return registry, entry, clones, hass


def test_old_ids_are_moved_and_the_load_factor_sensor_goes(monkeypatch):
    registry, entry, clones, hass = setup(monkeypatch, {
        ("number", "E_clones"): "number.virtual_cluster_clones",
        ("number", "E_max_share_clone_2"): "number.virtual_cluster_max_share_clone_2",
        ("number", "E_soc_offset_2"): "number.virtual_cluster_soc_offset_clone_2",
        ("number", "E_response_offset_1"): "number.virtual_cluster_response_offset_clone_1",
        ("sensor", "E_virtual_p1_power"): "sensor.virtual_p1_meter_power",
        ("sensor", "E_virtual_p1_real_grid"): "sensor.virtual_p1_meter_real_grid",
        ("sensor", "E_virtual_p1_load_factor"): "sensor.virtual_p1_meter_load_factor",
    })
    assert E.migrate_entity_ids(hass, entry, clones) == 6
    assert registry.ids == {
        ("number", "E_clones"): "number.virtual_cluster_copies",
        ("number", "E_max_share_clone_2"): "number.virtual_copy_2_max_share",
        ("number", "E_soc_offset_2"): "number.virtual_copy_2_soc_offset",
        ("number", "E_response_offset_1"): "number.virtual_copy_1_extra_response_time",
        ("sensor", "E_virtual_p1_power"): "sensor.virtual_p1_meter_grid_power",
        ("sensor", "E_virtual_p1_real_grid"): "sensor.virtual_p1_meter_real_grid_power",
    }
    assert registry.removed == ["sensor.virtual_p1_meter_load_factor"]
    # Once: a second start moves nothing.
    assert E.migrate_entity_ids(hass, entry, clones) == 0


def test_an_id_renamed_by_hand_or_a_taken_target_stays(monkeypatch):
    registry, entry, clones, hass = setup(monkeypatch, {
        ("number", "E_clones"): "number.my_copies",
        ("number", "E_soc_offset_2"): "number.virtual_cluster_soc_offset_clone_2",
        ("number", "E_other"): "number.virtual_copy_2_soc_offset",
    })
    assert E.migrate_entity_ids(hass, entry, clones) == 0
    assert registry.ids[("number", "E_clones")] == "number.my_copies"
    assert registry.ids[("number", "E_soc_offset_2")] == "number.virtual_cluster_soc_offset_clone_2"


def test_copies_get_a_fixed_name_whatever_the_model(monkeypatch):
    registry, entry, clones, hass = setup(monkeypatch, {
        ("sensor", "SIMULATED1E_electricLevel"): "sensor.virtual_mix_1_state_of_charge",
        ("sensor", "SIMULATED2E_controller_status"): "sensor.virtual_battery_2_controller_status",
        ("number", "E_max_share_clone_1"): "number.virtual_mix_1_max_share",
        ("sensor", "SIMULATED1E_battery_power"): "sensor.my_power",
    })
    assert E.migrate_entity_ids(hass, entry, clones) == 3
    assert registry.ids[("sensor", "SIMULATED1E_electricLevel")] == "sensor.virtual_copy_1_state_of_charge"
    assert registry.ids[("sensor", "SIMULATED2E_controller_status")] == "sensor.virtual_copy_2_controller_status"
    assert registry.ids[("number", "E_max_share_clone_1")] == "number.virtual_copy_1_max_share"
    assert registry.ids[("sensor", "SIMULATED1E_battery_power")] == "sensor.my_power"


def test_settings_of_a_removed_copy_go_with_it():
    entry = SimpleNamespace(entry_id="E")
    assert E.copy_setting_unique_ids(entry, 3) == ("E_max_share_clone_3", "E_soc_offset_3", "E_response_offset_3")


def test_cluster_power_and_soc_are_named_as_on_a_battery():
    assert (CE.POWER_DESCRIPTION.name, CE.SOC_DESCRIPTION.name) == ("Battery power", "State of charge")
    assert (CE.POWER_DESCRIPTION.key, CE.SOC_DESCRIPTION.key) == ("group_power", "group_state_of_charge")


def test_group_ids_are_moved_for_both_kinds(monkeypatch):
    registry, entry, _clones, hass = setup(monkeypatch, {
        ("sensor", "E_group_power"): "sensor.battery_cluster_group_power",
        ("sensor", "E_group_state_of_charge"): "sensor.my_soc",
    })
    entry.title = "Battery cluster"
    assert CE.migrate_group_ids(hass, entry) == 1
    assert registry.ids[("sensor", "E_group_power")] == "sensor.battery_cluster_battery_power"
    assert registry.ids[("sensor", "E_group_state_of_charge")] == "sensor.my_soc"
