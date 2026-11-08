"""Local HTTP client for the Zendure zenSDK RESTful API.

The device exposes a plain HTTP server on the LAN:

    GET  /properties/report          -> all current properties
    POST /properties/write           -> write properties (body must carry "sn")
    GET  /rpc?method=HA.Mqtt.*       -> RPC query
    POST /rpc                        -> RPC command

Two hard constraints from the zenSDK documentation drive this module:

1. The local API receive buffer is 512 bytes. Writes are therefore issued one
   property at a time and the encoded body length is verified before sending.
2. Under EN 18031 the HTTP server is disabled by default. The device only
   answers once the local API has been enabled (add HEMS, then exit).
"""

from __future__ import annotations

import asyncio

import json
import logging
from typing import Any

import aiohttp

from .const import (
    HTTP_TIMEOUT,
    MAX_WRITE_BODY_BYTES,
    PATH_REPORT,
    PATH_RPC,
    PATH_WRITE,
    READ_ATTEMPTS,
    RETRY_DELAYS,
    RPC_MQTT_GET_CONFIG,
    RPC_MQTT_STATUS,
    WRITE_ATTEMPTS,
)

_LOGGER = logging.getLogger(__name__)


class ZendureApiError(Exception):
    """Raised when the device cannot be reached or answers unusably."""


class ZendureConnectionError(ZendureApiError):
    """Raised when a request got no usable answer: timeout, dropped connection,
    empty or cut-off body. Worth trying again, unlike an answer the device gave."""


class ZendureBodyTooLarge(ZendureApiError):
    """Raised when a write body would exceed the 512 byte receive limit."""


