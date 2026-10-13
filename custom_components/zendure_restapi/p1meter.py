"""External P1 readers as a second grid meter for the controller.

Why a second meter at all. Measured through a splitter on one P1 port, a
YouLess LS120 and the Zendure P1 meter read the same connection point equally
accurately and refresh equally often, once per P1 telegram. They differ in one
respect that matters for control: the Zendure meter reports every load step
0.4 to 1.1 s after the YouLess does. Cross-correlation over some 1,300 paired
samples put the best match at one to two samples of shift, in both runs, one
of them with the controller in standby. The lag is a property of the Zendure
meter, not of the loop — and it means any reader that passes the P1 telegram
on promptly is a better feedback signal than the meter this integration was
built around.

Why this module polls the reader itself rather than reading a Home Assistant
entity. The YouLess integration that ships with Home Assistant refreshes every
ten seconds: ten telegrams in, nine thrown away, and the one that survives is
whatever the poll landed on — the exact fault the trim loop was built to
remove. A meter that is faster but read ten times less often is slower.

Why it polls four times a second while the telegram arrives once a second.
Polling at the telegram rate leaves the poll and the telegram unsynchronised,
so each telegram is seen anywhere from 0 to 1 s after it arrived, half a second
on average — the same order as the lag being won back. At 250 ms that falls to
0 to 0.25 s. The YouLess answers in 23 ms on average, 31 ms at worst, with no
failures over 30 consecutive requests, and four polls a second was the rate the
measurements themselves were taken at.

Downstream sees one sample per telegram, not per poll. Listeners fire only when
the adapter's telegram stamp moves, so the trim loop and the trace still run at
the meter's own rate of once a second, and a poll that returns the same
telegram again is not a new measurement. The stamp is also the freshness test:
`updated_at` is the moment a new telegram was first seen, not the moment of the
last successful request.

Adding a reader is adding an adapter: a subclass of ExternalMeter with a path
and a parser that returns the telegram stamp, the net power in W with import
positive, and whatever else is worth recording. A reader without a telegram
stamp of its own returns None as the stamp; every successful poll then counts
as a new reading, and `min_interval_ms` keeps it at the telegram rate.

Readers since v1.5.1: HomeWizard P1 (API v1), YouLess LS120, EcoTracker,
Shelly Pro 3EM, and the Zendure P1 meter or Smart Meter 3CT. Endpoints and
fields follow each maker's documentation; only the YouLess and the Zendure
P1 meter have been measured by the author.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant, callback
from homeassistant.util import dt as dt_util

from .const import (
    EXTERNAL_STATUS_LIVE,
    EXTERNAL_STATUS_STALE,
    EXTERNAL_STATUS_UNREACHABLE,
    METER_FALLBACK_AGE,
    EXTERNAL_BACKOFF_AFTER,
    EXTERNAL_BACKOFF_SECONDS,
    EXTERNAL_TIMEOUT,
    EXTERNAL_TYPE_ECOTRACKER,
    EXTERNAL_TYPE_HOMEWIZARD_V1,
    EXTERNAL_TYPE_SHELLY_PRO_3EM,
    EXTERNAL_TYPE_YOULESS,
    EXTERNAL_TYPE_ZENDURE,
)

_LOGGER = logging.getLogger(__name__)


# Marks "no telegram seen since the poller started".
_UNSEEN = object()


class ExternalMeter:
    """Polls one P1 reader and notifies on every new telegram.

    Exposes what the controller reads from the Zendure meter coordinator —
    ``hass``, ``data`` and ``updated_at`` — so both meters are judged by one
    set of rules. ``data["power"]`` is the net grid power in W, import
    positive, as reported.
    """

    kind = "generic"
    path = "/"
    manufacturer = "unknown"
    # Floor under the poll interval. A reader without a telegram stamp of its
    # own cannot tell a new telegram from the same one fetched again, so every
    # poll counts as a reading; polled faster than the telegram it would hand
    # the trim loop the same measurement several times. One second is the DSMR 5
    # telegram rate.
    min_interval_ms = 1000

    def __init__(
        self,
        hass: HomeAssistant,
        session: aiohttp.ClientSession,
        host: str,
        interval_ms: int,
    ) -> None:
        self.hass = hass
        self.host = host
        self.interval = max(interval_ms, self.min_interval_ms) / 1000.0
        self._session = session
        self._url = f"http://{host}{self.path}"

        self.data: dict[str, Any] = {}
        self.updated_at: datetime | None = None
        self.last_poll_ok: bool = False
        self.polls = 0
        self.telegrams = 0
        self.failures = 0

        self._failures_in_row = 0
        self._last_stamp: Any = _UNSEEN
        self._listeners: list[Callable[[], None]] = []
        self._task: asyncio.Task | None = None
        # The config entry this meter belongs to, set on setup.
        self.entry_id: str | None = None

    # ── Adapter ──────────────────────────────────────────────────────────

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        """Return (telegram stamp or None, power in W, extra fields).

        Raises ValueError when the payload is unusable.
        """
        raise NotImplementedError

    def energy(self) -> tuple[float | None, float | None]:
        """Cumulative import and export in kWh, where the reader has them."""
        return None, None

    @property
    def status(self) -> str:
        """live, stale or unreachable — the same 5 s rule `auto` applies."""
        if self._failures_in_row >= EXTERNAL_BACKOFF_AFTER:
            return EXTERNAL_STATUS_UNREACHABLE
        if self.recent_power(METER_FALLBACK_AGE) is None:
            return EXTERNAL_STATUS_STALE
        return EXTERNAL_STATUS_LIVE

    def telegram_age(self) -> float | None:
        """Seconds since the last new telegram, None before the first."""
        if self.updated_at is None:
            return None
        return round((dt_util.utcnow() - self.updated_at).total_seconds(), 1)

    async def async_probe(self) -> dict[str, Any]:
        """One request, for the config flow: the parsed reading or an error.

        Raises ValueError, TimeoutError or aiohttp.ClientError when the
        address does not answer as this reader type does.
        """
        async with asyncio.timeout(EXTERNAL_TIMEOUT):
            async with self._session.get(self._url) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                payload = await response.json(content_type=None)
        stamp, power, extra = self.parse(payload)
        return {"stamp": stamp, "power": power, **extra}

    # ── Listeners ────────────────────────────────────────────────────────

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to new telegrams. Returns the unsubscribe callable."""
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return _remove

    # ── Lifecycle ────────────────────────────────────────────────────────

    @callback
    def async_start(self) -> None:
        """Start polling in the background."""
        if self._task is not None:
            return
        self._task = self.hass.async_create_background_task(
            self._async_loop(), name=f"zendure_restapi {self.kind} {self.host}"
        )
        _LOGGER.info(
            "External meter (%s) at %s polled every %.0f ms",
            self.kind, self.host, self.interval * 1000,
        )

    @callback
    def async_stop(self) -> None:
        """Stop polling. Safe to call more than once."""
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self._listeners.clear()

    # ── Polling ──────────────────────────────────────────────────────────

    async def _async_loop(self) -> None:
        # Paced on the monotonic clock from the start of each request, so the
        # request time is absorbed rather than added to the interval.
        while True:
            started = time.monotonic()
            await self._async_poll()

            if self._failures_in_row >= EXTERNAL_BACKOFF_AFTER:
                # An address that does not answer is not worth four requests a
                # second. The controller has already stopped trusting this
                # meter by now, so a slower retry costs nothing.
                pause = EXTERNAL_BACKOFF_SECONDS
            else:
                pause = self.interval - (time.monotonic() - started)
            await asyncio.sleep(max(0.0, pause))

    async def _async_poll(self) -> None:
        self.polls += 1
        try:
            async with asyncio.timeout(EXTERNAL_TIMEOUT):
                async with self._session.get(self._url) as response:
                    if response.status != 200:
                        raise ValueError(f"HTTP {response.status}")
                    payload = await response.json(content_type=None)
            stamp, power, extra = self.parse(payload)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError,
                KeyError, IndexError) as err:
            self._failed(err)
            return

        if self._failures_in_row:
            _LOGGER.info(
                "External meter at %s answering again after %d failed polls",
                self.host, self._failures_in_row,
            )
        self._failures_in_row = 0
        self.last_poll_ok = True

        # Same telegram as last time: not a new measurement.
        if stamp is not None and stamp == self._last_stamp:
            return

        # The first telegram after a start proves only that the reader answers,
        # not that its data is current: its stamp has nothing to move away
        # from yet. Seen on this installation on 13 October 2026 — a YouLess
        # whose P1 cable had been out since 22 August kept answering with that
        # morning's `tm` and a fixed `pwr`. Accepted as new, that reading would
        # have steered the battery for the first seconds after every restart.
        # So a stamped reader counts as live from the first stamp that moves.
        # It is kept for the diagnostics, without an age and without listeners.
        if stamp is not None and self._last_stamp is _UNSEEN:
            self._last_stamp = stamp
            self.data = {"stamp": stamp, "power": power, **extra}
            return
        self._last_stamp = stamp
        self.data = {"stamp": stamp, "power": power, **extra}
        self.updated_at = dt_util.utcnow()
        self.telegrams += 1

        for listener in list(self._listeners):
            listener()

    def _failed(self, err: Exception) -> None:
        self.failures += 1
        self._failures_in_row += 1
        self.last_poll_ok = False
        if self._failures_in_row == 1:
            _LOGGER.debug("External meter poll at %s failed: %s", self.host, err)
        elif self._failures_in_row == EXTERNAL_BACKOFF_AFTER:
            _LOGGER.warning(
                "External meter (%s) at %s not answering (%s); retrying every %ss",
                self.kind, self.host, err, EXTERNAL_BACKOFF_SECONDS,
            )

    # ── Reporting ────────────────────────────────────────────────────────

    def recent_power(self, max_age: float) -> Any:
        """The last reported power if its telegram is younger than max_age.

        For recording only. A reading that is not current reads as missing,
        so a stale value cannot sit in the trace looking like a measurement.
        """
        if self.updated_at is None:
            return None
        if (dt_util.utcnow() - self.updated_at).total_seconds() > max_age:
            return None
        return self.data.get("power")

    def diagnostics(self) -> dict[str, Any]:
        """Counters and the last telegram, for the diagnostics download."""
        age = self.telegram_age()
        return {
            "type": self.kind,
            "status": self.status,
            "interval_ms": round(self.interval * 1000),
            "polls": self.polls,
            "telegrams": self.telegrams,
            "failures": self.failures,
            "last_poll_ok": self.last_poll_ok,
            "telegram_age_s": age,
            "last_telegram": dict(self.data),
        }


