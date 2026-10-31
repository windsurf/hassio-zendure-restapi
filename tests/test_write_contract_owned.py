"""Standby after a mode in which this integration set the limits (v1.7.0).

A limit written moments ago may not be in the last poll yet. When the
controller set the limits itself, standby sends the stop for both limits even
if the poll already reads 0, so a battery that has just started stops (a
primary leaving the cluster right after it started). smartMode 0 stays last in
the same request: with smartMode 1 before it, the limits go to RAM, as in 1.6.x.
"""

import asyncio

from custom_components.zendure_restapi import controller as C

from .helpers import make_controller, sent


def run(coro):
    return asyncio.run(coro)


def test_standby_with_owned_limits_stops_both_even_when_the_poll_reads_zero():
    c = make_controller(C.MODE_STANDBY)
    c._owns_limits = True
    data = {"smartMode": 1, "inputLimit": 0, "outputLimit": 0, "acMode": C.AC_MODE_CHARGE}
    run(c._apply_standby(data))
    assert sent(c) == [[("inputLimit", 0), ("outputLimit", 0), ("smartMode", 0)]]
    assert c._owns_limits is False


def test_standby_after_quick_charge_sends_the_zero_limit_too():
    c = make_controller(C.MODE_STANDBY)
    c._owns_limits = True
    data = {"smartMode": 1, "inputLimit": 3000, "outputLimit": 0, "acMode": C.AC_MODE_CHARGE}
    run(c._apply_standby(data))
    assert sent(c) == [[("inputLimit", 0), ("outputLimit", 0), ("smartMode", 0)]]
    run(c._apply_standby(data))
    assert len(sent(c)) == 1, "standby writes once per stay"
