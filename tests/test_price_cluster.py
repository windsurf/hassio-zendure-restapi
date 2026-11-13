"""Price Control for a battery cluster or a Virtual cluster (v1.8.0).

The day plan plans for the group: usable and stored energy of every battery
taking part added up, and the power the cluster may ask of each added up.
Price Control writes the Operation mode of the cluster, never pauses for it,
and has no battery controller or measurements of its own.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.zendure_restapi import pricecontrol as P
from custom_components.zendure_restapi.cluster.contract import Reading
from custom_components.zendure_restapi.const import (
    MODE_QUICK_CHARGE,
    MODE_QUICK_DISCHARGE,
    MODE_SMART_MATCHING,
    OPT_CHARGE_EFFICIENCY,
    OPT_DISCHARGE_EFFICIENCY,
    OPT_HOUSE_LOAD,
    OPT_MAX_CHARGE_POWER,
    OPT_MAX_DISCHARGE_POWER,
    OPT_OPERATION_MODE,
    OPT_PRICE_BETWEEN_MODE,
    OPT_PRICE_CHEAP_MODE,
    OPT_PRICE_EXPENSIVE_MODE,
    PRICE_STATUS_CHEAP,
)
from custom_components.zendure_restapi.pricecontrol import PriceControl, group_state

DOMAIN = "zendure_restapi"


class Settings:
    """Options in a dict, with the getters of ZendureSettings."""

    def __init__(self, **values):
        self.values = dict(values)
        self.written = []

    def get(self, key, default=None):
        return self.values.get(key, default)

    def get_int(self, key):
        return int(self.values.get(key, 0))

    def get_float(self, key):
        return float(self.values.get(key, 0))

    def get_bool(self, key):
        return bool(self.values.get(key))

    def set(self, key, value):
        self.values[key] = value
        self.written.append((key, value))


def mix_data(soc: float, floor: int = 10, ceiling: int = 100) -> dict:
    """A 3000 Mix AC+ as it reports: one 8 kWh internal pack."""
    return {
        "electricLevel": soc,
        "minSoc": floor * 10,
        "socSet": ceiling * 10,
        "pack1.sn": "BO4FAKE000000001",
        "pack1.packType": 70,
        "chargeMaxLimit": 3000,
        "inverseMaxPower": 3000,
    }


def member(name: str, soc: float, *, available: bool = True, charge_cap: int = 3000,
           discharge_cap: int = 3000, data: dict | None = None):
    reading = Reading(
        key=name, name=name, phase=1, priority=5, available=available, power=0.0,
        charge_limit=0, discharge_limit=0, charge_cap=charge_cap, discharge_cap=discharge_cap,
        full=False, empty=False, soc=soc, capacity_kwh=8.0, response_s=None,
    )
    coordinator = SimpleNamespace(data=mix_data(soc) if data is None else data, pack_count=1)
    return SimpleNamespace(
        name=name,
        reading=lambda: reading,
        controller=SimpleNamespace(coordinator=coordinator),
        own=Settings(**{OPT_MAX_CHARGE_POWER: 3600, OPT_MAX_DISCHARGE_POWER: 3600}),
    )


def test_the_group_adds_energy_and_power_up():
    usable, stored, charge, discharge = group_state([member("a", 50), member("b", 30, charge_cap=1500)])
    # 8 kWh each between 10 and 100 %: 7.2 usable; stored above the floor.
    assert round(usable, 2) == 14.4
    assert round(stored, 2) == round(8 * 0.40 + 8 * 0.20, 2)
    # The power the cluster may ask of each, Max share included.
    assert charge == 4.5 and discharge == 6.0


def test_a_battery_that_does_not_take_part_adds_nothing():
    state = group_state([member("a", 50), member("b", 30, available=False), member("c", 80, data={})])
    assert round(state[0], 2) == 7.2 and state[2] == 3.0


def test_no_battery_left_no_plan():
    assert group_state([]) is None
    assert group_state([member("a", 50, available=False)]) is None


def make_group_pc(members: list, mode: str = MODE_SMART_MATCHING) -> PriceControl:
    pc = PriceControl.__new__(PriceControl)
    pc.group = True
    pc._battery_entry_id = "cluster1"
    pc._battery = Settings(**{OPT_OPERATION_MODE: mode})
    pc._settings = Settings(**{
        OPT_CHARGE_EFFICIENCY: 95, OPT_DISCHARGE_EFFICIENCY: 95, OPT_HOUSE_LOAD: 250,
        OPT_PRICE_CHEAP_MODE: MODE_QUICK_CHARGE, OPT_PRICE_EXPENSIVE_MODE: MODE_QUICK_DISCHARGE,
        OPT_PRICE_BETWEEN_MODE: MODE_SMART_MATCHING,
    })
    pc.hass = SimpleNamespace(
        data={DOMAIN: {"cluster1": SimpleNamespace(members={m.name: m for m in members})}},
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: object()),
    )
    pc._signal = "signal"
    pc._enabled = True
    pc._last_target = None
    pc.price = 0.1
    return pc


def test_the_plan_is_made_for_the_group():
    pc = make_group_pc([member("a", 50), member("b", 30)])
    battery = pc._battery_input()
    assert round(battery.usable_kwh, 2) == 14.4 and battery.charge_kw == 6.0 and battery.discharge_kw == 6.0
    assert battery.eta_charge == 0.95 and battery.house_kw == 0.25
    assert battery.grid_charge and battery.sell


def test_a_virtual_cluster_keeps_its_members_in_its_cluster():
    pc = make_group_pc([])
    copies = [member("Virtual copy 1", 60), member("Virtual copy 2", 40)]
    pc.hass.data[DOMAIN]["cluster1"] = SimpleNamespace(cluster=SimpleNamespace(members={m.name: m for m in copies}))
    assert [m.name for m in pc.group_members()] == ["Virtual copy 1", "Virtual copy 2"]
    assert pc._battery_input() is not None


def test_a_cluster_that_is_not_there_gives_no_plan():
    pc = make_group_pc([])
    pc.hass.data[DOMAIN].pop("cluster1")
    assert pc.group_members() == [] and pc._battery_input() is None


def test_it_writes_the_operation_mode_of_the_cluster(monkeypatch):
    monkeypatch.setattr(P, "async_dispatcher_send", MagicMock())
    pc = make_group_pc([member("a", 50)])
    pc._apply(PRICE_STATUS_CHEAP)
    assert pc.status == PRICE_STATUS_CHEAP
    assert pc._battery.written == [(OPT_OPERATION_MODE, MODE_QUICK_CHARGE)]


def test_a_cluster_is_never_paused_and_has_no_controller():
    pc = make_group_pc([member("a", 50)])
    assert not pc._coordinated()
    assert pc.battery_controller is None and pc.measure is None


def test_switched_off_a_virtual_cluster_follows_its_source_again():
    from custom_components.zendure_restapi.const import MODE_FOLLOW_SOURCE

    pc = make_group_pc([member("Virtual copy 1", 50)], mode=MODE_QUICK_CHARGE)
    pc.virtual = True
    pc._release()
    assert pc._battery.written == [(OPT_OPERATION_MODE, MODE_FOLLOW_SOURCE)]


def test_switched_off_a_battery_cluster_keeps_its_mode():
    pc = make_group_pc([member("a", 50)], mode=MODE_QUICK_CHARGE)
    pc.virtual = False
    pc._release()
    assert pc._battery.written == []
