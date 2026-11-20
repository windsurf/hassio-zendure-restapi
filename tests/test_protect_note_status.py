"""SOC protection that waits says so in the status text (v1.8.4).

With SOC protection `price_control` a battery under its floor may wait for the
cheapest quarter, or under the fixed tariff for the sun. Until v1.8.3 that was
only in the attribute `soc_protection_waits`; the status text showed the mode
that ran meanwhile, for example "discharging blocked: battery empty".
"""

from types import SimpleNamespace

from custom_components.zendure_restapi import controller as C

from .test_stop_below_deadband import battery

NOTE = "SOC 4% under the floor 5%: charges from solar, from the grid at 17:30 at the latest"


def status(monkeypatch, note, state="tracking", reason="grid 213 W, discharging blocked: battery empty"):
    c, _, _ = battery(monkeypatch)
    c.state = SimpleNamespace(meter_power=213, battery_power=0)
    c.trace = SimpleNamespace(path=None)
    c._trim_writes = 0
    c._source_used = "test"
    c.protect_note = note
    C.ZendureController._set_state(c, state, reason)
    return c.state


def test_a_wait_is_added_to_the_status(monkeypatch):
    st = status(monkeypatch, NOTE)
    assert st.reason == "grid 213 W, discharging blocked: battery empty; " + NOTE
    assert st.attributes["soc_protection_waits"] == NOTE


def test_no_wait_no_addition(monkeypatch):
    assert status(monkeypatch, None).reason == "grid 213 W, discharging blocked: battery empty"


def test_not_twice_and_not_while_protecting(monkeypatch):
    assert status(monkeypatch, NOTE, reason="x; " + NOTE).reason == "x; " + NOTE
    assert status(monkeypatch, NOTE, state="protecting", reason="charging").reason == "charging"
