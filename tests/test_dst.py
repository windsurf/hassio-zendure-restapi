"""The day plan across the change of clocks (v1.6.10).

The night the clocks go back has 25 hours (100 quarters), the night they go
forward 23 hours (92 quarters). Nord Pool gives its times as datetimes in Home
Assistant's time zone; EPEX Spot, Zonneplan and others as text with an offset.
Both must give every quarter once, each a quarter of an hour long, and the
periods still ahead must follow the real moment, also inside the repeated hour.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from custom_components.zendure_restapi.dayplan import ahead, read_prices

AMS = ZoneInfo("Europe/Amsterdam")
AUTUMN = datetime(2026, 10, 24, 22, 0, tzinfo=timezone.utc)   # 25 Oct, local midnight
SPRING = datetime(2027, 3, 27, 23, 0, tzinfo=timezone.utc)    # 28 Mar, local midnight


def quarters(start: datetime, n: int) -> list[tuple[datetime, datetime]]:
    return [
        ((start + timedelta(minutes=15 * i)).astimezone(AMS),
         (start + timedelta(minutes=15 * (i + 1))).astimezone(AMS))
        for i in range(n)
    ]


def nord_pool(start: datetime, n: int) -> dict:
    return {"raw_today": [
        {"start": s, "end": e, "value": 0.10 + 0.001 * i}
        for i, (s, e) in enumerate(quarters(start, n))
    ]}


def as_text(start: datetime, n: int) -> dict:
    return {"data": [
        {"start_time": s.isoformat(), "end_time": e.isoformat(), "price_per_kwh": 0.10 + 0.001 * i}
        for i, (s, e) in enumerate(quarters(start, n))
    ]}


def check(attributes: dict, n: int) -> list:
    periods = read_prices(attributes)
    assert len(periods) == n
    assert all(abs(p.hours - 0.25) < 1e-9 for p in periods)
    assert [p.value for p in periods] == sorted(p.value for p in periods)
    return periods


def test_clocks_back_nord_pool_keeps_all_100_quarters():
    periods = check(nord_pool(AUTUMN, 100), 100)
    second_two = datetime(2026, 10, 25, 1, 10, tzinfo=timezone.utc).astimezone(AMS)  # 02:10 winter time
    left = ahead(periods, second_two)
    assert len(left) == 88 and abs(left[0].hours - 5 / 60) < 1e-9


def test_clocks_back_text_keeps_all_100_quarters():
    check(as_text(AUTUMN, 100), 100)


def test_clocks_forward_nord_pool_has_no_long_quarter():
    check(nord_pool(SPRING, 92), 92)


def test_clocks_forward_text_has_92_quarters():
    check(as_text(SPRING, 92), 92)
