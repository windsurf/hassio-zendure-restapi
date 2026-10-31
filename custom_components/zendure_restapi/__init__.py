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

Since v1.5.3 there may be several external meters — a YouLess, a HomeWizard
and a Home Assistant sensor side by side on one splitter, say — and each
battery's `External meter` select says which of them it uses.

Since v1.7.0 a battery cluster lets up to three batteries, one per phase,
hold the grid at zero together: an entry of its own with a subentry per
battery. The logic lives in cluster/ and member.py; here it is only wired up.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.start import async_at_started
from homeassistant.util import slugify

from .api import ZendureApiError, ZendureLocalApi
from .const import (
    METER_SOURCE_EXTERNAL,
    METER_SOURCE_ZENDURE,
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
    DEVICE_TYPE_BATTERY,
    DEVICE_TYPE_METER,
    DOMAIN,
    EXTERNAL_NONE,
    EXTERNAL_TYPE_HA_SENSOR,
    HA_SENSOR_INTERVAL,
    INTEGRATION_VERSION,
    CONF_MODEL,
    CONF_BATTERY_ENTRY,
    DEVICE_TYPE_PRICE,
    OPT_CREATE_DASHBOARD,
    OPT_REBUILD_DASHBOARD,
    OPT_SOLAR_ENERGY,
    OPT_SOLAR_POWER,
    OWN_PV_MIN_KWH,
    OPT_DASHBOARD_OFFERED,
    OPT_EXTERNAL_METER,
    SIGNAL_EXTERNALS_CHANGED,
    CONF_EXPORT_ENTITY,
    CONF_SOURCE_ENTITY,
    RETIRED_BATTERY_ENTITIES,
    RETIRED_EXTERNAL_ENTITIES,
    RETIRED_PRICE_ENTITIES,
    CONF_INVERT,
    CONF_METER_PLAN,
    CONF_SOURCE_DEVICE,
    SLOW_METER_SECONDS,
    DEVICE_TYPE_CLUSTER,
    MODE_STANDBY,
    OPT_CHARGE_BUFFER,
    OPT_CONCENTRATE_BELOW,
    OPT_DISCHARGE_BUFFER,
    OPT_OPERATION_MODE,
    OPT_REDISTRIBUTE,
    OPT_START_CHARGE_AT,
    OPT_START_DISCHARGE_AT,
    SIGNAL_CLUSTER,
    SUBENTRY_BATTERY,
    CLUSTER_KEY,
)
from .controller import ZendureController
from .dashboard import EXISTS, async_create_dashboard, notify
from .pricecontrol import PriceControl
from .coordinator import ZendureCoordinator
from .entity import migrate_pack_entity_ids
from .hadevice import MeterPlan
from .p1meter import ADAPTERS, ExternalMeter
from .cluster.contract import GroupSettings
from .cluster.runtime import ClusterCoordinator
from .member import ZendureMember
from .settings import SettingsView, ZendureSettings

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
PRICE_PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH, Platform.NUMBER, Platform.SELECT]
CLUSTER_PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.NUMBER, Platform.SELECT]

