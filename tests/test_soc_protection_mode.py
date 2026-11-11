"""SOC protection with Price Control, and for a whole cluster (v1.7.6).

On a battery on its own SOC protection is a choice (until v1.7.5 a switch):
`off`, `direct` (as the switch when on) or `price_control`: charge from under minSoc in the cheapest period within 6 h of
the Price Control price list, directly without one, after 6 h, or 2 points or
more under minSoc. In a battery cluster the cluster charges every battery once
all of them are under their floor (role protect).
"""

import asyncio
from datetime import timedelta
from types import SimpleNamespace

from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi.const import (
    DEFAULTS, OPT_SOC_PROTECTION, SOC_PROTECT_DIRECT, SOC_PROTECT_HARD_POINTS, SOC_PROTECT_OFF,
    SOC_PROTECT_PRICE, SOC_PROTECT_WINDOW_H, soc_protection_choice,
)
from custom_components.zendure_restapi.settings import ROLE_PROTECT

from .helpers import T0, make_controller, sent

Q = timedelta(minutes=15)


def data(level, min_soc=100):
    return {"electricLevel": level, "socLimit": 0, "minSoc": min_soc, "socSet": 1000,
            "inputLimit": 0, "outputLimit": 0, "smartMode": 1, "acMode": C.AC_MODE_CHARGE}


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def battery(monkeypatch, mode=SOC_PROTECT_PRICE, prices=None):
    c = make_controller(C.MODE_SMART_MATCHING)
    c.settings = SimpleNamespace(
        coordinated=False, group=None, role=None,
        get=lambda key, default=None: {
            C.OPT_OPERATION_MODE: C.MODE_SMART_MATCHING, OPT_SOC_PROTECTION: mode,
        }.get(key, default),
    )
    c._cap = lambda d, charging: 800
    c._meter_reading = lambda: (0.0, "test")
    c._stop_key = lambda: "test"
    c._import_periods = lambda: list(prices or [])
    monkeypatch.setattr(C, "_OPEN_LIMITS", set())

    async def no_guard(d, wanted):
        return False

    c._reverse_guard = no_guard
    clock = Clock()
    monkeypatch.setattr(C.dt_util, "utcnow", clock)
    return c, clock


def curve(*values):
    """Quarters from T0 with these import prices."""
    return [(T0 + i * Q, T0 + (i + 1) * Q, v) for i, v in enumerate(values)]


def run(c, level):
    return asyncio.run(c._handle_soc_protection(data(level), C.MODE_SMART_MATCHING))


def charged(c):
    return ("inputLimit", 800) in [kv for req in sent(c) for kv in req]


def test_the_switch_of_v175_reads_as_a_choice():
    assert soc_protection_choice(True) == SOC_PROTECT_DIRECT
    assert soc_protection_choice(False) == SOC_PROTECT_OFF
    assert soc_protection_choice("price_control") == SOC_PROTECT_PRICE
    assert soc_protection_choice("nonsense") == SOC_PROTECT_DIRECT
    assert DEFAULTS[OPT_SOC_PROTECTION] == SOC_PROTECT_DIRECT


def test_off_does_nothing(monkeypatch):
    c, _ = battery(monkeypatch, mode=SOC_PROTECT_OFF, prices=curve(0.10))
    assert run(c, 5) is False and not charged(c)


def test_a_stored_true_charges_directly(monkeypatch):
    c, _ = battery(monkeypatch, mode=True, prices=curve(0.40, 0.10))
    assert run(c, 9) is True and charged(c)


def test_direct_charges_at_once(monkeypatch):
    c, _ = battery(monkeypatch, mode=SOC_PROTECT_DIRECT, prices=curve(0.40, 0.10))
    assert run(c, 9) is True and charged(c)


def test_price_control_waits_for_the_cheapest_quarter(monkeypatch):
    c, clock = battery(monkeypatch, prices=curve(0.40, 0.30, 0.10, 0.20))
    assert run(c, 9) is False, "the strategy carries on meanwhile"
    assert not charged(c) and not c._protecting
    assert "charges at" in c.protect_note and "0.1 per kWh" in c.protect_note and "now 0.4" in c.protect_note
    clock.now = T0 + 2 * Q
    assert run(c, 9) is True and charged(c)
    assert c.protect_note is None


def test_once_charging_it_carries_on_whatever_the_price(monkeypatch):
    c, clock = battery(monkeypatch, prices=curve(0.10, 0.90, 0.90))
    assert run(c, 9) is True
    clock.now = T0 + Q
    assert run(c, 12) is True, "past the floor, not yet at floor + 5"
    assert run(c, 15) is False and not c._protecting


def test_of_equal_prices_the_first(monkeypatch):
    c, _ = battery(monkeypatch, prices=curve(0.20, 0.20, 0.20))
    assert run(c, 9) is True


def test_without_prices_direct(monkeypatch):
    c, _ = battery(monkeypatch, prices=[])
    assert run(c, 9) is True and charged(c)
    assert "no prices" in c._protect_why


def test_two_points_under_the_floor_direct(monkeypatch):
    c, _ = battery(monkeypatch, prices=curve(0.40, 0.10))
    assert run(c, 10 - SOC_PROTECT_HARD_POINTS) is True and charged(c)


def test_after_the_window_direct(monkeypatch):
    late = T0 + timedelta(hours=7)
    c, clock = battery(monkeypatch, prices=curve(0.40, 0.40, 0.05) + [(late, late + Q, 0.01)])
    assert run(c, 9) is False, "waits for the 0.05 quarter; the 0.01 one lies past 6 h"
    assert "0.05" in c.protect_note
    clock.now = T0 + timedelta(hours=SOC_PROTECT_WINDOW_H)
    assert run(c, 9) is True
    assert "waited" in c._protect_why


def test_above_the_floor_it_forgets_the_wait(monkeypatch):
    c, _ = battery(monkeypatch, prices=curve(0.40, 0.10))
    run(c, 9)
    assert c._protect_since is not None
    assert run(c, 10) is False
    assert c._protect_since is None and c.protect_note is None


def test_in_a_cluster_only_the_role_protect_charges(monkeypatch):
    c, _ = battery(monkeypatch, mode=SOC_PROTECT_DIRECT)
    c.settings.coordinated = True
    c.settings.role = "primary"
    assert run(c, 9) is False and not charged(c)
    c.settings.role = ROLE_PROTECT
    assert run(c, 9) is True and charged(c)
    assert c._set_state.call_args.args[0] == "protecting"
    assert "cluster" in c._set_state.call_args.args[1]


def test_price_control_hands_out_the_periods_ahead(monkeypatch):
    from custom_components.zendure_restapi import pricecontrol as P
    from custom_components.zendure_restapi.dayplan import Period

    pc = P.PriceControl.__new__(P.PriceControl)
    pc._periods = [Period(T0 - Q, T0, 0.5), Period(T0, T0 + Q, 0.1), Period(T0 + Q, T0 + 2 * Q, 0.2)]
    pc.tariff = lambda: SimpleNamespace(spot=lambda v: v, import_price=lambda s: s * 2)
    monkeypatch.setattr(P.dt_util, "now", lambda: T0 + timedelta(minutes=5))
    periods = pc.import_periods()
    assert [p[2] for p in periods] == [0.2, 0.4], "the past one is left out"
    assert periods[0][0] == T0 + timedelta(minutes=5), "the current one from now"
