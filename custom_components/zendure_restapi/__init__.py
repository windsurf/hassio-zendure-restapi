"""The Zendure RestAPI integration.

Local control of Zendure devices through the zenSDK HTTP API. No cloud account,
no MQTT broker, no protobuf: a plain JSON REST server running on the device
itself.

Two device classes are supported and each gets its own config entry:

* SolarFlow batteries, which report a properties envelope with packData.
* The P1 meter, which reports a flat payload with per-phase power and no serial
  number.

A battery entry also runs the operation-mode controller, which is two loops.
The mode loop executes once per battery poll, so it always acts on readings
from that same cycle — a loop on its own timer would eventually correct twice
for one deviation, which is how charge/discharge oscillation gets built. The
trim loop is subscribed to the meter coordinator instead, and follows the grid
between battery polls without ever changing direction. See controller.py.

A battery entry can also poll an external P1 reader, configured under
Configure on that entry. Which meter feeds the loops is the `Meter source`
select; see p1meter.py for why a second meter is worth having.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZendureApiError, ZendureLocalApi
from .const import (
    CONF_EXTERNAL_HOST,
    CONF_EXTERNAL_INTERVAL,
    CONF_EXTERNAL_TYPE,
    CONF_HOST,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SN,
    DEFAULT_PORT,
    DEFAULT_EXTERNAL_INTERVAL,
    DEFAULT_EXTERNAL_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DEVICE_TYPE_METER,
    DOMAIN,
    INTEGRATION_VERSION,
)
from .controller import ZendureController
from .coordinator import ZendureCoordinator
from .p1meter import ADAPTERS
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

RUNTIME_SUFFIX = "_runtime"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Zendure device from a config entry."""
    _LOGGER.debug("Setting up Zendure RestAPI v%s", INTEGRATION_VERSION)

    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    sn = entry.data.get(CONF_SN)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    session = async_get_clientsession(hass)
    api = ZendureLocalApi(session, host, port, sn)
    coordinator = ZendureCoordinator(hass, api, scan_interval)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ZendureApiError as err:
        raise ConfigEntryNotReady(f"Zendure device at {host} unreachable: {err}") from err

    store = hass.data.setdefault(DOMAIN, {})
    store[entry.entry_id] = coordinator

    # Only a battery gets a controller; a meter has nothing to control.
    if coordinator.device_type != DEVICE_TYPE_METER:
        settings = ZendureSettings(hass, entry)
        controller = ZendureController(coordinator, settings)
        runtime = {
            "settings": settings,
            "controller": controller,
            "external": None,
            "external_config": None,
        }
        store[f"{entry.entry_id}{RUNTIME_SUFFIX}"] = runtime
        _setup_external(hass, entry, runtime)
        entry.async_on_unload(lambda: _stop_external(runtime))

        @callback
        def _on_update() -> None:
            hass.async_create_task(controller.async_run())

        entry.async_on_unload(coordinator.async_add_listener(_on_update))

    _link_meters(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


@callback
def _link_meters(hass: HomeAssistant) -> None:
    """Point every controller at the meter coordinator, if there is one.

    Linking happens on each setup because the order in which the battery and
    the meter are configured is not fixed: whichever comes second completes the
    pairing for both.
    """
    store = hass.data.get(DOMAIN, {})
    meters = [
        c for key, c in store.items()
        if not key.endswith(RUNTIME_SUFFIX)
        and isinstance(c, ZendureCoordinator)
        and c.device_type == DEVICE_TYPE_METER
    ]
    meter = meters[0] if len(meters) == 1 else None

    if len(meters) > 1:
        _LOGGER.warning(
            "%d Zendure meters configured. Automatic linking needs exactly one; "
            "smart modes will stay blocked.", len(meters)
        )

    for key, runtime in store.items():
        if key.endswith(RUNTIME_SUFFIX):
            runtime["controller"].attach_meter(meter)


def _external_config(entry: ConfigEntry) -> tuple[str, str, int] | None:
    """The external meter as configured, or None when there is none.

    Only an address actually saved in the options counts. The form pre-fills
    one, but an entry that never submitted it polls nothing.
    """
    host = str(entry.options.get(CONF_EXTERNAL_HOST) or "").strip()
    if not host:
        return None
    kind = entry.options.get(CONF_EXTERNAL_TYPE, DEFAULT_EXTERNAL_TYPE)
    if kind not in ADAPTERS:
        _LOGGER.warning("Unknown external meter type '%s', not polling", kind)
        return None
    try:
        interval = int(entry.options.get(CONF_EXTERNAL_INTERVAL, DEFAULT_EXTERNAL_INTERVAL))
    except (TypeError, ValueError):
        interval = DEFAULT_EXTERNAL_INTERVAL
    return kind, host, interval


@callback
def _setup_external(hass: HomeAssistant, entry: ConfigEntry, runtime: dict) -> None:
    """Start, restart or stop the external meter to match the options.

    Called on setup and on every options change. Most option changes are a
    controller setting moving, which must not touch the poller, so it is only
    rebuilt when its own configuration differs.
    """
    config = _external_config(entry)
    if config == runtime["external_config"] and (
        config is None or runtime["external"] is not None
    ):
        return

    _stop_external(runtime)
    runtime["external_config"] = config
    if config is None:
        runtime["controller"].attach_external(None)
        return

    kind, host, interval = config
    meter = ADAPTERS[kind](hass, async_get_clientsession(hass), host, interval)
    runtime["external"] = meter
    runtime["controller"].attach_external(meter)
    meter.async_start()


@callback
def _stop_external(runtime: dict) -> None:
    meter = runtime.get("external")
    if meter is not None:
        meter.async_stop()
        runtime["external"] = None
    runtime["controller"].attach_external(None)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options in place."""
    coordinator: ZendureCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return
    runtime = hass.data.get(DOMAIN, {}).get(f"{entry.entry_id}{RUNTIME_SUFFIX}")
    if runtime is not None:
        _setup_external(hass, entry, runtime)
    interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator.set_scan_interval(interval)
    _LOGGER.debug("Polling interval set to %ss", interval)
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        store = hass.data.get(DOMAIN, {})
        store.pop(entry.entry_id, None)
        # Drop the meter subscription before the controller leaves the store.
        # After the pop, _link_meters can no longer reach it, so a listener
        # left behind here would outlive the entry and keep trimming a device
        # Home Assistant considers unloaded.
        runtime = store.pop(f"{entry.entry_id}{RUNTIME_SUFFIX}", None)
        if runtime is not None:
            _stop_external(runtime)
            runtime["controller"].detach_meter()
        _link_meters(hass)
    return unloaded
