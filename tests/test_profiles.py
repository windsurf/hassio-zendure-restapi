"""Profiles from the battery's own use (v1.8.7): efficiency per power, house load, usage.

Measured only: nothing here steers. The efficiency curve counts stable stretches
per band of AC power and fits the loss; the round trip of whole cycles corrects
it. The house load profile keeps the load per quarter, workdays and weekends
apart, with the load the battery could cover. The usage profile keeps charging
and discharging per hour.
"""

from datetime import date, datetime, time, timedelta, timezone

from custom_components.zendure_restapi.measure import BatteryMeasure, Sample
from custom_components.zendure_restapi.profiles import (
    BAND_EDGES,
    BANDS_VERSION,
    EfficiencyCurve,
    HouseProfile,
    UsageProfile,
    band_of,
    fit_loss,
    sum_usage,
)

T0 = datetime(2026, 11, 2, 10, 0, tzinfo=timezone.utc)   # a Monday


def _charge_loss(power):
    """A published fit for the same model: the loss in W while charging."""
    return 44 + 0.012 * power + 1.15e-5 * power * power


def _run(curve, start, power, seconds, dc_of, step=5):
    for t in range(0, seconds + 1, step):
        curve.add(start + timedelta(seconds=t), power, dc_of(power))


def test_bands():
    assert band_of(30) is None
    assert band_of(-120) == BAND_EDGES.index(100)
    assert band_of(2999) == band_of(3000) == BAND_EDGES.index(3000)
    assert band_of(2600) == BAND_EDGES.index(2500)
    assert band_of(5000) == len(BAND_EDGES) - 1


def test_the_fit_finds_the_published_curve():
    points = [(p, _charge_loss(p), 1.0) for p in (250, 500, 1000, 1500, 2000, 3000)]
    fit = fit_loss(points)
    assert abs(fit["a"] - 44) < 1
    assert abs(fit["b"] - 0.012) < 0.001
    assert abs(fit["c"] - 1.15e-5) < 1e-6
    assert fit_loss(points[:2])["c"] == 0.0
    assert fit_loss(points[:1]) is None


def test_only_stable_stretches_count():
    curve = EfficiencyCurve()
    # A step from 500 to 1500 W with the DC side 7 s behind: the stretch right
    # after the step must not count, so the 1000 W band stays empty.
    for t in range(0, 7200, 5):
        ac = -500 if t < 3600 else -1500
        dc_ac = -500 if t < 3607 else -1500
        dc = dc_ac + _charge_loss(abs(dc_ac))
        curve.add(T0 + timedelta(seconds=t), ac, dc)
    bands = curve.bands["charge"]
    assert BAND_EDGES.index(500) in bands and BAND_EDGES.index(1500) in bands
    assert BAND_EDGES.index(1000) not in bands
    spot = {row["band_w"]: row["efficiency"] for row in curve.spot("charge")}
    expected = (500 - _charge_loss(500)) / 500 * 100
    assert abs(spot[500] - expected) < 0.5


def test_a_band_shows_from_enough_energy():
    curve = EfficiencyCurve()
    _run(curve, T0, -300, 600, lambda p: p + _charge_loss(abs(p)))
    assert curve.spot("charge") == []           # 300 W for 10 min: 0.05 kWh
    _run(curve, T0 + timedelta(hours=1), -300, 3600, lambda p: p + _charge_loss(abs(p)))
    assert curve.spot("charge")[0]["band_w"] == 300


def _filled():
    curve = EfficiencyCurve()
    start = T0
    for power in (500, 1000, 1500, 2000, 3000):
        _run(curve, start, -power, 3600, lambda p: p + _charge_loss(abs(p)))
        _run(curve, start + timedelta(hours=2), power, 3600, lambda p: p + 34 + 0.009 * p + 1.38e-5 * p * p)
        start += timedelta(hours=4)
    return curve


def test_stages_and_the_round_trip_correction():
    curve = _filled()
    eta_c = curve.efficiency_at("charge", 1500)
    eta_d = curve.efficiency_at("discharge", 1500)
    # A cycle that kept 3 points less than the spot figures promise.
    cycles = [{"ac_in_kwh": 10.0, "ac_out_kwh": 10.0 * eta_c * eta_d * 0.97}]
    factor = curve.correction(cycles, 1500, 1500)
    assert abs(factor - 0.97 ** 0.5) < 0.001
    stages = curve.curve("charge", factor)
    assert stages[0][0] == 500 and stages[-1][0] == 3000     # the bands measured
    plain = dict(curve.curve("charge", None))
    corrected = dict(stages)
    assert corrected[1500] < plain[1500]
    attributes = curve.attributes("discharge", cycles, 1500, 1500)
    assert set(attributes) == {"curve", "loss_fit", "round_trip_factor", "stages"}


