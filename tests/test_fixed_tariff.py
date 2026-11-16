"""Price bands and the fixed tariff (v1.8.2).

Price bands is the decision called Fixed prices until v1.8.1; a stored
"fixed_prices" reads as the new name and is stored anew once. The fixed tariff
needs no price sensor: a fixed import and export price, and an Operation mode
for the day and one for the night, the day running from sunrise plus its
offset to sunset plus its offset.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from custom_components.zendure_restapi import pricecontrol as P
from custom_components.zendure_restapi.const import (
    DECISION_BANDS,
    DECISION_BANDS_OLD,
    DECISION_PLAN,
    DECISION_TARIFF,
    DEFAULTS,
    MODE_SMART_CHARGE,
    MODE_SMART_MATCHING,
    MODE_STANDBY,
    OPT_FIXED_EXPORT_PRICE,
    OPT_FIXED_IMPORT_PRICE,
    OPT_NET_METERING,
    OPT_OPERATION_MODE,
    OPT_PRICE_DECISION,
    OPT_SUNRISE_MODE,
    OPT_SUNRISE_OFFSET,
    OPT_SUNSET_MODE,
    OPT_SUNSET_OFFSET,
    PRICE_DECISIONS,
    PRICE_STATUS_SUN_DOWN,
    PRICE_STATUS_SUN_UP,
    PRICE_STATUSES,
    decision_choice,
    price_presets,
    stop_charge_advice,
)
from custom_components.zendure_restapi.pricecontrol import PriceControl, sun_band

UTC = timezone.utc
SUNRISE = datetime(2026, 6, 1, 4, 0, tzinfo=UTC)
SUNSET = datetime(2026, 6, 1, 20, 0, tzinfo=UTC)


# ── Price bands ────────────────────────────────────────────────────

def test_three_decisions_and_the_old_name_reads_as_cheap_expensive():
    assert PRICE_DECISIONS == (DECISION_BANDS, DECISION_PLAN, DECISION_TARIFF)
    assert DEFAULTS[OPT_PRICE_DECISION] == DECISION_BANDS == "price_bands"
    assert decision_choice(DECISION_BANDS_OLD) == DECISION_BANDS
    assert decision_choice(DECISION_PLAN) == DECISION_PLAN
    assert decision_choice("something else") == DECISION_BANDS


def test_the_old_name_is_stored_anew_once():
    pc = PriceControl.__new__(PriceControl)
    updates = []
    entry = SimpleNamespace(options={OPT_PRICE_DECISION: DECISION_BANDS_OLD, "price_cheap": 0.1})
    pc._entry = entry
    pc.hass = SimpleNamespace(config_entries=SimpleNamespace(
        async_update_entry=lambda e, options: updates.append(options)))
    assert pc._migrate_decision()
    assert updates == [{OPT_PRICE_DECISION: DECISION_BANDS, "price_cheap": 0.1}]
    entry.options = updates[0]
    assert not pc._migrate_decision()


# ── The sun ──────────────────────────────────────────────────────────────

def test_the_day_runs_from_sunrise_plus_its_offset_to_sunset_plus_its_offset():
    band, start, end = sun_band(SUNRISE + timedelta(minutes=59), SUNRISE, SUNSET, 60, -30)
    assert band == PRICE_STATUS_SUN_DOWN
    assert start == SUNRISE + timedelta(hours=1) and end == SUNSET - timedelta(minutes=30)
    assert sun_band(SUNRISE + timedelta(minutes=60), SUNRISE, SUNSET, 60, -30)[0] == PRICE_STATUS_SUN_UP
    assert sun_band(SUNSET - timedelta(minutes=31), SUNRISE, SUNSET, 60, -30)[0] == PRICE_STATUS_SUN_UP
    assert sun_band(SUNSET - timedelta(minutes=30), SUNRISE, SUNSET, 60, -30)[0] == PRICE_STATUS_SUN_DOWN


def test_offsets_that_close_the_window_leave_no_day():
    sunrise = datetime(2026, 12, 21, 8, 0, tzinfo=UTC)
    sunset = datetime(2026, 12, 21, 12, 0, tzinfo=UTC)
    assert sun_band(sunrise + timedelta(hours=2), sunrise, sunset, 180, -180)[0] == PRICE_STATUS_SUN_DOWN


def test_without_sunrise_or_sunset_the_sun_decides():
    now = datetime(2026, 6, 21, 0, 0, tzinfo=UTC)
    assert sun_band(now, None, None, 60, -30, above_horizon=True) == (PRICE_STATUS_SUN_UP, None, None)
    assert sun_band(now, None, None, 60, -30, above_horizon=False)[0] == PRICE_STATUS_SUN_DOWN


# ── Presets and advice ───────────────────────────────────────────────────

def test_presets_of_the_fixed_tariff():
    assert price_presets(DECISION_TARIFF, False) == {
        OPT_SUNRISE_MODE: MODE_SMART_CHARGE, OPT_SUNSET_MODE: MODE_SMART_MATCHING,
    }
    # Under net metering storing solar earns nothing: the battery rests.
    assert price_presets(DECISION_TARIFF, True) == {
        OPT_SUNRISE_MODE: MODE_STANDBY, OPT_SUNSET_MODE: MODE_STANDBY,
    }
    # The other decisions keep their band presets.
    assert OPT_SUNRISE_MODE not in price_presets(DECISION_BANDS, False)


def test_stop_charging_advice_follows_the_two_prices():
    # Fixed contract with feed-in costs: 1 cent of 25 -> about 48 W, so 50.
    assert stop_charge_advice(0.25, 0.01) == 50
    # Dynamic 2027, export a third of import: about 79 W, so 80.
    assert stop_charge_advice(0.30, 0.10) == 80
    # Export worth the import price: storing a small surplus never pays.
    assert stop_charge_advice(0.25, 0.25) is None
    assert stop_charge_advice(0, 0.01) is None


def test_the_status_knows_day_and_night():
    assert PRICE_STATUS_SUN_UP in PRICE_STATUSES and PRICE_STATUS_SUN_DOWN in PRICE_STATUSES


# ── Deciding ─────────────────────────────────────────────────────────────

class Settings:
    def __init__(self, **values):
        self.values = dict(values)
        self.written = []

    def get(self, key, default=None):
        return self.values.get(key, DEFAULTS.get(key, default))

    def get_float(self, key):
        return float(self.get(key) or 0)

    def get_int(self, key):
        return int(self.get(key) or 0)

    def get_bool(self, key):
        return bool(self.get(key))

    def set(self, key, value):
        self.values[key] = value
        self.written.append((key, value))


def make_tariff_pc(monkeypatch, now, net_metering=False, sun_state="below_horizon"):
    monkeypatch.setattr(P, "async_dispatcher_send", MagicMock())
    monkeypatch.setattr(P.dt_util, "now", lambda: now)
    monkeypatch.setattr(PriceControl, "_sun_times", lambda self, _now: (SUNRISE, SUNSET))
    pc = PriceControl.__new__(PriceControl)
    pc.group = False
    pc.virtual = False
    pc._battery_entry_id = "battery1"
    pc._battery = Settings(**{OPT_OPERATION_MODE: MODE_STANDBY})
    pc._settings = Settings(**{
        OPT_PRICE_DECISION: DECISION_TARIFF, OPT_NET_METERING: net_metering,
        OPT_FIXED_IMPORT_PRICE: 0.25, OPT_FIXED_EXPORT_PRICE: 0.01,
        OPT_SUNRISE_OFFSET: 60, OPT_SUNSET_OFFSET: -30,
    })
    pc.hass = SimpleNamespace(
        data={"zendure_restapi": {}},
        states=SimpleNamespace(get=lambda e: SimpleNamespace(state=sun_state) if e == "sun.sun" else None),
        config_entries=SimpleNamespace(async_get_entry=lambda entry_id: object()),
    )
    pc._signal = "signal"
    pc._entity = ""
    pc._enabled = True
    pc._last_target = None
    pc._generation = 0
    pc.sun_window = (None, None)
    return pc


def test_by_day_the_sunrise_mode_with_the_fixed_prices(monkeypatch):
    pc = make_tariff_pc(monkeypatch, SUNRISE + timedelta(hours=3))
    pc.evaluate()
    assert pc.status == PRICE_STATUS_SUN_UP
    assert pc._battery.written == [(OPT_OPERATION_MODE, MODE_SMART_CHARGE)]
    assert (pc.import_price, pc.export_value) == (0.25, 0.01)
    assert pc.plan is None and pc.price_curve == []
    attributes = pc.attributes
    assert attributes["decision"] == DECISION_TARIFF
    assert attributes["day_from"] == P.dt_util.as_local(SUNRISE + timedelta(hours=1)).strftime("%H:%M")
    assert attributes["day_until"] == P.dt_util.as_local(SUNSET - timedelta(minutes=30)).strftime("%H:%M")
    assert attributes["stop_charging_below_advice"] == "50 W"


def test_by_night_the_sunset_mode(monkeypatch):
    pc = make_tariff_pc(monkeypatch, SUNSET + timedelta(hours=1))
    pc.evaluate()
    assert pc.status == PRICE_STATUS_SUN_DOWN
    assert pc._battery.written == [(OPT_OPERATION_MODE, MODE_SMART_MATCHING)]


def test_under_net_metering_export_is_worth_the_import_price(monkeypatch):
    pc = make_tariff_pc(monkeypatch, SUNRISE + timedelta(hours=3), net_metering=True)
    pc.evaluate()
    assert pc.export_value == pc.import_price == 0.25
    assert pc.attributes["stop_charging_below_advice"] == "storing does not pay"


def test_the_minute_timer_only_acts_for_the_fixed_tariff(monkeypatch):
    pc = make_tariff_pc(monkeypatch, SUNRISE + timedelta(hours=3))
    calls = []
    monkeypatch.setattr(PriceControl, "evaluate", lambda self: calls.append(1))
    pc._on_minute(None)
    pc._settings.values[OPT_PRICE_DECISION] = DECISION_PLAN
    pc._on_minute(None)
    assert calls == [1]


def test_after_the_evening_switch_the_heading_shows_the_next_day(monkeypatch):
    pc = make_tariff_pc(monkeypatch, SUNSET + timedelta(hours=1))
    tomorrow = (SUNRISE + timedelta(days=1, minutes=2), SUNSET + timedelta(days=1, minutes=-2))
    monkeypatch.setattr(PriceControl, "_sun_times", lambda self, when: (
        tomorrow if when.date() > SUNSET.date() else (SUNRISE, SUNSET)))
    pc.evaluate()
    assert pc.status == PRICE_STATUS_SUN_DOWN
    attributes = pc.attributes
    # Sunrise left, sunset right as always, but of tomorrow: never a window that is over.
    assert attributes["day_from"] == P.dt_util.as_local(tomorrow[0] + timedelta(hours=1)).strftime("%H:%M")
    assert attributes["day_until"] == P.dt_util.as_local(tomorrow[1] - timedelta(minutes=30)).strftime("%H:%M")


def test_by_day_and_before_sunrise_the_heading_shows_today(monkeypatch):
    for now in (SUNRISE - timedelta(hours=2), SUNRISE + timedelta(hours=3)):
        pc = make_tariff_pc(monkeypatch, now)
        pc.evaluate()
        assert pc.attributes["day_from"] == P.dt_util.as_local(SUNRISE + timedelta(hours=1)).strftime("%H:%M")
