"""The PV series on the cluster dashboard: the solar power of the batteries.

The same series as on the battery dashboard, amber and filled: the external
solar sensor chosen under Configure, else the device's own PV once it has
produced something; with both, the external one as a series of its own. A
Virtual cluster shows the real battery's PV; its copies have none. An external
sensor chosen for more batteries of a battery cluster shows once. Without a
source no series.
"""

from custom_components.zendure_restapi import cluster_dashboard as CD

from .test_cluster_dashboard import group

PREFIX = "zendure_solarflow_3000_mix_ac_"
OWN_POWER = f"sensor.{PREFIX}pv_input_power"
OWN_ENERGY = f"sensor.{PREFIX}pv_energy"
EXT_POWER = "sensor.roof_solar_power"
EXT_ENERGY = "sensor.roof_solar_energy"
KNOWN = {OWN_POWER, OWN_ENERGY}
def args(own_pv=False, power=None, energy=None) -> dict:
    return {"own_pv": own_pv, "solar_power": power, "solar_energy": energy}



def with_solar(*sources) -> dict:
    g = group()
    g["solar"] = []
    for source in sources:
        CD.add_solar_sources(g["solar"], source)
    return g



def pv(series) -> list:
    return [s for s in series if "pv_" in s["entity"] or "solar" in s["entity"]]



def test_own_pv_only_once_it_has_produced():
    assert CD.solar_sources("A", PREFIX, args(own_pv=False), KNOWN) == []
    (own,) = CD.solar_sources("A", PREFIX, args(own_pv=True), KNOWN)
    assert (own["power"], own["energy"], own["external"]) == (OWN_POWER, OWN_ENERGY, False)



def test_own_pv_without_the_entity_is_left_out():
    assert CD.solar_sources("A", PREFIX, args(own_pv=True), set()) == []



def test_external_sensor_takes_over_without_own_pv():
    (ext,) = CD.solar_sources("A", PREFIX, args(power=EXT_POWER, energy=EXT_ENERGY), KNOWN)
    assert (ext["power"], ext["energy"], ext["external"]) == (EXT_POWER, EXT_ENERGY, True)



def test_external_sensor_follows_own_pv():
    sources = CD.solar_sources("A", PREFIX, args(own_pv=True, power=EXT_POWER), KNOWN)
    assert [s["power"] for s in sources] == [OWN_POWER, EXT_POWER]
    assert sources[1]["energy"] is None



def test_an_external_sensor_of_two_batteries_shows_once():
    g = with_solar(CD.solar_sources("A", "a_", args(power=EXT_POWER), set()),
                   CD.solar_sources("B", "b_", args(power=EXT_POWER), set()))
    assert [s["power"] for s in g["solar"]] == [EXT_POWER]



def test_day_chart_pv_after_charging_in_amber():
    g = with_solar(CD.solar_sources("A", PREFIX, args(own_pv=True), KNOWN))
    series = CD.day_chart(g, "sensor.grid", None, None, 1)["series"]
    names = [s["name"] for s in series]
    assert names[names.index("Charging") + 1] == "PV"
    (serie,) = pv(series)
    assert (serie["entity"], serie["color"], serie["type"]) == (OWN_POWER, "#FFB300", "area")



def test_no_source_no_series():
    g = with_solar()
    assert all(s["name"] != "PV" for s in CD.day_chart(g, "sensor.grid", None, None, 1)["series"])



def test_a_group_without_the_key_is_as_before():
    assert all(s["name"] != "PV" for s in CD.day_chart(group(), "sensor.grid", None, None, 1)["series"])



def test_both_sources_named_and_the_external_dashed():
    g = with_solar(CD.solar_sources("A", PREFIX, args(own_pv=True, power=EXT_POWER), KNOWN))
    series = pv(CD.day_chart(g, None, None, None, 1)["series"])
    assert [(s["name"], s.get("stroke_dash")) for s in series] == [("PV", None), ("PV external", 4)]



def test_two_batteries_each_with_own_pv_named_by_battery():
    g = with_solar(CD.solar_sources("Battery A", PREFIX, args(own_pv=True), KNOWN),
                   CD.solar_sources("Battery B", "b_", args(own_pv=True),
                                    {"sensor.b_pv_input_power", "sensor.b_pv_energy"}))
    names = [s["name"] for s in pv(CD.day_chart(g, None, None, None, 2)["series"])]
    assert names == ["PV Battery A", "PV Battery B"]


def test_without_apexcharts_pv_in_the_history_graph():
    g = with_solar(CD.solar_sources("A", PREFIX, args(own_pv=True), KNOWN))
    batteries = [{"name": "Battery A", "power": "sensor.a_battery_power", "soc": "sensor.a_state_of_charge"}]
    view = CD.cluster_view(g, batteries, [], False, custom_cards=set())
    cards = [c for s in view["sections"] for c in s["cards"] if c.get("type") == "history-graph"]
    assert cards and all({"entity": OWN_POWER} in c["entities"] for c in cards)
