"""SOC protection under the fixed tariff waits for the sun (v1.8.2).

With SOC protection on price_control and Price Control on the fixed tariff
there is no price list: until v1.8.1 the battery then charged from the grid
at once. Every quarter costs the same, so it now waits for the sun: by day it
charges from solar surplus and from the grid only at the end of the day, or
after 6 hours if that comes first; under its floor at night it first waits for
the day, if that comes within 6 hours. The other decisions keep the cheapest
quarter of the price list. The option is called just "Price Control".
"""

import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from custom_components.zendure_restapi import controller as CT
from custom_components.zendure_restapi import pricecontrol as P
from custom_components.zendure_restapi.cluster.contract import solar_wait
from custom_components.zendure_restapi.cluster.group import Distributor
from custom_components.zendure_restapi.const import (
    DECISION_BANDS,
    DECISION_TARIFF,
    OPT_PRICE_DECISION,
    OPT_SOC_PROTECTION,
    OPT_SUNRISE_OFFSET,
    OPT_SUNSET_OFFSET,
    SOC_PROTECT_PRICE,
)
from custom_components.zendure_restapi.controller import ZendureController
from custom_components.zendure_restapi.pricecontrol import PriceControl

from .test_cluster_protect import SETTINGS, reading

H = 3600.0
SIX = 6 * H
# A day window from 08:30 to 16:40, and the next one a day later; t = 0 is midnight.
DAY = (8.5 * H, 16 * H + 40 * 60, "08:30", "16:40")
NEXT = (DAY[0] + 24 * H, DAY[1] + 24 * H, "08:30", "16:40")
WINDOWS = (DAY, NEXT)


# ── The rule ─────────────────────────────────────────────────────────────

def test_by_day_it_waits_for_solar_until_the_end_of_the_day():
    wait = solar_wait(since=13 * H, now=13 * H, windows=WINDOWS, window_s=SIX)
    assert wait.charge_at == DAY[1] and wait.charge_label == "16:40" and wait.daylight_at is None


def test_six_hours_come_before_the_end_of_the_day():
    wait = solar_wait(since=9 * H, now=9 * H, windows=WINDOWS, window_s=SIX)
    assert wait.charge_at == 15 * H and wait.charge_label is None


def test_at_night_it_waits_for_daylight_within_six_hours():
    wait = solar_wait(since=4 * H, now=4 * H, windows=WINDOWS, window_s=SIX)
    assert wait.daylight_at == "08:30" and wait.charge_at == 10 * H
    # Once the day has begun it waits for solar.
    assert solar_wait(since=4 * H, now=9 * H, windows=WINDOWS, window_s=SIX).daylight_at is None


def test_daylight_too_far_away_charges_at_once():
    wait = solar_wait(since=18 * H, now=18 * H, windows=WINDOWS, window_s=SIX)
    assert wait.charge_at is None and wait.why == "direct: no daylight within 6 h"


def test_the_end_of_the_day_charges_from_the_grid():
    wait = solar_wait(since=13 * H, now=DAY[1], windows=WINDOWS, window_s=SIX)
    assert wait.charge_at is None and wait.why == "direct: no solar surplus by the end of the day"
    wait = solar_wait(since=9 * H, now=15 * H, windows=WINDOWS, window_s=SIX)
    assert wait.charge_at is None and wait.why == "direct: waited 6 h"


def test_no_day_at_all_charges_at_once():
    assert solar_wait(0.0, 0.0, (), SIX).why == "direct: no daylight"


# ── A battery ────────────────────────────────────────────────────────────

def battery(monkeypatch, now: datetime, windows) -> ZendureController:
    monkeypatch.setattr(CT.dt_util, "utcnow", lambda: now)
    c = ZendureController.__new__(ZendureController)
    c.settings = SimpleNamespace(get=lambda key: SOC_PROTECT_PRICE if key == OPT_SOC_PROTECTION else None)
    c._protect_since = None
    c._protect_why = None
    c._solar_windows = lambda: windows
    c._import_periods = lambda: []
    return c