class ZendureLocalApi:
    """Thin async wrapper around one device's local HTTP server."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int = 80,
        sn: str | None = None,
    ) -> None:
        self._session = session
        self._host = host
        self._port = port
        self._sn = sn

    @property
    def sn(self) -> str | None:
        """Serial number, learned from the first report when not configured."""
        return self._sn

    @sn.setter
    def sn(self, value: str | None) -> None:
        self._sn = value

    @property
    def base_url(self) -> str:
        """Base URL of the device, without trailing slash."""
        if self._port in (80, None):
            return f"http://{self._host}"
        return f"http://{self._host}:{self._port}"

    # ── Read ─────────────────────────────────────────────────────────────

    async def async_get_report(self) -> dict[str, Any]:
        """Fetch all device properties.

        Returns the raw decoded JSON. Normalisation into a flat key/value map
        is the coordinator's job, because the payload envelope differs between
        firmware revisions.
        """
        return await self._request("GET", PATH_REPORT, attempts=READ_ATTEMPTS)

    async def async_get_mqtt_status(self) -> dict[str, Any]:
        """Query the device's MQTT client status."""
        return await self._request("GET", f"{PATH_RPC}?method={RPC_MQTT_STATUS}")

    async def async_get_mqtt_config(self) -> dict[str, Any]:
        """Query the device's MQTT client configuration."""
        return await self._request("GET", f"{PATH_RPC}?method={RPC_MQTT_GET_CONFIG}")

    # ── Write ────────────────────────────────────────────────────────────

    async def async_write_property(self, key: str, value: Any) -> dict[str, Any]:
        """Write exactly one property.

        One property per request for a write that stands on its own: a body
        that silently truncates at the 512 byte receive limit is effectively
        undebuggable. Writes that belong together go through
        async_write_properties.
        """
        if not self._sn:
            raise ZendureApiError("serial number unknown, cannot write")

        payload = {"sn": self._sn, "properties": {key: value}}
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_WRITE_BODY_BYTES:
            raise ZendureBodyTooLarge(
                f"write body is {len(encoded)} bytes, limit is {MAX_WRITE_BODY_BYTES}"
            )

        _LOGGER.debug("Write %s=%s to %s", key, value, self.base_url)
        return await self._request("POST", PATH_WRITE, payload, attempts=WRITE_ATTEMPTS)

    async def async_write_properties(self, properties: dict[str, Any]) -> dict[str, Any]:
        """Write several properties that belong together in one request (v1.6.4).

        Used only for writes that make one change together: a direction
        change (flash flag, the other limit, acMode, the limit) or a stop
        (both limits). zenSDK takes several properties in one body; Zendure's
        own integration drives every SolarFlow that way. The body is checked
        against the 512 byte receive limit like a single write, so it can
        never be truncated, and the caller falls back to one property per
        request if the device refuses it.
        """
        if not self._sn:
            raise ZendureApiError("serial number unknown, cannot write")

        payload = {"sn": self._sn, "properties": dict(properties)}
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_WRITE_BODY_BYTES:
            raise ZendureBodyTooLarge(
                f"write body is {len(encoded)} bytes, limit is {MAX_WRITE_BODY_BYTES}"
            )

        _LOGGER.debug("Write %s to %s", properties, self.base_url)
        return await self._request("POST", PATH_WRITE, payload, attempts=WRITE_ATTEMPTS)

    async def async_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Issue an RPC command."""
        if not self._sn:
            raise ZendureApiError("serial number unknown, cannot call rpc")
        payload: dict[str, Any] = {"sn": self._sn, "method": method}
        if params is not None:
            payload["params"] = params
        return await self._request("POST", PATH_RPC, payload, attempts=WRITE_ATTEMPTS)

    # ── Transport ────────────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        attempts: int = 1,
    ) -> dict[str, Any]:
        """Send one request, trying again on a dropped connection.

        Only ZendureConnectionError is retried: the device did not answer, or
        its answer did not arrive whole. Every retry is logged at warning, a
        request that got through after one at info, so a flaky link shows in
        the log without debug logging switched on.
        """
        what = _describe(method, path, payload)
        attempts = max(1, attempts)
        for attempt in range(1, attempts + 1):
            try:
                result = await self._request_once(method, path, payload)
            except ZendureConnectionError as err:
                if attempt >= attempts:
                    if attempts > 1:
                        _LOGGER.warning(
                            "%s to %s failed after %d attempts: %s", what, self.base_url, attempts, err
                        )
                        raise ZendureConnectionError(f"{err} (after {attempts} attempts)") from err
                    raise
                delay = RETRY_DELAYS[min(attempt - 1, len(RETRY_DELAYS) - 1)]
                _LOGGER.warning(
                    "%s to %s failed (%s); attempt %d of %d in %.1f s",
                    what, self.base_url, err, attempt + 1, attempts, delay,
                )
                await asyncio.sleep(delay)
                continue
            if attempt > 1:
                _LOGGER.info("%s to %s succeeded on attempt %d of %d", what, self.base_url, attempt, attempts)
            return result
        raise AssertionError("unreachable")  # pragma: no cover

    async def _request_once(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with asyncio.timeout(HTTP_TIMEOUT):
                if method == "GET":
                    response = await self._session.get(url)
                else:
                    response = await self._session.post(url, json=payload)

                if response.status != 200:
                    raise ZendureApiError(f"HTTP {response.status} on {path}")

                text = await response.text()
        except aiohttp.ClientError as err:
            raise ZendureConnectionError(f"connection failed: {err}") from err
        except TimeoutError as err:
            raise ZendureConnectionError(f"timeout after {HTTP_TIMEOUT}s on {path}") from err

        if not text.strip():
            raise ZendureConnectionError(f"empty response on {path}")

        try:
            decoded = json.loads(text)
        except ValueError as err:
            # On a weak link this is a body cut off on the way.
            raise ZendureConnectionError(f"invalid JSON on {path}: {err}") from err

        if not isinstance(decoded, dict):
            raise ZendureApiError(f"unexpected payload type on {path}")

        return decoded


def _describe(method: str, path: str, payload: dict[str, Any] | None) -> str:
    """A short name for a request in the log: the properties a write sets."""
    if method == "POST" and path == PATH_WRITE and payload:
        props = payload.get("properties") or {}
        return "Write " + ", ".join(f"{k}={v}" for k, v in props.items())
    if method == "POST" and path == PATH_RPC and payload:
        return f"RPC {payload.get('method')}"
    return f"{method} {path}"
