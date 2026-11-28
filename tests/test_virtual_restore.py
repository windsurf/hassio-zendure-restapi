"""The copies of a Virtual cluster keep their state across a restart (v1.8.2).

Per copy the SoC (one decimal) and the stop memory are saved in
the options of the cluster, where every setting is kept; no file of its own, no
entity. At a start they are restored in every Operation mode but Follow source,
which centres on the real battery as before. The downtime is not simulated: the
copies go on where they were. Written when a copy changed, checked every 5
minutes and when the cluster or Home Assistant stops.
"""

from types import SimpleNamespace

import custom_components.zendure_restapi.virtual.cluster as SC
from custom_components.zendure_restapi.const import (
    MODE_FOLLOW_SOURCE,
    MODE_SMART_MATCHING,
    OPT_CLONES,
    OPT_OPERATION_MODE,
    OPT_SOC_OFFSET_1,
    OPT_SOC_OFFSET_2,
    OPT_SOC_OFFSET_3,
    OPT_VIRTUAL_STATE,
)
from custom_components.zendure_restapi.virtual.cluster import VirtualCluster

def cluster(mode: str = MODE_SMART_MATCHING, clones: int = 3, real: float = 46) -> VirtualCluster:
    vc = VirtualCluster.__new__(VirtualCluster)
    values = {OPT_CLONES: clones, OPT_OPERATION_MODE: mode,
              OPT_SOC_OFFSET_1: 0, OPT_SOC_OFFSET_2: -20, OPT_SOC_OFFSET_3: 15}
    vc.settings = type("S", (), {"get": staticmethod(lambda key: values.get(key))})()
    vc._source_soc = lambda: real
    vc.start_info, vc.start_offsets = {}, []
    vc.clones = []
    vc._running = False
    return vc


def copy(soc: float, stopped=None, mode=None) -> SimpleNamespace:
    return SimpleNamespace(
        device=SimpleNamespace(soc=soc),
        controller=SimpleNamespace(_stopped=stopped, _stopped_mode=mode),
    )


SAVED = {
    "copies": [
        {"soc": 71.3, "stopped": None, "stopped_mode": None},
        {"soc": 100.0, "stopped": "charge", "stopped_mode": "smart_matching"},
        {"soc": 55.4, "stopped": "sideways", "stopped_mode": "smart_matching"},
    ],
}


def test_the_snapshot_keeps_soc_and_stop_memory_per_copy():
    vc = cluster()
    vc.clones = [copy(71.26), copy(99.96, "charge", MODE_SMART_MATCHING)]
    assert vc.snapshot() == {"copies": [
        {"soc": 71.26, "stopped": None, "stopped_mode": None},
        {"soc": 99.96, "stopped": "charge", "stopped_mode": MODE_SMART_MATCHING},
    ]}


def test_the_copies_pick_up_where_they_were():
    vc = cluster()
    restored = vc.restored_copies(SAVED)
    assert [c["soc"] for c in restored] == [71.3, 100.0, 55.4]
    assert restored[1]["stopped"] == "charge" and restored[1]["stopped_mode"] == "smart_matching"
    # An unknown stop is no stop.
    assert restored[2]["stopped"] is None and restored[2]["stopped_mode"] is None
    assert vc.start_info == {"start_soc_real": 46}
    assert vc.start_offsets == [25.3, 54.0, 9.4]


def test_follow_source_centres_on_the_real_battery_as_before():
    vc = cluster(MODE_FOLLOW_SOURCE)
    assert vc.restored_copies(SAVED) is None
    assert vc.start_info == {}


def test_another_number_of_copies_or_a_broken_file_centres_afresh():
    assert cluster(clones=2).restored_copies(SAVED) is None
    assert cluster().restored_copies(None) is None
    assert cluster().restored_copies({"copies": "x"}) is None
    broken = {"copies": [{"soc": "full"}, {"soc": 1}, {"soc": 2}]}
    assert cluster().restored_copies(broken) is None
    assert cluster().restored_copies({"copies": [{}, {}, {}]}) is None


def saving_cluster(options: dict | None = None) -> VirtualCluster:
    vc = cluster()
    vc.entry = SimpleNamespace(options=dict(options or {"clones": 3}))
    vc.hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda entry, options: setattr(entry, "options", options)))
    vc._running = True
    return vc


def test_the_state_is_kept_in_the_options_like_every_setting():
    vc = saving_cluster()
    vc.clones = [copy(50.0)]
    vc.save_state()
    assert vc.entry.options["clones"] == 3
    assert vc.entry.options[OPT_VIRTUAL_STATE]["copies"] == [{"soc": 50.0, "stopped": None, "stopped_mode": None}]
    assert SC.STATE_SAVE_INTERVAL.total_seconds() == 300


def test_written_only_when_a_copy_changed():
    vc = saving_cluster()
    vc.clones = [copy(50.0)]
    vc.save_state()
    first = vc.entry.options
    vc.save_state()
    assert vc.entry.options is first
    vc.clones[0].device.soc = 50.04
    vc.save_state()
    assert vc.entry.options is first  # the same to one decimal
    vc.clones[0].device.soc = 50.06
    vc.save_state()
    assert vc.entry.options is not first


def test_nothing_is_written_before_the_copies_run():
    vc = saving_cluster()
    vc.clones = [copy(50.0)]
    vc._running = False
    vc.save_state()
    assert OPT_VIRTUAL_STATE not in vc.entry.options


def test_a_copy_comes_back_at_the_point_it_showed():
    """4.52 % showed 5 %; kept to one decimal (4.5) and rounded half to even it
    came back as 4 %, under a floor of 5 % (seen on a restart). Since v1.8.8 two
    decimals are kept and the report rounds half up."""
    from custom_components.zendure_restapi.virtual.device import VirtualDevice

    vc = cluster()
    vc.clones = [copy(4.52)]
    kept = vc.snapshot()["copies"][0]["soc"]
    assert kept == 4.52
    report = {"product": "solarFlow3000MixAC+", "properties": {"messageId": 1, "minSoc": 50, "socSet": 1000}}
    for soc, shown in ((kept, 5), (4.5, 5), (4.49, 4), (5.5, 6)):
        device = VirtualDevice(report, "SIMULATED1ABCDEF", soc, 7.973, clock=lambda: 1000.0)
        assert device.report()["properties"]["electricLevel"] == shown