def test_a_battery_under_the_fixed_tariff_waits_for_solar(monkeypatch):
    base = datetime(2026, 10, 31, 0, 0, tzinfo=timezone.utc)
    windows = tuple((base.timestamp() + w[0], base.timestamp() + w[1], w[2], w[3]) for w in WINDOWS)
    c = battery(monkeypatch, base + timedelta(hours=13), windows)
    text = c._protect_waits({}, 4, 5)
    assert text == "SOC 4% under the floor 5%: charges from solar, from the grid at 16:40 at the latest"
    c = battery(monkeypatch, base + timedelta(hours=4), windows)
    assert "waits for daylight at 08:30" in c._protect_waits({}, 4, 5)


def test_a_battery_with_a_price_list_keeps_the_cheapest_quarter(monkeypatch):
    c = battery(monkeypatch, datetime(2026, 10, 31, 4, 0, tzinfo=timezone.utc), None)
    # No fixed tariff and no prices: direct, as in v1.7.6.
    assert c._protect_waits({}, 4, 5) is None
    assert c._protect_why == "direct: no prices from Price Control"


def test_two_points_under_the_floor_does_not_wait(monkeypatch):
    c = battery(monkeypatch, datetime(2026, 10, 31, 13, 0, tzinfo=timezone.utc), WINDOWS)
    assert c._protect_waits({}, 3, 5) is None and "2 points or more" in c._protect_why


# ── A cluster ────────────────────────────────────────────────────────────

def test_a_cluster_under_the_fixed_tariff_waits_for_solar():
    sun = replace(SETTINGS, soc_protection_price=True, solar_windows=WINDOWS)
    d = Distributor()
    decision = d.decide([reading("a", 9), reading("b", 9)], sun, grid=300.0, now=13 * H)
    assert decision.status != "protecting"
    assert decision.attributes["soc_protection_waits"] == (
        "every battery under its SOC floor: charges from solar, from the grid at 16:40 at the latest")
    decision = d.decide([reading("a", 9), reading("b", 9)], sun, grid=300.0, now=DAY[1])
    assert decision.status == "protecting" and "no solar surplus by the end of the day" in decision.reason


def test_group_settings_default_has_no_windows():
    assert SETTINGS.solar_windows is None


# ── Price Control ────────────────────────────────────────────────────────

def test_price_control_gives_windows_only_under_the_fixed_tariff(monkeypatch):
    now = datetime(2026, 10, 31, 9, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(P.dt_util, "now", lambda: now)
    rise = datetime(2026, 10, 31, 6, 30, tzinfo=timezone.utc)
    sets = datetime(2026, 10, 31, 16, 10, tzinfo=timezone.utc)

    def sun_times(self, when):
        days = (when.date() - now.date()).days
        return rise + timedelta(days=days), sets + timedelta(days=days)

    monkeypatch.setattr(PriceControl, "_sun_times", sun_times)
    pc = PriceControl.__new__(PriceControl)
    values = {OPT_PRICE_DECISION: DECISION_TARIFF, OPT_SUNRISE_OFFSET: 60, OPT_SUNSET_OFFSET: -30}
    pc._settings = SimpleNamespace(get=values.get, get_float=lambda key: float(values[key]))
    windows = pc.solar_windows()
    assert windows == [(rise + timedelta(hours=1), sets - timedelta(minutes=30)),
                       (rise + timedelta(days=1, hours=1), sets + timedelta(days=1, minutes=-30))]
    values[OPT_PRICE_DECISION] = DECISION_BANDS
    assert pc.solar_windows() is None


def test_the_option_is_called_price_control_in_every_language():
    root = Path(__file__).parent.parent / "custom_components" / "zendure_restapi"
    for path in [root / "strings.json", *sorted((root / "translations").glob("*.json"))]:
        text = json.dumps(json.loads(path.read_text(encoding="utf-8")), ensure_ascii=False)
        assert '"price_control": "Price Control"' in text, path.name
        assert "6 h)" not in text and "6 t)" not in text and "6 u)" not in text, path.name
