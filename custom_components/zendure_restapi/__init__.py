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

An external P1 reader — HomeWizard, YouLess, EcoTracker, Shelly Pro 3EM, or a
Zendure meter read directly — is a third kind of entry since v1.5.2, with a
device and sensors of its own. Every battery controller is linked to it, the
same way it is linked to the Zendure meter, and the `Meter source` select on
the battery decides which of the two feeds the loops. See p1meter.py for why a
second meter is worth having. In v1.5.0 and v1.5.1 the external meter lived in
the battery entry's options; those are moved into an entry of its own on the
first start.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ZendureApiError, ZendureLocalApi
from .const import (
    CONF_DEVICE_TYPE,
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
    DEVICE_TYPE_EXTERNAL,
    DEVICE_TYPE_METER,
    DOMAIN,
    INTEGRATION_VERSION,
)
from .controller import ZendureController
from .coordinator import ZendureCoordinator
from .p1meter import ADAPTERS, ExternalMeter
from .settings import ZendureSettings

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SWITCH,
]

# An external meter entry has no switches, selects or numbers of its own.
EXTERNAL_PLATFORMS: list[Platform] = [Platform.SENSOR]

RUNTIME_SUFFIX = "_runtime"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Zendure device, or an external meter, from a config entry."""
    _LOGGER.debug("Setting up Zendure RestAPI v%s", INTEGRATION_VERSION)

    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_EXTERNAL:
        return await _async_setup_external(hass, entry)

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
        _migrate_external_options(hass, entry)
        settings = ZendureSettings(hass, entry)
        controller = ZendureController(coordinator, settings)
        store[f"{entry.entry_id}{RUNTIME_SUFFIX}"] = {
            "settings": settings,
            "controller": controller,
        }

        @callback
        def _on_update() -> None:
            hass.async_create_task(controller.async_run())

        entry.async_on_unload(coordinator.async_add_listener(_on_update))

    _link_meters(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_update_options))
    return True


async def _async_setup_external(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start polling an external P1 reader and link it to the controllers."""
    kind = entry.data.get(CONF_EXTERNAL_TYPE)
    if kind not in ADAPTERS:
        _LOGGER.error("Unknown external meter type '%s'", kind)
        return False
    interval = _as_int(
        entry.options.get(CONF_EXTERNAL_INTERVAL), DEFAULT_EXTERNAL_INTERVAL
    )
    meter = ADAPTERS[kind](
        hass, async_get_clientsession(hass), entry.data[CONF_HOST], interval
    )
    meter.entry_id = entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = meter
    meter.async_start()
    entry.async_on_unload(meter.async_stop)

    _link_meters(hass)

    await hass.config_entries.async_forward_entry_setups(entry, EXTERNAL_PLATFORMS)
    # The interval and the address belong to the poller, which is cheapest to
    # rebuild by reloading the entry; nothing else hangs off it.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@callback
def _migrate_external_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Move an external meter from the battery's options into its own entry.

    v1.5.0 and v1.5.1 kept the reader's type, address and interval in the
    battery entry's options. If an address was saved there, an entry of its
    own is created through the import step — which aborts when one for that
    address already exists — and the three keys leave the battery options in
    any case, so this runs once.
    """
    keys = (CONF_EXTERNAL_TYPE, CONF_EXTERNAL_HOST, CONF_EXTERNAL_INTERVAL)
    if not any(key in entry.options for key in keys):
        return

    host = str(entry.options.get(CONF_EXTERNAL_HOST) or "").strip()
    if host:
        data = {
            CONF_EXTERNAL_TYPE: entry.options.get(CONF_EXTERNAL_TYPE, DEFAULT_EXTERNAL_TYPE),
            CONF_HOST: host,
            CONF_EXTERNAL_INTERVAL: _as_int(
                entry.options.get(CONF_EXTERNAL_INTERVAL), DEFAULT_EXTERNAL_INTERVAL
            ),
        }
        _LOGGER.info(
            "Moving the external meter at %s from the battery options into an "
            "entry of its own", host,
        )
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data=data
            )
        )

    options = {k: v for k, v in entry.options.items() if k not in keys}
    hass.config_entries.async_update_entry(entry, options=options)


@callback
def _link_meters(hass: HomeAssistant) -> None:
    """Point every controller at the Zendure meter and the external meter.

    Linking happens on each setup and unload because the order in which the
    entries come up is not fixed: whichever comes last completes the pairing.
    Each kind is linked only when exactly one is configured; with two of the
    same kind there is no telling which one measures the grid connection.
    """
    store = hass.data.get(DOMAIN, {})
    meters = [
        c for key, c in store.items()
        if not key.endswith(RUNTIME_SUFFIX)
        and isinstance(c, ZendureCoordinator)
        and c.device_type == DEVICE_TYPE_METER
    ]
    externals = [c for c in store.values() if isinstance(c, ExternalMeter)]
    meter = meters[0] if len(meters) == 1 else None
    external = externals[0] if len(externals) == 1 else None

    if len(meters) > 1:
        _LOGGER.warning(
            "%d Zendure meters configured. Automatic linking needs exactly one.",
            len(meters),
        )
    if len(externals) > 1:
        _LOGGER.warning(
            "%d external meters configured. Automatic linking needs exactly one; "
            "none is used.", len(externals),
        )

    for key, runtime in store.items():
        if key.endswith(RUNTIME_SUFFIX):
            runtime["controller"].attach_meter(meter)
            runtime["controller"].attach_external(external)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options in place."""
    coordinator: ZendureCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(coordinator, ZendureCoordinator):
        return
    interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator.set_scan_interval(interval)
    _LOGGER.debug("Polling interval set to %ss", interval)
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    external = entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_EXTERNAL
    unloaded = await hass.config_entries.async_unload_platforms(
        entry, EXTERNAL_PLATFORMS if external else PLATFORMS
    )
    if unloaded:
        store = hass.data.get(DOMAIN, {})
        store.pop(entry.entry_id, None)
        # Drop the meter subscriptions before the controller leaves the store.
        # After the pop, _link_meters can no longer reach it, so a listener
        # left behind here would outlive the entry and keep trimming a device
        # Home Assistant considers unloaded.
        runtime = store.pop(f"{entry.entry_id}{RUNTIME_SUFFIX}", None)
        if runtime is not None:
            runtime["controller"].detach_meter()
        _link_meters(hass)
    return unloaded
