"""The reason of the cluster status while the primary winds down (v1.8.8).

After a switch from quick to a smart mode the primary still holds the limit of
the quick mode until its own loop takes it down. The group then asks nothing in
that direction, and the reason says the direction is winding down instead of
reading as if the cluster wants it.
"""

from dataclasses import replace

from custom_components.zendure_restapi.cluster.group import Distributor

from .test_cluster_protect import SETTINGS, reading

CALM = replace(SETTINGS, soc_protection=False, soc_balance=0)


def _charging(key: str, limit: int, power: float) -> object:
    return replace(reading(key, 50), charge_limit=limit, power=power)


def test_a_primary_left_charging_winds_down():
    d = Distributor()
    d.primary = "a"
    # Importing 2700 W with the primary still on the quick limit: nothing asks to charge.
    decision = d.decide([_charging("a", 2400, -2400.0), reading("b", 50)], CALM, grid=2700.0, now=0.0)
    assert "A is primary, charge, winding down" in decision.reason


def test_a_primary_asked_to_charge_says_so_plainly():
    d = Distributor()
    d.primary = "a"
    # Exporting 500 W while the primary charges 300 W: charging is still asked.
    decision = d.decide([_charging("a", 300, -300.0), reading("b", 50)], CALM, grid=-500.0, now=0.0)
    assert "A is primary, charge" in decision.reason and "winding down" not in decision.reason