RUNTIME_SUFFIX = "_runtime"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up one Zendure device, or an external meter, from a config entry."""
    _LOGGER.debug("Setting up Zendure RestAPI v%s", INTEGRATION_VERSION)

    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_EXTERNAL:
        return await _async_setup_external(hass, entry)
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_PRICE:
        return await _async_setup_price(hass, entry)
    if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_CLUSTER:
        return await _async_setup_cluster(hass, entry)

    host = entry.data[CONF_HOST]
    port = entry.data.get(CONF_PORT, DEFAULT_PORT)
    sn = entry.data.get(CONF_SN)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

    session = async_get_clientsession(hass)
    api = ZendureLocalApi(session, host, port, sn)
    coordinator = ZendureCoordinator(hass, api, scan_interval)
    await coordinator.async_load_seen(entry.entry_id, entry.title)

    try:
        await coordinator.async_config_entry_first_refresh()
    except ZendureApiError as err:
        raise ConfigEntryNotReady(f"Zendure device at {host} unreachable: {err}") from err

    store = hass.data.setdefault(DOMAIN, {})
    store[entry.entry_id] = coordinator

    # Before v1.6.7 a Smart Meter 3CT was added as a battery: it reports no
    # meterType (issue #7). Its report now says meter, so the entry follows.
    # Its names stay those of the old entry; adding it again gives the right ones.
    if (
        coordinator.device_type == DEVICE_TYPE_METER
        and entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_BATTERY
    ):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_DEVICE_TYPE: DEVICE_TYPE_METER}
        )
        _LOGGER.info("%s reports as a meter; its entry is now a meter entry", entry.title)

    # Only a battery gets a controller; a meter has nothing to control.
    if coordinator.device_type != DEVICE_TYPE_METER:
        _migrate_external_options(hass, entry)
        settings = ZendureSettings(hass, entry)
        # The controller reads through a view: its own settings, or the
        # cluster's while the battery takes part (v1.7.0).
        view = SettingsView(settings)
        controller = ZendureController(coordinator, view)
        store[f"{entry.entry_id}{RUNTIME_SUFFIX}"] = {
            "settings": settings,
            "view": view,
            "controller": controller,
            # Whether its Take part switch is made: it belongs to the cluster.
            "take_part": is_member(hass, entry.entry_id),
        }

        await controller.async_load_measure()
        _remove_retired(
            hass, coordinator.device_id or entry.unique_id or entry.entry_id, RETIRED_BATTERY_ENTITIES
        )

        @callback
        def _on_update() -> None:
            # Measured before the loop acts, on the same poll (v1.6.3).
            controller.observe()
            hass.async_create_task(controller.async_run())

        entry.async_on_unload(coordinator.async_add_listener(_on_update))

    _link_cluster(hass)
    _link_meters(hass)

    # Before the platforms: the pack entities come up under their new ids.
    migrate_pack_entity_ids(hass, entry.entry_id)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if coordinator.device_type == DEVICE_TYPE_METER:
        _remove_battery_leftovers(hass, entry)
    entry.async_on_unload(entry.add_update_listener(async_update_options))

    # The dashboard needs the entities registered and the frontend up, hence
    # after the platforms and once Home Assistant has started. It is made by
    # default, once (v1.6.3); after that only when asked in the options.
    if coordinator.device_type != DEVICE_TYPE_METER and _dashboard_wanted(entry):

        async def _create(_hass: HomeAssistant) -> None:
            await _async_dashboard(hass, entry)

        entry.async_on_unload(async_at_started(hass, _create))
    return True


async def _async_setup_price(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Price control for one battery: its own device, writing the battery's mode."""
    battery_id = entry.data[CONF_BATTERY_ENTRY]
    runtime = hass.data.get(DOMAIN, {}).get(f"{battery_id}{RUNTIME_SUFFIX}")
    if runtime is None:
        # Set up after its battery; Home Assistant retries until it is there.
        raise ConfigEntryNotReady("the battery for price control is not set up yet")
    price = PriceControl(
        hass, entry, ZendureSettings(hass, entry), runtime["settings"], battery_id
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = price
    _remove_retired(hass, entry.entry_id, RETIRED_PRICE_ENTITIES)
    await hass.config_entries.async_forward_entry_setups(entry, PRICE_PLATFORMS)

    # The listener first: on a reload Home Assistant is already running, so
    # _start runs at once, and a setting it writes must reach the listener.
    async def _changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
        price.async_update()

    entry.async_on_unload(entry.add_update_listener(_changed))

    # The price comes from another integration: start once everything is up,
    # so a price that is not there yet is not mistaken for none.
    @callback
    def _start(_hass: HomeAssistant) -> None:
        price.async_start()

    entry.async_on_unload(async_at_started(hass, _start))
    entry.async_on_unload(price.async_stop)
    return True


# ── Battery cluster (v1.7.0) ─────────────────────────────────────────


def _member_ids(entry: ConfigEntry) -> list[str]:
    """The battery entries under a cluster entry, in subentry order."""
    return [
        str(sub.data.get(CONF_BATTERY_ENTRY))
        for sub in entry.subentries.values()
        if sub.subentry_type == SUBENTRY_BATTERY and sub.data.get(CONF_BATTERY_ENTRY)
    ]


def cluster_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """The cluster entry, loaded or not."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.data.get(CONF_DEVICE_TYPE) == DEVICE_TYPE_CLUSTER:
            return entry
    return None


def is_member(hass: HomeAssistant, battery_id: str) -> bool:
    """Whether a battery entry belongs to the cluster, set up or not."""
    entry = cluster_entry(hass)
    return entry is not None and battery_id in _member_ids(entry)


async def _async_setup_cluster(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """The battery cluster: its own device, steering its batteries."""
    store = hass.data.setdefault(DOMAIN, {})
    settings = ZendureSettings(hass, entry)

    def group_settings() -> GroupSettings:
        return GroupSettings(
            mode=str(settings.get(OPT_OPERATION_MODE)),
            charge_buffer=settings.get_int(OPT_CHARGE_BUFFER),
            discharge_buffer=settings.get_int(OPT_DISCHARGE_BUFFER),
            start_charge_at=settings.get_int(OPT_START_CHARGE_AT),
            start_discharge_at=settings.get_int(OPT_START_DISCHARGE_AT),
            concentrate_below=settings.get_int(OPT_CONCENTRATE_BELOW),
            redistribute_s=settings.get_int(OPT_REDISTRIBUTE),
        )

    signal = f"{SIGNAL_CLUSTER}_{entry.entry_id}"
    cluster = ClusterCoordinator(hass, group_settings, lambda: async_dispatcher_send(hass, signal))
    cluster.entry = entry
    cluster.settings = settings
    cluster.signal = signal
    store[entry.entry_id] = cluster
    store[CLUSTER_KEY] = entry.entry_id
    known = {sid: dict(sub.data) for sid, sub in entry.subentries.items()}

    # A battery parks in standby when it joins: should the cluster go,
    # it stands still until its owner chooses again.
    for battery_id in _member_ids(entry):
        battery = hass.config_entries.async_get_entry(battery_id)
        if battery is not None and battery.options.get(OPT_OPERATION_MODE, MODE_STANDBY) != MODE_STANDBY:
            hass.config_entries.async_update_entry(
                battery, options={**battery.options, OPT_OPERATION_MODE: MODE_STANDBY}
            )

    # A battery set up without its Take part switch, or with one it no longer
    # needs, is reloaded so the switch comes or goes. At a start of Home
    # Assistant they already match, because the switch follows the entries.
    _reload_mismatched(hass, set(_member_ids(entry)))

    _link_cluster(hass)
    _link_meters(hass)
    await hass.config_entries.async_forward_entry_setups(entry, CLUSTER_PLATFORMS)

    async def _changed(hass: HomeAssistant, entry: ConfigEntry) -> None:
        # A battery added, changed or removed: set up again. A setting: decide now.
        now = {sid: dict(sub.data) for sid, sub in entry.subentries.items()}
        if now != known:
            hass.config_entries.async_schedule_reload(entry.entry_id)
            return
        _link_meters(hass)
        await cluster.async_evaluate()

    entry.async_on_unload(entry.add_update_listener(_changed))
    cluster.start()
    entry.async_on_unload(cluster.stop)
    return True


@callback
def _link_cluster(hass: HomeAssistant) -> None:
    """Give every battery under the cluster its member, take it from the others.

    Runs on every setup and unload of a battery or the cluster, like
    _link_meters: whichever comes up last completes the pairing.
    """
    store = hass.data.get(DOMAIN, {})
    cluster = store.get(store.get(CLUSTER_KEY) or "")
    wanted: dict[str, Any] = {}
    if isinstance(cluster, ClusterCoordinator):
        for sid, sub in cluster.entry.subentries.items():
            if sub.subentry_type == SUBENTRY_BATTERY and sub.data.get(CONF_BATTERY_ENTRY):
                wanted[str(sub.data[CONF_BATTERY_ENTRY])] = (sid, sub)

    members: list[ZendureMember] = []
    for key, runtime in list(store.items()):
        if not key.endswith(RUNTIME_SUFFIX) or not isinstance(runtime, dict):
            continue
        battery_id = key[: -len(RUNTIME_SUFFIX)]
        current: ZendureMember | None = runtime.get("member")
        if battery_id not in wanted:
            if current is not None:
                current.leave()
                runtime["member"] = None
            continue
        sid, sub = wanted[battery_id]
        if current is None or current.key != sid or current.data != dict(sub.data):
            if current is not None:
                current.leave()
            battery = hass.config_entries.async_get_entry(battery_id)
            # Title plus serial tail: two of the same model share a title.
            sn = str(battery.data.get(CONF_SN) or "") if battery is not None else ""
            name = sub.title.rsplit(" · ", 1)[0]
            if battery is not None:
                name = f"{battery.title} (…{sn[-4:]})" if sn else battery.title
            current = ZendureMember(
                sid,
                name,
                dict(sub.data),
                runtime["controller"],
                runtime["view"],
                runtime["settings"],
            )
            current.join(cluster.settings, cluster.entry.title, cluster.secondaries_against)
            runtime["member"] = current
        members.append(current)
    if isinstance(cluster, ClusterCoordinator):
        cluster.set_members(members)


def _remove_retired(
    hass: HomeAssistant, unique_prefix: str, retired: tuple[tuple[str, str], ...]
) -> None:
    """Remove the registry entries of entities an earlier version made (v1.6.5)."""
    registry = er.async_get(hass)
    for platform, key in retired:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, f"{unique_prefix}_{key}")
        if entity_id is None:
            continue
        registry.async_remove(entity_id)
        _LOGGER.info("Removed %s: no longer provided since an earlier version", entity_id)


async def _async_setup_external(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start polling an external P1 reader and link it to the controllers."""
    kind = entry.data.get(CONF_EXTERNAL_TYPE)
    if kind not in ADAPTERS:
        _LOGGER.error("Unknown external meter type '%s'", kind)
        return False
    session = async_get_clientsession(hass)
    if kind == EXTERNAL_TYPE_HA_SENSOR:
        meter = _ha_meter(hass, session, entry)
    else:
        interval = _as_int(
            entry.options.get(CONF_EXTERNAL_INTERVAL), DEFAULT_EXTERNAL_INTERVAL
        )
        meter = ADAPTERS[kind](hass, session, entry.data[CONF_HOST], interval)
    meter.entry_id = entry.entry_id
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = meter
    meter.async_start()
    entry.async_on_unload(meter.async_stop)

    @callback
    def _check_speed(_now: Any = None) -> None:
        _slow_meter_issue(hass, entry, meter)

    entry.async_on_unload(async_track_time_interval(hass, _check_speed, SPEED_CHECK))
    entry.async_on_unload(lambda: ir.async_delete_issue(hass, DOMAIN, f"slow_meter_{entry.entry_id}"))

    _link_meters(hass)

    _remove_retired(hass, entry.entry_id, RETIRED_EXTERNAL_ENTITIES)
    await hass.config_entries.async_forward_entry_setups(entry, EXTERNAL_PLATFORMS)
    # The interval and the address belong to the poller, which is cheapest to
    # rebuild by reloading the entry; nothing else hangs off it.
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


# How often the update interval of an external meter is checked (v1.6.5).
SPEED_CHECK = timedelta(minutes=1)


def _slow_meter_issue(hass: HomeAssistant, entry: ConfigEntry, meter: Any) -> None:
    """A repair while a meter delivers readings further apart than steering needs.

    The loops accept a reading for METER_FALLBACK_AGE seconds; a meter that
    reports every ten seconds steers only part of the time. Mostly a Home
    Assistant sensor whose integration polls slowly, such as the YouLess
    integration of Home Assistant (every 10 s). Clears by itself.
    """
    issue_id = f"slow_meter_{entry.entry_id}"
    interval = meter.update_interval
    if interval is not None and interval > SLOW_METER_SECONDS:
        ir.async_create_issue(
            hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="slow_meter",
            translation_placeholders={
                "meter": meter.label,
                "seconds": f"{interval:.0f}" if interval >= 10 else f"{interval:.1f}",
            },
        )
    elif interval is not None:
        ir.async_delete_issue(hass, DOMAIN, issue_id)


def _ha_meter(hass: HomeAssistant, session: Any, entry: ConfigEntry) -> Any:
    """A meter of Home Assistant sensors: a device's plan, or one sensor (v1.6.5)."""
    data = entry.data
    if data.get(CONF_METER_PLAN):
        plan = MeterPlan.from_data(data[CONF_METER_PLAN])
        plan.invert = bool(data.get(CONF_INVERT, plan.invert))
        device = dr.async_get(hass).async_get(data.get(CONF_SOURCE_DEVICE) or "")
        name = (device.name_by_user or device.name) if device else None
        # The host only names the meter in the log; the plan says what is read.
        meter = ADAPTERS[EXTERNAL_TYPE_HA_SENSOR](
            hass, session, name or data.get(CONF_SOURCE_DEVICE) or "", HA_SENSOR_INTERVAL
        )
        meter.plan = plan
        meter.name = name
        return meter
    meter = ADAPTERS[EXTERNAL_TYPE_HA_SENSOR](
        hass, session, data[CONF_SOURCE_ENTITY], HA_SENSOR_INTERVAL
    )
    meter.export_entity = data.get(CONF_EXPORT_ENTITY) or None
    terms = [(data[CONF_SOURCE_ENTITY], 1)]
    if meter.export_entity:
        terms.append((meter.export_entity, -1))
    meter.plan = MeterPlan(terms=terms, invert=bool(data.get(CONF_INVERT)), how="sensor")
    return meter


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _as_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@callback
def battery_leftovers(entries: list[Any], live: set[str]) -> list[str]:
    """The entity ids a meter entry still holds from when it was set up as a battery.

    A Smart Meter 3CT added before v1.6.7 became a battery entry, with a
    controller, an operation mode and the settings of one. Once the entry is a
    meter those entities are gone for good and read "unavailable". They are
    recognised by the operation mode select, which only a battery has. An
    entity that is live, or disabled on purpose (the meter's own timestamp and
    message id), stays.
    """
    if not any((entity.unique_id or "").endswith("_operation_mode") for entity in entries):
        return []
    return [
        entity.entity_id
        for entity in entries
        if entity.entity_id not in live and entity.disabled_by is None
    ]


def _remove_battery_leftovers(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove what a battery entry left behind once it is a meter (v1.6.10)."""
    registry = er.async_get(hass)
    live = {
        entity_id
        for platform in entity_platform.async_get_platforms(hass, DOMAIN)
        if platform.config_entry is entry
        for entity_id in platform.entities
    }
    for entity_id in battery_leftovers(er.async_entries_for_config_entry(registry, entry.entry_id), live):
        registry.async_remove(entity_id)
        _LOGGER.info("Removed %s: %s is a meter, not a battery", entity_id, entry.title)


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


def external_meters(hass: HomeAssistant) -> list[ExternalMeter]:
    """The external meters set up, in the order they came up."""
    return [m for m in hass.data.get(DOMAIN, {}).values() if isinstance(m, ExternalMeter)]


def chosen_external(
    externals: list[ExternalMeter], choice: str
) -> ExternalMeter | None:
    """The external meter a battery uses, given its External meter setting.

    `none` uses none. An entry_id uses that meter while it is set up. Unset —
    or set to a meter that has since been removed — uses the only external
    meter if there is exactly one; with several there is no telling which one
    measures the grid connection, so none is used until one is chosen.
    """
    if choice == EXTERNAL_NONE:
        return None
    for meter in externals:
        if meter.entry_id == choice:
            return meter
    return externals[0] if len(externals) == 1 else None


@callback
def _link_meters(hass: HomeAssistant) -> None:
    """Point every controller at the Zendure meter and its external meter.

    Linking happens on each setup and unload because the order in which the
    entries come up is not fixed: whichever comes last completes the pairing.
    The Zendure meter is linked only when exactly one is configured. The
    external meter follows each battery's External meter select.
    """
    store = hass.data.get(DOMAIN, {})
    meters = [
        c for key, c in store.items()
        if not key.endswith(RUNTIME_SUFFIX)
        and isinstance(c, ZendureCoordinator)
        and c.device_type == DEVICE_TYPE_METER
    ]
    externals = external_meters(hass)
    meter = meters[0] if len(meters) == 1 else None

    if len(meters) > 1:
        _LOGGER.warning(
            "%d Zendure meters configured. Automatic linking needs exactly one.",
            len(meters),
        )

    for key, runtime in store.items():
        if not key.endswith(RUNTIME_SUFFIX):
            continue
        # Through the view: a battery under the cluster uses its meter.
        choice = str(runtime.get("view", runtime["settings"]).get(OPT_EXTERNAL_METER) or "")
        external = chosen_external(externals, choice)
        if external is None and len(externals) > 1 and choice != EXTERNAL_NONE:
            _LOGGER.warning(
                "%d external meters configured and none chosen on %s; "
                "set its External meter select", len(externals), key[: -len(RUNTIME_SUFFIX)],
            )
        controller = runtime["controller"]
        controller.attach_meter(meter)
        # Relinking the same meter would only reset the trim loop's state.
        if getattr(controller, "external", None) is not external:
            controller.attach_external(external)

    async_dispatcher_send(hass, SIGNAL_EXTERNALS_CHANGED)


async def async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Apply changed options in place."""
    coordinator: ZendureCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(coordinator, ZendureCoordinator):
        return
    if coordinator.device_type != DEVICE_TYPE_METER and _dashboard_wanted(entry):
        hass.async_create_task(_async_dashboard(hass, entry))
    interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator.set_scan_interval(interval)
    _LOGGER.debug("Polling interval set to %ss", interval)
    await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    kind = entry.data.get(CONF_DEVICE_TYPE)
    platforms = (
        EXTERNAL_PLATFORMS if kind == DEVICE_TYPE_EXTERNAL
        else PRICE_PLATFORMS if kind == DEVICE_TYPE_PRICE
        else CLUSTER_PLATFORMS if kind == DEVICE_TYPE_CLUSTER
        else PLATFORMS
    )
    unloaded = await hass.config_entries.async_unload_platforms(entry, platforms)
    if unloaded and kind == DEVICE_TYPE_CLUSTER:
        store = hass.data.get(DOMAIN, {})
        cluster = store.pop(entry.entry_id, None)
        if cluster is not None:
            cluster.stop()
        if store.get(CLUSTER_KEY) == entry.entry_id:
            store.pop(CLUSTER_KEY, None)
        # The batteries return to their own settings, in standby.
        _link_cluster(hass)
        _link_meters(hass)
        return unloaded
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
            # Written before a reload sets the entry up again and reads it.
            await runtime["controller"].async_save_measure()
        _link_cluster(hass)
        _link_meters(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A removed coordinator: reload its batteries, so their Take part switch goes."""
    if entry.data.get(CONF_DEVICE_TYPE) != DEVICE_TYPE_CLUSTER:
        return
    _reload_mismatched(hass, set())


@callback
def _reload_mismatched(hass: HomeAssistant, members: set[str]) -> None:
    """Reload the batteries whose Take part switch does not match their membership."""
    store = hass.data.get(DOMAIN, {})
    for key, runtime in list(store.items()):
        if not key.endswith(RUNTIME_SUFFIX) or not isinstance(runtime, dict):
            continue
        battery_id = key[: -len(RUNTIME_SUFFIX)]
        if bool(runtime.get("take_part")) != (battery_id in members):
            hass.config_entries.async_schedule_reload(battery_id)


# ── Dashboard ────────────────────────────────────────────────────────────

DASHBOARD_BUSY = f"{DOMAIN}_dashboard_busy"


def _dashboard_wanted(entry: ConfigEntry) -> bool:
    """Asked for in the options, or never made yet on this installation."""
    return (
        bool(entry.options.get(OPT_CREATE_DASHBOARD))
        or bool(entry.options.get(OPT_REBUILD_DASHBOARD))
        or not entry.options.get(OPT_DASHBOARD_OFFERED)
    )


def _own_pv(hass: HomeAssistant, registry: er.EntityRegistry, device_id: str) -> bool:
    """Whether the device's own PV has produced anything worth a series."""
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, f"{device_id}_energy_pv")
    state = hass.states.get(entity_id) if entity_id else None
    try:
        return state is not None and float(state.state) >= OWN_PV_MIN_KWH
    except (TypeError, ValueError):
        return False


def _prefix(registry: er.EntityRegistry, unique_id: str, suffix: str) -> str | None:
    """Entity id prefix of a device, read off one of its entities."""
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    if entity_id is None:
        return None
    object_id = entity_id.split(".", 1)[1]
    return object_id[: -len(suffix)] if object_id.endswith(suffix) else None


async def _async_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle a request for the dashboard once, then clear the request."""
    store = hass.data.get(DOMAIN, {})
    coordinator = store.get(entry.entry_id)
    if (
        hass.data.get(DASHBOARD_BUSY)
        or not _dashboard_wanted(entry)
        or not isinstance(coordinator, ZendureCoordinator)
    ):
        return
    hass.data[DASHBOARD_BUSY] = True
    replace = bool(entry.options.get(OPT_REBUILD_DASHBOARD))
    requested = bool(entry.options.get(OPT_CREATE_DASHBOARD)) or replace
    try:
        registry = er.async_get(hass)
        device_id = coordinator.device_id or entry.unique_id or entry.entry_id
        model = entry.data.get(CONF_MODEL) or "SolarFlow"
        battery = (
            _prefix(registry, f"{device_id}_controller_status", "controller_status")
            or f"{slugify(f'Zendure {model}')}_"
        )
        meter = None
        meters = [
            (key, c) for key, c in store.items()
            if isinstance(c, ZendureCoordinator) and c.device_type == DEVICE_TYPE_METER
        ]
        if len(meters) == 1:
            key, meter_coordinator = meters[0]
            meter_entry = hass.config_entries.async_get_entry(key)
            meter_id = meter_coordinator.device_id or (meter_entry.unique_id if meter_entry else None) or key
            meter = _prefix(registry, f"{meter_id}_total_power", "grid_power_total")
        price = None
        for other in hass.config_entries.async_entries(DOMAIN):
            if other.data.get(CONF_BATTERY_ENTRY) == entry.entry_id:
                price = _prefix(registry, f"{other.entry_id}_price_control_status", "status")
        try:
            ceiling = int((coordinator.data or {}).get("inverseMaxPower") or 3000)
        except (TypeError, ValueError):
            ceiling = 3000
        # The external meter's real entity ids, read off their unique ids: the
        # template's sensor.external_p1_meter_* is only right for an English
        # installation that kept the default device name (v1.6.4).
        external_ids: dict[str, str] = {}
        ext_ids = [k for k, m in hass.data.get(DOMAIN, {}).items() if isinstance(m, ExternalMeter)]
        runtime = store.get(f"{entry.entry_id}{RUNTIME_SUFFIX}") or {}
        controller = runtime.get("controller")
        linked = getattr(controller, "external", None)
        # The meter this battery is linked to first, so its sensors are the ones named.
        ext_ids.sort(key=lambda k: hass.data[DOMAIN][k] is not linked)
        if ext_ids:
            ext_id = ext_ids[0]
            if ext_id:
                for key in ("power", "status", "energy_imported", "energy_exported"):
                    found = registry.async_get_entity_id("sensor", DOMAIN, f"{ext_id}_{key}")
                    if found:
                        external_ids[key] = found
        # The grid figures name the sensor of the meter that steers, with its
        # history (v1.6.6): the Zendure P1 meter's total or the external one.
        grid = None
        source = controller.grid_source() if controller is not None else METER_SOURCE_ZENDURE
        zendure_grid = f"sensor.{meter}grid_power_total" if meter else None
        external_grid = external_ids.get("power") if linked is not None or not meter else None
        if source == METER_SOURCE_EXTERNAL:
            grid = external_grid or zendure_grid
        else:
            grid = zendure_grid or external_grid
        result = await async_create_dashboard(
            hass,
            # Disabled entities have no state; a card for one says "not found".
            known={
                e.entity_id for e in registry.entities.values()
                if e.platform == DOMAIN and e.disabled_by is None
            },
            external_ids=external_ids,
            battery=battery,
            meter=meter,
            external=bool(external_meters(hass)),
            ceiling=min(max(ceiling, 800), 5000),
            price=price,
            solar_power=entry.options.get(OPT_SOLAR_POWER) or None,
            solar_energy=entry.options.get(OPT_SOLAR_ENERGY) or None,
            own_pv=_own_pv(hass, registry, device_id),
            replace=replace,
            grid=grid,
        )
        # Unasked, a dashboard that is already there needs no message.
        if requested or result != EXISTS:
            notify(hass, result, battery)
    finally:
        hass.data.pop(DASHBOARD_BUSY, None)
        hass.config_entries.async_update_entry(
            entry,
            options={
                **entry.options,
                OPT_CREATE_DASHBOARD: False,
                OPT_REBUILD_DASHBOARD: False,
                OPT_DASHBOARD_OFFERED: True,
            },
        )
