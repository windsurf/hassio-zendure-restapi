"""A battery at the edge of the WiFi: dropped requests are retried and logged,
answers the device gave are not, and a dropped poll keeps the last report for
a short while instead of turning every entity unavailable."""

import asyncio
import logging
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.zendure_restapi import api as A
from custom_components.zendure_restapi import controller as C
from custom_components.zendure_restapi import coordinator as CO

from .helpers import make_controller, make_coordinator


class Response:
    def __init__(self, status=200, text='{"code":200}'):
        self.status = status
        self._text = text

    async def text(self):
        return self._text


class Session:
    """Answers each request with the next outcome: a Response or an exception."""

    def __init__(self, *outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    async def _next(self, method, url, **kw):
        self.calls.append((method, url, kw.get("json")))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    async def get(self, url, **kw):
        return await self._next("GET", url, **kw)

    async def post(self, url, **kw):
        return await self._next("POST", url, **kw)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(A, "RETRY_DELAYS", (0, 0))


def api(*outcomes):
    session = Session(*outcomes)
    return A.ZendureLocalApi(session, "192.0.2.1", sn="SN1"), session


def test_a_dropped_write_is_retried_and_logged(caplog):
    client, session = api(aiohttp.ClientConnectionError("reset"), TimeoutError(), Response())
    with caplog.at_level(logging.INFO, logger=A.__name__):
        asyncio.run(client.async_write_property("outputLimit", 400))
    assert len(session.calls) == 3
    assert all(call[2] == {"sn": "SN1", "properties": {"outputLimit": 400}} for call in session.calls)
    retries = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(retries) == 2 and "outputLimit=400" in retries[0].getMessage()
    assert "attempt 2 of 3" in retries[0].getMessage()
    assert any("succeeded on attempt 3 of 3" in r.getMessage() for r in caplog.records)


def test_a_write_gives_up_after_its_attempts():
    client, session = api(*[aiohttp.ClientConnectionError("gone")] * A.WRITE_ATTEMPTS)
    with pytest.raises(A.ZendureConnectionError, match="after 3 attempts"):
        asyncio.run(client.async_write_properties({"inputLimit": 0, "outputLimit": 0}))
    assert len(session.calls) == A.WRITE_ATTEMPTS


def test_a_cut_off_or_empty_body_is_retried():
    client, session = api(Response(text='{"code":2'), Response(text=""), Response())
    asyncio.run(client.async_write_property("acMode", 2))
    assert len(session.calls) == 3


def test_an_answer_the_device_gave_is_not_retried():
    client, session = api(Response(status=400), Response())
    with pytest.raises(A.ZendureApiError) as raised:
        asyncio.run(client.async_write_properties({"acMode": 1, "inputLimit": 300}))
    assert not isinstance(raised.value, A.ZendureConnectionError)
    assert len(session.calls) == 1


def test_the_report_gets_one_quick_retry():
    client, session = api(TimeoutError(), Response(text='{"sn":"SN1","properties":{}}'))
    assert asyncio.run(client.async_get_report())["sn"] == "SN1"
    client, session = api(TimeoutError(), TimeoutError(), Response())
    with pytest.raises(A.ZendureConnectionError):
        asyncio.run(client.async_get_report())
    assert len(session.calls) == A.READ_ATTEMPTS


def test_a_dropped_poll_keeps_the_last_report_for_a_while():
    co = make_coordinator()
    co.data = {"electricLevel": 50}
    co.updated_at = None
    co.api.async_get_report = AsyncMock(side_effect=A.ZendureConnectionError("timeout"))
    for n in range(1, CO.POLL_FAILURE_GRACE + 1):
        assert asyncio.run(co._async_update_data()) == {"electricLevel": 50}
        assert co.failed_polls == n
    # updated_at was not touched: the age checks still see an old report.
    assert co.updated_at is None
    with pytest.raises(UpdateFailed):
        asyncio.run(co._async_update_data())
    # The first failure is when the device went quiet, for the restart record.
    assert co._failing_since is not None

    co.api.async_get_report = AsyncMock(return_value={"sn": "SN1", "properties": {"electricLevel": 51}})
    assert asyncio.run(co._async_update_data())["electricLevel"] == 51
    assert co.failed_polls == 0 and co.updated_at is not None


def test_without_a_report_yet_a_failed_poll_fails():
    co = make_coordinator()
    co.api.async_get_report = AsyncMock(side_effect=A.ZendureConnectionError("timeout"))
    with pytest.raises(UpdateFailed):
        asyncio.run(co._async_update_data())


def test_a_meter_in_its_grace_is_not_trimmed_against():
    c = make_controller(C.MODE_SMART_MATCHING)
    c.meter = make_coordinator()
    c.meter.last_update_success = True
    c.meter.failed_polls = 1
    c.meter.data = {"total_power": 300}
    assert c._zendure_power() is None


def test_a_link_failure_on_a_multi_write_does_not_drop_multi_writes():
    c = make_controller(C.MODE_SMART_MATCHING)
    c.coordinator.api.async_write_properties.side_effect = A.ZendureConnectionError("timeout")
    data = {"acMode": 1, "inputLimit": 0, "outputLimit": 0}
    with pytest.raises(A.ZendureConnectionError):
        asyncio.run(c._write_many([("acMode", 2), ("outputLimit", 300)], data))
    assert c._multi_write is True
    c.coordinator.api.async_write_property.assert_not_awaited()
