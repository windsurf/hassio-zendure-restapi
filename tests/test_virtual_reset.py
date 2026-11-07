"""Reset settings of a Virtual cluster (v1.7.3): every setting back to its default.

The tick is under Configure. It removes the cluster's and the copies' settings from
the options, so each falls back to its default; the Operation mode returns to Follow
source. Everything else in the options stays. When the number of copies changes the
entry sets up again, which the method reports.
"""

from types import SimpleNamespace

import pytest

import custom_components.zendure_restapi.virtual.cluster as SC
from custom_components.zendure_restapi.cluster_entities import CLUSTER_NUMBERS
from custom_components.zendure_restapi.const import (
    OPT_CLONES, OPT_LOAD_FACTOR, OPT_MAX_SHARE_CLONES, OPT_OPERATION_MODE, OPT_SOC_OFFSET_2,
    MODE_FOLLOW_SOURCE, MODE_STANDBY,
)
from custom_components.zendure_restapi.virtual.cluster import VIRTUAL_RESET_KEYS, VirtualCluster, VirtualSettings
from custom_components.zendure_restapi.virtual.entities import TEST_NUMBERS, copy_numbers


def cluster(options: dict) -> VirtualCluster:
    vc = VirtualCluster.__new__(VirtualCluster)
    vc.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=lambda entry, options: setattr(entry, "options", options))
    )
    vc.entry = SimpleNamespace(options=dict(options))
    vc.settings = VirtualSettings(vc.hass, vc.entry)
    vc.signal = "signal"
    return vc


@pytest.fixture(autouse=True)
def no_dispatcher(monkeypatch):
    monkeypatch.setattr(SC, "async_dispatcher_send", lambda *args, **kwargs: None)


def test_every_setting_with_an_entity_is_in_the_reset():
    keys = {key for _, key in CLUSTER_NUMBERS} | {key for _, key in TEST_NUMBERS} | {OPT_OPERATION_MODE}
    for index in (1, 2, 3):
        keys |= {key for _, key, _ in copy_numbers(index)}
    assert keys <= set(VIRTUAL_RESET_KEYS), sorted(keys - set(VIRTUAL_RESET_KEYS))


def test_the_settings_return_to_their_defaults_and_the_rest_stays():
    vc = cluster({
        OPT_OPERATION_MODE: MODE_STANDBY, OPT_LOAD_FACTOR: 2.5, OPT_SOC_OFFSET_2: -5,
        OPT_MAX_SHARE_CLONES[0]: 900, "dashboard_offered": True, "scan_interval": 5,
    })
    assert vc.settings.get(OPT_OPERATION_MODE) == MODE_STANDBY
    assert vc.reset_settings() is False
    assert vc.settings.get(OPT_OPERATION_MODE) == MODE_FOLLOW_SOURCE
    assert vc.settings.get(OPT_LOAD_FACTOR) == 1.0
    assert vc.settings.get(OPT_SOC_OFFSET_2) == -20
    assert vc.settings.get(OPT_MAX_SHARE_CLONES[0]) == 0
    assert vc.entry.options == {"dashboard_offered": True, "scan_interval": 5}


def test_another_number_of_copies_is_reported():
    vc = cluster({OPT_CLONES: 2})
    assert vc.clone_count() == 2
    assert vc.reset_settings() is True
    assert vc.clone_count() == 3


def test_a_reset_of_defaults_changes_nothing():
    vc = cluster({})
    assert vc.reset_settings() is False
    assert vc.entry.options == {}
