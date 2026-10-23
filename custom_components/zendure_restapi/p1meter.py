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
from collections import deque
from collections.abc import Callable
from datetime import datetime
from statistics import median
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
    EXTERNAL_KIND_LABELS,
    EXTERNAL_TYPE_ECOTRACKER,
    EXTERNAL_TYPE_HA_SENSOR,
    EXTERNAL_TYPE_HOMEWIZARD_V1,
    EXTERNAL_TYPE_SHELLY_PRO_3EM,
    EXTERNAL_TYPE_YOULESS,
    EXTERNAL_TYPE_ZENDURE,
)
from .hadevice import MeterPlan

_LOGGER = logging.getLogger(__name__)


# Marks "no telegram seen since the poller started".
_UNSEEN = object()

# The update interval is the median gap over the last readings, from this many
# on: enough to ride out one late reading, few enough to follow a change.
UPDATE_SAMPLES = 30
UPDATE_MIN_SAMPLES = 10
# A meter that reports every few minutes would need most of an hour for ten
# gaps; two gaps spanning a minute already show it is far too slow to steer.
UPDATE_SLOW_SAMPLES = 2
UPDATE_SLOW_SPAN = 60.0         # s


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
        # Seconds between new readings, for the update interval (v1.6.5).
        self._gaps: deque[float] = deque(maxlen=UPDATE_SAMPLES)
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

    @property
    def update_interval(self) -> float | None:
        """Median seconds between new readings, once there are enough of them.

        What the meter really delivers, whatever it is polled at: a Home
        Assistant sensor is only as fast as its own integration (v1.6.5).
        """
        gaps = len(self._gaps)
        enough = gaps >= UPDATE_MIN_SAMPLES or (
            gaps >= UPDATE_SLOW_SAMPLES and sum(self._gaps) >= UPDATE_SLOW_SPAN
        )
        if not enough:
            return None
        return round(median(self._gaps), 2)

    def telegram_age(self) -> float | None:
        """Seconds since the last new telegram, None before the first."""
        if self.updated_at is None:
            return None
        return round((dt_util.utcnow() - self.updated_at).total_seconds(), 1)

    async def _async_fetch(self) -> Any:
        """One request to the reader; the payload for parse()."""
        async with asyncio.timeout(EXTERNAL_TIMEOUT):
            async with self._session.get(self._url) as response:
                if response.status != 200:
                    raise ValueError(f"HTTP {response.status}")
                return await response.json(content_type=None)

    async def async_probe(self) -> dict[str, Any]:
        """One request, for the config flow: the parsed reading or an error.

        Raises ValueError, TimeoutError or aiohttp.ClientError when the
        address does not answer as this reader type does.
        """
        stamp, power, extra = self.parse(await self._async_fetch())
        return {"stamp": stamp, "power": power, **extra}

    @property
    def label(self) -> str:
        """How the External meter select names this meter."""
        return f"{EXTERNAL_KIND_LABELS.get(self.kind, self.kind)} ({self.host})"

    @property
    def configuration_url(self) -> str | None:
        return f"http://{self.host}"

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
            stamp, power, extra = self.parse(await self._async_fetch())
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
        # from yet. Seen on this installation — a YouLess whose P1 cable had
        # been out for weeks kept answering with the `tm` of the moment it was
        # unplugged and a fixed `pwr`. Accepted as new, that reading would
        # have steered the battery for the first seconds after every restart.
        # So a stamped reader counts as live from the first stamp that moves.
        # It is kept for the diagnostics, without an age and without listeners.
        if stamp is not None and self._last_stamp is _UNSEEN:
            self._last_stamp = stamp
            self.data = {"stamp": stamp, "power": power, **extra}
            return
        self._last_stamp = stamp
        self.data = {"stamp": stamp, "power": power, **extra}
        now = dt_util.utcnow()
        if self.updated_at is not None:
            self._gaps.append((now - self.updated_at).total_seconds())
        self.updated_at = now
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
            "update_interval_s": self.update_interval,
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
    had: ``/properties/report``, `total_power`. The Smart Meter 3CT reports no
    `meterType` (issue #7); since v1.6.7 the meter entry recognises it by its
    fields. Read this way it needs nothing but the power field.
    """

    manufacturer = "Zendure"
    kind = EXTERNAL_TYPE_ZENDURE
    path = "/properties/report"

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        return None, _number(payload, "total_power"), {}


_POWER_SCALE = {"W": 1.0, "kW": 1000.0, "MW": 1_000_000.0}


def state_power(state: Any) -> float:
    """Power in W from a Home Assistant state, or ValueError.

    Accepts W, kW and MW; anything else — no unit, an energy unit, a state
    that is unknown or unavailable — is not a power reading.
    """
    if state is None:
        raise ValueError("entity not found")
    unit = state.attributes.get("unit_of_measurement")
    if unit not in _POWER_SCALE:
        raise ValueError(f"unit {unit!r} is not a power unit")
    try:
        return float(state.state) * _POWER_SCALE[unit]
    except (TypeError, ValueError) as err:
        raise ValueError(f"state {state.state!r} is not a number") from err


class HaSensorMeter(ExternalMeter):
    """Power sensors already in Home Assistant, read as an external meter.

    For meters this integration has no adapter for: a Zonneplan Connect, a DSMR
    reader, a Shelly other than the Pro 3EM, an inverter's grid meter. Whether
    the result can steer depends on how often those sensors are updated, not
    on this adapter. A sensor refreshed every ten seconds, as the YouLess
    integration of Home Assistant does, let alone every five minutes as the
    Zonneplan cloud does, reads as `stale` between updates, and `auto` then
    uses the Zendure meter. It is still worth having on the dashboard; the
    update interval shows how fast it really is.

    Since v1.6.5 the meter is a plan (hadevice.MeterPlan): the terms of the
    total with their signs, the phases, optionally a direction sensor and an
    inverted sign. An entry from before v1.6.5 holds one power sensor, import
    positive, and optionally an export sensor; it reads as the plan
    [(power, +1), (export, -1)].

    The telegram stamp is the sensors' `last_reported`: it moves on every
    report, also when the value is unchanged, so a steady reading is not
    mistaken for a frozen one.
    """

    manufacturer = "Home Assistant"
    kind = EXTERNAL_TYPE_HA_SENSOR
    path = ""
    # The state is read from memory and carries its own stamp.
    min_interval_ms = 100

    def __init__(self, hass, session, host, interval_ms) -> None:
        super().__init__(hass, session, host, interval_ms)
        self.export_entity: str | None = None
        self.plan: MeterPlan | None = None
        # How the meter is named in the External meter select: the device,
        # or the sensor for an entry of one sensor.
        self.name: str | None = None

    def _plan(self) -> MeterPlan:
        if self.plan is not None:
            return self.plan
        terms = [(self.host, 1)]
        if self.export_entity:
            terms.append((self.export_entity, -1))
        return MeterPlan(terms=terms, how="sensor")

    async def _async_fetch(self) -> Any:
        plan = self._plan()
        get = self.hass.states.get
        return {
            "terms": [(get(entity_id), sign) for entity_id, sign in plan.total_terms()],
            "phases": [(get(entity_id), sign, phase) for entity_id, sign, phase in plan.phases],
            "direction": (get(plan.direction[0]), plan.direction[1]) if plan.direction else None,
            "invert": plan.invert,
            "split": plan.how == "sensor" and len(plan.terms) == 2,
        }

    def parse(self, payload: Any) -> tuple[Any, float, dict[str, Any]]:
        if isinstance(payload, list):
            # The shape before v1.6.5: [power state, export state?].
            payload = {
                "terms": [(payload[0], 1)] + ([(payload[1], -1)] if len(payload) > 1 else []),
                "phases": [], "direction": None, "invert": False, "split": len(payload) > 1,
            }
        terms = payload["terms"]
        if not terms:
            raise ValueError("no sensors to read")
        power = 0.0
        stamp = []
        values = []
        for state, sign in terms:
            value = state_power(state)
            values.append(value)
            power += sign * value
            stamp.append(_reported(state))
        extra: dict[str, Any] = {}
        if payload.get("split"):
            # One import and one export sensor, both positive, as before v1.6.5.
            extra["import_w"] = values[0]
            extra["export_w"] = values[1]
        direction = payload.get("direction")
        if direction is not None:
            state, export = direction
            if state is None:
                raise ValueError("direction sensor not found")
            stamp.append(_reported(state))
            if str(state.state).lower() == str(export).lower():
                power = -abs(power)
        if payload.get("invert"):
            power = -power
        for state, sign, phase in payload.get("phases") or []:
            try:
                value = sign * state_power(state)
            except ValueError:
                continue
            key = f"phase_{phase}_w"
            extra[key] = round(extra.get(key, 0.0) + (-value if payload.get("invert") else value), 1)
        return tuple(stamp), round(power, 1), extra

    @property
    def label(self) -> str:
        return f"{EXTERNAL_KIND_LABELS[self.kind]} ({self.name or self.host})"

    @property
    def configuration_url(self) -> str | None:
        return None


def _reported(state: Any) -> Any:
    return getattr(state, "last_reported", None) or state.last_updated


ADAPTERS: dict[str, type[ExternalMeter]] = {
    adapter.kind: adapter
    for adapter in (
        HomeWizardV1Meter,
        YoulessMeter,
        EcoTrackerMeter,
        ShellyPro3EMMeter,
        ZendureMeterExternal,
        HaSensorMeter,
    )
}