def test_the_curve_is_kept_and_older_days_count_less():
    curve = _filled()
    copy = EfficiencyCurve()
    copy.load(curve.to_dict())
    assert copy.spot("charge") == curve.spot("charge")
    kwh = copy.bands["charge"][BAND_EDGES.index(1000)][0]
    later = datetime.combine(date.fromordinal(copy.day + 30), time(12), tzinfo=timezone.utc)
    copy.add(later, 0, 0)
    assert abs(copy.bands["charge"][BAND_EDGES.index(1000)][0] - kwh / 2) < 0.01


def _house_day(profile, start, load_of, battery=0.0, threshold=50.0, min_s=30.0):
    for t in range(0, 24 * 3600, 10):
        moment = start + timedelta(seconds=t)
        profile.add(moment, load_of(moment), battery, threshold, min_s)


def test_house_load_per_quarter_and_its_base():
    profile = HouseProfile()
    # 150 W, with a 2 kW kettle for one minute in every quarter.
    _house_day(profile, T0.replace(hour=0), lambda m: 2000.0 if m.minute % 15 == 0 else 150.0)
    profile.add(T0.replace(hour=0) + timedelta(days=1), 150.0, 0.0, 50.0, 30.0)
    attributes = profile.attributes()
    assert attributes["days"] == 1
    assert 250 <= attributes["workday_w"][40] <= 290          # 150 + 1/15 of 1850
    assert attributes["workday_base_w"][40] == 150
    assert attributes["weekend_w"][40] is None
    assert attributes["yesterday"]["house_kwh"] > 3.6


def test_coverable_load_needs_start_after():
    profile = HouseProfile()
    start = T0.replace(hour=12)
    # 20 s above the threshold, then 120 s above it.
    for t in range(0, 400, 10):
        load = 400.0 if 10 <= t < 30 or 100 <= t < 220 else 20.0
        profile.add(start + timedelta(seconds=t), load, 0.0, 50.0, 30.0)
    coverable = profile.today["coverable_kwh"]
    assert abs(coverable - 400 * 120 / 3_600_000) < 0.002


def test_usage_per_hour_and_the_cluster_sum():
    usage = UsageProfile()
    usage.add(T0.replace(hour=2), 1.0, 0.0)
    usage.add(T0.replace(hour=19), 0.0, 2.0)
    attributes = usage.attributes()
    assert attributes["charge_share_by_hour"][2] == 100.0
    assert attributes["discharge_share_by_hour"][19] == 100.0
    other = UsageProfile()
    other.add(T0.replace(hour=20), 0.0, 2.0)
    total = sum_usage([usage, other])
    assert total["discharge_share_by_hour"][19] == 50.0 and total["discharged_today_kwh"] == 4.0
    copy = UsageProfile()
    copy.load(usage.to_dict())
    assert copy.attributes() == attributes


def test_the_battery_measure_feeds_the_profiles():
    m = BatteryMeasure()
    for t in range(0, 7200, 10):
        now = T0 + timedelta(seconds=t)
        m.battery_sample(Sample(
            now=now, today="2026-11-02", ac_w=1000.0, dc_w=1000.0 + 34 + 9 + 13.8,
            soc=50, soc_limit=0, input_limit=0, output_limit=1000,
        ))
        m.grid_sample(now, 200.0, None)
    assert m.usage.today["discharged_kwh"] > 1.9
    assert m.house.today["covered_kwh"] > 1.9
    assert m.curve.spot("discharge")[0]["band_w"] == 1000
    restored = BatteryMeasure()
    restored.load(m.to_dict())
    assert restored.curve.spot("discharge") == m.curve.spot("discharge")
    assert restored.usage.today == m.usage.today
    assert set(m.diagnostics()) >= {"efficiency_curve", "house_profile", "usage_profile"}