class YoulessMeter(ExternalMeter):
    """YouLess LS120, endpoint ``/e?f=j``.

    The endpoint returns a one-element JSON list. `/p?f=j` was measured
    identical on 240 of 240 samples, so there is no faster endpoint to prefer.

    `tm` is the telegram time and the freshness test. With the P1 cable pulled
    the device was seen to keep `pwr` moving while `tm` and `net` stood still —
    the reading then comes from something other than the smart meter, so a
    `tm` that does not move makes the reading old however recently it was
    fetched.

    `pwr` is net power with import positive, the same convention as the
    Zendure meter. The four OBIS registers `p1` `p2` (import, low and normal
    tariff) and `n1` `n2` (export) are cumulative kWh at 1 Wh resolution;
    `p1 + p2 - n1 - n2` equals `net` to the Wh. They are recorded, not yet used.
    """

    manufacturer = "YouLess"
    kind = EXTERNAL_TYPE_YOULESS
    path = "/e?f=j"
    min_interval_ms = 100      # `tm` tells new telegrams apart; poll freely

    _EXTRA = ("net", "p1", "p2", "n1", "n2")

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        if isinstance(payload, list):
            payload = payload[0] if payload else None
        if not isinstance(payload, dict):
            raise ValueError("unexpected payload")
        stamp = payload.get("tm")
        if stamp in (None, ""):
            # Without tm a new telegram cannot be told from an old one.
            raise ValueError("payload without tm")
        power = float(payload["pwr"])
        return stamp, power, {key: payload.get(key) for key in self._EXTRA}

    def energy(self) -> tuple[float | None, float | None]:
        """Import p1 + p2 and export n1 + n2. Empty while the P1 port is silent."""
        def total(a: str, b: str) -> float | None:
            try:
                return round(float(self.data[a]) + float(self.data[b]), 3)
            except (KeyError, TypeError, ValueError):
                return None
        return total("p1", "p2"), total("n1", "n2")


