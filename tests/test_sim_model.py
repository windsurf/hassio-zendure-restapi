"""The device model behind the copies of a Virtual cluster (v1.7.2).

From standstill the first power comes ~8 s after the write and full power
~2 s later, as measured; a running battery still changes at 200 W/s.
Energy per percent from the real battery's measurements: usable capacity
over the measured range when discharging, divided by the cells' round trip
efficiency when charging. Without a measurement: capacity over 100 %.
"""

from types import SimpleNamespace

import pytest

from custom_components.zendure_restapi.sim.cluster import VirtualCluster
from custom_components.zendure_restapi.sim.device import (
    RAMP_UP_W_S, SETPOINT_DELAY_S, START_DELAY_S, SimDevice,
)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def device(soc: float = 50.0, **kw) -> tuple[SimDevice, Clock]:
    clock = Clock()
    report = {"product": "solarFlow3000MixAC+", "properties": {"messageId": 1, "minSoc": 50, "socSet": 1000}}
    return SimDevice(report, "SIMULATED1ABCDEF", soc, 7.973, clock=clock, **kw), clock


def run(dev: SimDevice, clock: Clock, seconds: float, step: float = 0.1) -> list[tuple[float, float]]:
    start = clock.t
    out = []
    while clock.t - start < seconds - 1e-9:
        clock.t += step
        dev.step()
        out.append((clock.t - start, dev.power))
    return out


def test_cold_start_reaches_full_power_two_seconds_after_the_first_flow():
    dev, clock = device()
    dev.write({"outputLimit": 3000})
    trace = run(dev, clock, 15)
    first = next(t for t, p in trace if p > 20)
    full = next(t for t, p in trace if p >= 2999)
    assert first == pytest.approx(SETPOINT_DELAY_S + START_DELAY_S, abs=0.2)
    assert full - first == pytest.approx(2.0, abs=0.2)


def test_a_running_battery_still_rises_at_200_w_per_second():
    dev, clock = device()
    dev.write({"outputLimit": 1000})
    run(dev, clock, 15)
    assert dev.power == pytest.approx(1000)
    dev.write({"outputLimit": 2000})
    run(dev, clock, SETPOINT_DELAY_S + 2.0)
    assert dev.power == pytest.approx(1000 + 2.0 * RAMP_UP_W_S, abs=25)


def test_after_a_stop_the_next_start_is_cold_again():
    dev, clock = device()
    dev.write({"outputLimit": 1000})
    run(dev, clock, 15)
    dev.write({"outputLimit": 0})
    run(dev, clock, 10)
    assert dev.power == 0
    dev.write({"inputLimit": 3000})
    trace = run(dev, clock, 15)
    first = next(t for t, p in trace if p < -20)
    full = next(t for t, p in trace if p <= -2999)
    assert full - first == pytest.approx(2.0, abs=0.2)


def test_discharge_takes_the_measured_energy_per_percent():
    # 84 Wh per % of the cells: 3000 W AC is 3000 / 0.95 W DC.
    dev, clock = device(50.0, wh_per_percent=84.0, charge_efficiency=0.96)
    dev.power = 3000.0
    dev.props["outputLimit"] = 3000
    run(dev, clock, 3600, step=1.0)
    assert 50.0 - dev.soc == pytest.approx(3000 / 0.95 / 84.0, rel=0.01)


def test_charge_needs_more_per_percent_by_the_round_trip():
    dev, clock = device(20.0, wh_per_percent=84.0, charge_efficiency=0.96)
    dev.power = -3000.0
    dev.props["inputLimit"] = 3000
    run(dev, clock, 3600, step=1.0)
    assert dev.soc - 20.0 == pytest.approx(3000 * 0.95 / (84.0 / 0.96), rel=0.01)


def test_without_a_measurement_capacity_over_100_both_ways():
    dev, _ = device()
    assert dev.wh_per_percent_out == pytest.approx(79.73)
    assert dev.wh_per_percent_in == pytest.approx(79.73)


def cluster_with(capacities: list, round_trip: float | None) -> VirtualCluster:
    vc = VirtualCluster.__new__(VirtualCluster)
    vc.source = SimpleNamespace(measure=SimpleNamespace(capacities=capacities, round_trip=lambda side: round_trip))
    vc._capacity = lambda: 7.973
    return vc


def test_energy_from_the_real_battery_measurements():
    vc = cluster_with([{"kwh": 7.973, "from_soc": 100, "to_soc": 5}], 96.0)
    e = vc.energy_per_percent()
    assert e["source"] == "measured"
    assert e["wh_per_percent_out"] == pytest.approx(83.9, abs=0.1)
    assert e["wh_per_percent_in"] == pytest.approx(87.4, abs=0.1)
    assert e["charge_efficiency"] == pytest.approx(0.96)


def test_energy_without_measurements_is_nominal():
    e = cluster_with([], None).energy_per_percent()
    assert e["source"] == "nominal" and e["wh_per_percent"] is None and e["charge_efficiency"] == 1.0
    # A capacity without a round trip: discharging measured, charging the same.
    e = cluster_with([{"kwh": 7.973, "from_soc": 100, "to_soc": 5}], None).energy_per_percent()
    assert e["wh_per_percent_out"] == e["wh_per_percent_in"]