def test_the_cluster_status_carries_the_group_usage():
    from types import SimpleNamespace

    from custom_components.zendure_restapi.cluster_entities import usage_totals

    members = {}
    for key, hour in (("a", 19), ("b", 20)):
        measure = BatteryMeasure()
        measure.usage.add(T0.replace(hour=hour), 0.0, 1.0)
        members[key] = SimpleNamespace(controller=SimpleNamespace(measure=measure))
    totals = usage_totals(SimpleNamespace(cluster=SimpleNamespace(members=members)))
    assert totals["discharged_today_kwh"] == 2.0
    assert totals["discharge_share_by_hour"][19] == totals["discharge_share_by_hour"][20] == 50.0
    assert usage_totals(SimpleNamespace(cluster=SimpleNamespace(members={}))) == {}


def test_charging_at_the_maximum_stays_in_one_band():
    curve = EfficiencyCurve()
    # Charging at the maximum swings around 3000 W: one band, so no fit and no stages.
    for t in range(0, 7200, 5):
        power = -2999 if t % 10 else -3000
        curve.add(T0 + timedelta(seconds=t), power, power + _charge_loss(abs(power)))
    assert [row["band_w"] for row in curve.spot("charge")] == [3000]
    assert curve.fit("charge") is None and curve.curve("charge", None) == []


def test_a_fit_needs_bands_that_differ_in_power():
    close = [(2500, _charge_loss(2500), 1.0), (3000, _charge_loss(3000), 1.0)]
    assert fit_loss(close) is None                      # 1.2 times: says nothing of the shape
    apart = [(1000, _charge_loss(1000), 1.0), (3000, _charge_loss(3000), 1.0)]
    assert fit_loss(apart)["c"] == 0.0                  # 3 times: linear
    three = [(1000, _charge_loss(1000), 1.0), (2000, _charge_loss(2000), 1.0), (3000, _charge_loss(3000), 1.0)]
    assert fit_loss(three)["c"] == 0.0                  # three bands but 3 times: still linear
    wide = [(500, _charge_loss(500), 1.0)] + three
    assert fit_loss(wide)["c"] != 0.0                   # 6 times: quadratic


def test_stages_stay_within_the_bands_measured():
    curve = EfficiencyCurve()
    _run(curve, T0, -1000, 3600, lambda p: p + _charge_loss(abs(p)))
    _run(curve, T0 + timedelta(hours=2), -3000, 3600, lambda p: p + _charge_loss(abs(p)))
    assert [edge for edge, _ in curve.curve("charge", None)] == [1000, 1500, 2000, 2500, 3000]


def test_sums_of_the_old_bands_are_dropped_once():
    curve = _filled()
    data = curve.to_dict()
    assert data["bands_version"] == BANDS_VERSION
    old = dict(data)
    old.pop("bands_version")
    copy = EfficiencyCurve()
    copy.load(old)
    assert copy.bands == {"charge": {}, "discharge": {}} and copy.day == curve.day
    again = EfficiencyCurve()
    again.load(data)
    assert again.spot("charge") == curve.spot("charge")


def test_the_expected_load_falls_back():
    profile = HouseProfile()
    monday = T0.replace(hour=6, minute=20)                      # quarter 25 on a workday
    saturday = monday + timedelta(days=5)
    assert profile.lookup(monday) == (None, None)
    profile.average["workday"][22] = 237.0                      # 05:30, three quarters back
    assert profile.lookup(monday) == (237, "earlier_quarter")
    profile.average["workday"][24] = 229.0
    assert profile.lookup(monday) == (229, "earlier_quarter")
    profile.average["weekend"][25] = 310.0
    assert profile.lookup(monday) == (310, "other_days")
    profile.average["workday"][25] = 205.0
    assert profile.lookup(monday) == (205, "profile")
    assert profile.lookup(saturday) == (310, "profile")
    assert profile.expected(monday) == 205


def test_no_fallback_beyond_two_hours():
    profile = HouseProfile()
    monday = T0.replace(hour=6, minute=20)                      # quarter 25
    profile.average["workday"][16] = 200.0                      # 04:00: nine quarters back
    assert profile.lookup(monday) == (None, None)
    profile.average["workday"][17] = 210.0                      # 04:15: eight quarters back
    assert profile.lookup(monday) == (210, "earlier_quarter")
    midnight = T0.replace(hour=0, minute=5)                     # quarter 0 looks back over midnight
    profile.average["workday"][95] = 180.0
    assert profile.lookup(midnight) == (180, "earlier_quarter")