def _number(payload: Any, key: str) -> float:
    """A required numeric field, or ValueError."""
    if not isinstance(payload, dict) or payload.get(key) in (None, ""):
        raise ValueError(f"payload without {key}")
    return float(payload[key])


class HomeWizardV1Meter(ExternalMeter):
    """HomeWizard P1 meter, local API v1: ``/api/v1/data``.

    The local API has to be switched on in the HomeWizard Energy app. Fields per
    HomeWizard's API documentation: `active_power_w` is net power with import
    positive; the cumulative registers are kept for the diagnostics. The v1
    payload carries no time of the telegram, so this reader is polled once a
    second. API v2 does carry one, and a websocket besides; it needs a token
    and HTTPS and is planned separately.
    """

    manufacturer = "HomeWizard"
    kind = EXTERNAL_TYPE_HOMEWIZARD_V1
    path = "/api/v1/data"

    _EXTRA = ("total_power_import_kwh", "total_power_export_kwh", "active_tariff")

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        power = _number(payload, "active_power_w")
        return None, power, {key: payload.get(key) for key in self._EXTRA}

    def energy(self) -> tuple[float | None, float | None]:
        def value(key: str) -> float | None:
            try:
                return round(float(self.data[key]), 3)
            except (KeyError, TypeError, ValueError):
                return None
        return value("total_power_import_kwh"), value("total_power_export_kwh")


class EcoTrackerMeter(ExternalMeter):
    """EcoTracker by everHome: ``/v1/json``, field `power`.

    The local HTTP server has to be switched on in the EcoTracker app.
    """

    manufacturer = "everHome"
    kind = EXTERNAL_TYPE_ECOTRACKER
    path = "/v1/json"

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        return None, _number(payload, "power"), {}


class ShellyPro3EMMeter(ExternalMeter):
    """Shelly Pro 3EM, current clamps: ``/rpc/EM.GetStatus?id=0``.

    `total_act_power` is the sum of the three phases. A clamp meter measures
    where its clamps are, so they have to sit on the grid connection for this
    to be the grid reading.
    """

    manufacturer = "Shelly"
    kind = EXTERNAL_TYPE_SHELLY_PRO_3EM
    path = "/rpc/EM.GetStatus?id=0"

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        return None, _number(payload, "total_act_power"), {}


class ZendureMeterExternal(ExternalMeter):
    """Zendure P1 meter or Smart Meter 3CT, read as an external meter.

    Same endpoint and field as the meter entry this integration has always
    had: ``/properties/report``, `total_power`. Offered here as well because
    the Smart Meter 3CT has never been seen by this integration and may not
    carry the `meterType` marker the meter entry is recognised by; read this
    way it needs nothing but the power field.
    """

    manufacturer = "Zendure"
    kind = EXTERNAL_TYPE_ZENDURE
    path = "/properties/report"

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        return None, _number(payload, "total_power"), {}


ADAPTERS: dict[str, type[ExternalMeter]] = {
    adapter.kind: adapter
    for adapter in (
        HomeWizardV1Meter,
        YoulessMeter,
        EcoTrackerMeter,
        ShellyPro3EMMeter,
        ZendureMeterExternal,
    )
}
