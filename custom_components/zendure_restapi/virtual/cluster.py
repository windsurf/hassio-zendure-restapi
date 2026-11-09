"""A virtual cluster: copies of a real battery under a cluster of their own (v1.7.1).

    real (unchanged)                       virtual (writes nothing real)
    P1 meter ──┐                           virtual P1 = house x factor - clones
               ├─► house load ───────────►       │
    battery ───┘   (P1 + battery power)    cluster (cluster/, unchanged)    
                                            ├─ clone 1  twin, real SoC at start
                                            ├─ clone 2  twin SoC + offset
                                            └─ clone 3  twin SoC + offset

Each clone is the integration's own coordinator and controller on a VirtualDevice
instead of the network, so it regulates with exactly the code of the real
battery and with the real battery's own tuning. The group logic is the
battery cluster of v1.7.0, unchanged. The clones are kept here, not in
hass.data, so nothing that links meters, members or the grid claim of the real
batteries ever sees them.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send

from ..const import (
    CONF_MAX_SHARE,
    CONF_PHASE,
    CONF_PRIORITY,
    CONF_SCAN_INTERVAL,
    CONF_SOURCE_BATTERY,
    CLUSTER_MODES,
    DEFAULT_SCAN_INTERVAL,
    DEFAULTS,
    DOMAIN,
    METER_SOURCE_EXTERNAL,
    MODE_FOLLOW_SOURCE,
    MODE_STANDBY,
    OPT_CHARGE_BUFFER,
    OPT_CLONES,
    OPT_CONCENTRATE_BELOW,
    OPT_DISCHARGE_BUFFER,
    OPT_LOAD_FACTOR,
    OPT_METER_SOURCE,
    OPT_OPERATION_MODE,
    OPT_PRICE_CONTROL,
    OPT_REDISTRIBUTE,
    OPT_SOC_OFFSET_1,
    OPT_SOC_OFFSET_2,
    OPT_SOC_OFFSET_3,
    OPT_MAX_SHARE_CLONES,
    MAX_SHARE_LIMIT,
    OPT_RESPONSE_OFFSET_1,
    OPT_RESPONSE_OFFSET_2,
    OPT_RESPONSE_OFFSET_3,
    OPT_START_CHARGE_AT,
    OPT_START_DISCHARGE_AT,
    OPT_START_AFTER,
    OPT_STOP_AFTER,
    OPT_STOP_CHARGE_BELOW,
    OPT_STOP_DISCHARGE_BELOW,
    OPT_TAKE_PART,
    PACK_PREFIX,
    SIGNAL_CLUSTER,
    SIGNAL_EXTERNALS_CHANGED,
    VIRTUAL_MODES,
    MAX_CLONES,
)
from ..controller import ZendureController
from ..coordinator import ZendureCoordinator
from ..cluster.contract import GroupSettings
from ..cluster.runtime import ClusterCoordinator
from ..member import ZendureMember
from ..registry import model_name, pack_model
from ..settings import SettingsView, ZendureSettings
from .api import VirtualApi
from .device import VirtualDevice
from .meter import VirtualMeter

_LOGGER = logging.getLogger(__name__)

RUNTIME_SUFFIX = "_runtime"
# A clone's start-of-test state of charge stays inside this band.
SOC_MIN, SOC_MAX = 5.0, 100.0


class MemorySettings(ZendureSettings):
    """A clone's own settings: a copy of the real battery's, kept in memory."""

    def __init__(self, options: dict[str, Any]) -> None:  # noqa: D107 - no entry behind it
        self._hass = None
        self._entry = SimpleNamespace(options=dict(options))

    def set(self, key: str, value: Any) -> None:
        self._entry.options[key] = value

    def set_many(self, values: dict[str, Any]) -> None:
        self._entry.options.update(values)


class VirtualSettings(ZendureSettings):
    """The virtual cluster's own settings; its Operation mode may follow the source."""

    def get(self, key: str) -> Any:
        if key == OPT_OPERATION_MODE:
            value = self._entry.options.get(key, MODE_FOLLOW_SOURCE)
            return value if value in VIRTUAL_MODES else MODE_FOLLOW_SOURCE
        return super().get(key)


# The settings a Virtual cluster has entities for, on the cluster and on its copies
# (v1.7.3): what *Reset settings* under Configure takes back to the defaults. A key
# that is removed from the options falls back to its default, so the Operation mode
# returns to Follow source and not to the standby of a real battery.
VIRTUAL_RESET_KEYS = (
    OPT_OPERATION_MODE, OPT_CHARGE_BUFFER, OPT_DISCHARGE_BUFFER,
    OPT_START_DISCHARGE_AT, OPT_START_CHARGE_AT,
    OPT_STOP_DISCHARGE_BELOW, OPT_STOP_CHARGE_BELOW, OPT_STOP_AFTER, OPT_START_AFTER,
    OPT_CONCENTRATE_BELOW, OPT_REDISTRIBUTE,
    OPT_CLONES, OPT_LOAD_FACTOR,
    OPT_SOC_OFFSET_1, OPT_SOC_OFFSET_2, OPT_SOC_OFFSET_3,
    OPT_RESPONSE_OFFSET_1, OPT_RESPONSE_OFFSET_2, OPT_RESPONSE_OFFSET_3,
    *OPT_MAX_SHARE_CLONES,
)


class GroupView:
    """What a clone reads as its group: the cluster's settings, the mode resolved,
    and always the virtual P1 as its meter."""

    def __init__(self, settings: VirtualSettings, mode) -> None:
        self._settings = settings
        self._mode = mode

    def get(self, key: str) -> Any:
        if key == OPT_OPERATION_MODE:
            return self._mode()
        if key == OPT_METER_SOURCE:
            return METER_SOURCE_EXTERNAL
        return self._settings.get(key)

    def get_int(self, key: str) -> int:
        try:
            return int(self.get(key))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    def get_float(self, key: str) -> float:
        try:
            return float(self.get(key))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))


class VirtualCoordinator(ZendureCoordinator):
    """The poll of a clone. No config entry of its own: no stored measurements,
    no repairs, no Price Control for a battery that does not exist."""

    def __init__(self, hass: HomeAssistant, api: VirtualApi, scan_interval: int) -> None:
        super().__init__(hass, api, scan_interval)
        self.config_entry = None


# The device name of a copy, followed by its number (v1.7.3).
COPY_NAME = "Virtual copy"
# The device names of v1.7.1/v1.7.2, slugified: Virtual Mix n, Virtual battery n.
OLD_COPY_PREFIXES = ("virtual_mix_", "virtual_battery_")


class Clone:
    """One copy of the real battery."""

    def __init__(self, index: int, name: str, model: str, device: VirtualDevice, coordinator: VirtualCoordinator,
                 settings: MemorySettings, view: SettingsView, controller: ZendureController,
                 member: ZendureMember) -> None:
        self.index = index
        self.name = name
        self.model = model
        self.device = device
        self.coordinator = coordinator
        self.settings = settings
        self.view = view
        self.controller = controller
        self.member = member
        self.unsub: Any = None
        # Start SoC minus the real battery's SoC at the last start or Reset (v1.7.2).
        self.start_offset: float | None = None

    @property
    def device_id(self) -> str:
        return self.device.serial


class VirtualCluster:
    """The runtime of one Virtual cluster entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.settings = VirtualSettings(hass, entry)
        self.signal = f"{SIGNAL_CLUSTER}_{entry.entry_id}"
        self.source_id = str(entry.data.get(CONF_SOURCE_BATTERY) or "")
        self.source: ZendureController | None = None
        self.source_settings: ZendureSettings | None = None
        self.source_name = ""
        self.clones: list[Clone] = []
        self.group = GroupView(self.settings, self.mode)
        self.cluster = ClusterCoordinator(hass, self.group_settings, self._on_decision)
        self.cluster.trace_virtual = self._trace_columns
        self.meter: VirtualMeter | None = None
        self._unsubs: list[Any] = []
        # How the copies started (v1.7.2): the real SoC, the centring and the shift.
        self.start_info: dict[str, Any] = {}
        self.start_offsets: list[float] = []

    # ── What the entities read ──────────────────────────────────────────

    @property
    def status(self) -> str:
        return self.cluster.status

    @property
    def attributes(self) -> dict[str, Any]:
        return {**self.cluster.attributes, **self.start_info}

    @property
    def readings(self):
        return self.cluster.readings

    @property
    def members(self):
        return self.cluster.members

    @property
    def group_power(self):
        return self.cluster.group_power

    @property
    def group_soc(self):
        return self.cluster.group_soc

    @property
    def soc_spread(self):
        return self.cluster.soc_spread

    def mode(self) -> str:
        """The mode the group runs: its own, or the real battery's."""
        mode = str(self.settings.get(OPT_OPERATION_MODE))
        if mode != MODE_FOLLOW_SOURCE:
            return mode
        if self.source_settings is None:
            return MODE_STANDBY
        real = self.source_settings.get(OPT_OPERATION_MODE)
        # manual follows a number of its own; the group stands still for it.
        return real if real in CLUSTER_MODES else MODE_STANDBY

    def group_settings(self) -> GroupSettings:
        s = self.settings
        return GroupSettings(
            mode=self.mode(),
            charge_buffer=s.get_int(OPT_CHARGE_BUFFER),
            discharge_buffer=s.get_int(OPT_DISCHARGE_BUFFER),
            start_charge_at=s.get_int(OPT_START_CHARGE_AT),
            start_discharge_at=s.get_int(OPT_START_DISCHARGE_AT),
            concentrate_below=s.get_int(OPT_CONCENTRATE_BELOW),
            redistribute_s=s.get_int(OPT_REDISTRIBUTE),
            start_after=s.get_int(OPT_START_AFTER),
        )

    def load_factor(self) -> float:
        try:
            return max(1.0, min(3.0, float(self.settings.get(OPT_LOAD_FACTOR))))
        except (TypeError, ValueError):
            return 1.0

    def _trace_columns(self) -> dict[str, Any]:
        """The Virtual cluster's own trace columns (v1.7.5).

        house_w is the real house load (real meter plus the real battery);
        the copies see it times the Load factor, minus what they deliver, as
        virtual_p1_w.
        """
        data = (self.meter.data or {}) if self.meter is not None else {}
        return {
            "house_w": data.get("house", ""),
            "real_grid_w": data.get("real_grid", ""),
            "virtual_p1_w": data.get("power", ""),
            "load_factor": self.load_factor(),
        }

    def clone_count(self) -> int:
        try:
            return max(1, min(MAX_CLONES, int(self.settings.get(OPT_CLONES))))
        except (TypeError, ValueError):
            return MAX_CLONES

    def start_socs(self) -> list[float]:
        """The start SoC of every copy (v1.7.2).

        Centred on the real state of charge: each copy gets the real SoC plus
        its own offset minus the mean offset, so the copies average the real
        battery. Then the whole set shifts until it lies within 5-100 %, so the
        differences between the copies stay the offsets that were set. Only a
        set wider than 95 points is cut: the lowest on 5 %, the rest at most
        100 %. Only the differences between the offsets count: with one copy
        its offset centres away, and it starts on the real SoC. Kept in `start_info` and `start_offsets`.
        """
        real = self._source_soc()
        count = self.clone_count()
        offsets = [self._offset(OPT_SOC_OFFSET_1), self._offset(OPT_SOC_OFFSET_2),
                   self._offset(OPT_SOC_OFFSET_3)][:count]
        centre = sum(offsets) / len(offsets)
        raw = [real + offset - centre for offset in offsets]
        low, high = min(raw), max(raw)
        if low < SOC_MIN or high - low > SOC_MAX - SOC_MIN:
            shift = SOC_MIN - low
        elif high > SOC_MAX:
            shift = SOC_MAX - high
        else:
            shift = 0.0
        socs = [max(SOC_MIN, min(SOC_MAX, value + shift)) for value in raw]
        self.start_info = {
            "start_soc_real": round(real, 1),
            "start_centre": round(-centre, 2),
            "start_shift": round(shift, 2),
        }
        self.start_offsets = [round(soc - real, 1) for soc in socs]
        _LOGGER.info(
            "Virtual cluster start: real SoC %.1f %%, centring %+.2f, shift %+.2f, copies %s",
            real, -centre, shift, [round(soc, 1) for soc in socs],
        )
        return socs

    def _offset(self, key: str) -> float:
        try:
            return float(self.settings.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

    def max_share(self, index: int) -> int:
        """Max share of clone `index` in W; 0 is its own maximum (v1.7.2)."""
        if not 1 <= index <= len(OPT_MAX_SHARE_CLONES):
            return 0
        try:
            return max(0, min(MAX_SHARE_LIMIT, int(self.settings.get(OPT_MAX_SHARE_CLONES[index - 1]) or 0)))
        except (TypeError, ValueError):
            return 0

    def apply_max_share(self, index: int) -> None:
        """A changed Max share counts at once: from the next reading of the clone,
        for its own loop and for the cluster's split."""
        value = self.max_share(index)
        for clone in self.clones:
            if clone.index == index:
                clone.member.max_share = value
                if clone.view.group is not None:
                    clone.view.max_share = value

    def response_offset(self, index: int) -> float:
        """Extra seconds before a write is in clone `index` (copy 1 too since v1.7.2, 0 by default)."""
        key = {1: OPT_RESPONSE_OFFSET_1, 2: OPT_RESPONSE_OFFSET_2, 3: OPT_RESPONSE_OFFSET_3}.get(index)
        if key is None:
            return 0.0
        try:
            return max(0.0, min(10.0, float(self.settings.get(key) or 0)))
        except (TypeError, ValueError):
            return 0.0

    def _source_soc(self) -> float:
        data = (self.source.coordinator.data or {}) if self.source is not None else {}
        try:
            return float(data.get("electricLevel"))
        except (TypeError, ValueError):
            return 50.0

    # ── Life cycle ──────────────────────────────────────────────────────

    def source_ready(self) -> bool:
        runtime = self.hass.data.get(DOMAIN, {}).get(f"{self.source_id}{RUNTIME_SUFFIX}")
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.source_id)
        return bool(runtime) and coordinator is not None and bool(getattr(coordinator, "raw", None))

    async def async_start(self) -> None:
        store = self.hass.data[DOMAIN]
        runtime = store[f"{self.source_id}{RUNTIME_SUFFIX}"]
        self.source = runtime["controller"]
        self.source_settings = runtime["settings"]
        source_entry = self.hass.config_entries.async_get_entry(self.source_id)
        self.source_name = source_entry.title if source_entry is not None else "battery"
        source_coord: ZendureCoordinator = self.source.coordinator
        model = model_name(source_coord.product) if source_coord.product else "SolarFlow"
        capacity = self._capacity()
        self.energy = self.energy_per_percent()
        scan = int((source_entry.options if source_entry else {}).get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

        self.meter = VirtualMeter(self.source, lambda: [c.device for c in self.clones], self.load_factor)
        self.meter.entry_id = self.entry.entry_id

        own = dict(source_entry.options) if source_entry is not None else {}
        own.update({
            OPT_OPERATION_MODE: MODE_STANDBY,
            OPT_PRICE_CONTROL: False,
            OPT_TAKE_PART: True,
        })
        socs = self.start_socs()
        tail = self.entry.entry_id[-6:].upper()
        for index, soc in enumerate(socs, start=1):
            serial = f"SIMULATED{index}{tail}"
            device = VirtualDevice(source_coord.raw, serial, soc, capacity,
                               extra_delay=lambda i=index: self.response_offset(i),
                               wh_per_percent=self.energy["wh_per_percent"],
                               charge_efficiency=self.energy["charge_efficiency"])
            coordinator = VirtualCoordinator(self.hass, VirtualApi(device), scan)
            settings = MemorySettings(own)
            view = SettingsView(settings)
            controller = ZendureController(coordinator, view)
            controller.virtual = True
            # A fixed name, whatever the model (v1.7.3): the entity ids stay the
            # same when another battery is copied. Until v1.7.2 Virtual Mix n,
            # or Virtual battery n for a model without "Mix".
            name = f"{COPY_NAME} {index}"
            data = {CONF_PHASE: f"l{index}", CONF_PRIORITY: 6 if index == 1 else 5,
                    CONF_MAX_SHARE: self.max_share(index)}
            member = ZendureMember(f"clone{index}", name, data, controller, view, settings)
            self.clones.append(Clone(index, name, model, device, coordinator, settings, view, controller, member))
            self.clones[-1].start_offset = self.start_offsets[index - 1]

        for clone in self.clones:
            await clone.coordinator.async_refresh()
            clone.controller.attach_external(self.meter)
            clone.member.join(self.group, self.entry.title, self.cluster.secondaries_against)

            def _on_update(c: Clone = clone) -> None:
                c.controller.observe()
                self.hass.async_create_task(c.controller.async_run())

            clone.unsub = clone.coordinator.async_add_listener(_on_update)

        self.cluster.set_members([c.member for c in self.clones])
        self.meter.async_link()
        self._unsubs.append(async_dispatcher_connect(self.hass, SIGNAL_EXTERNALS_CHANGED, self._on_relinked))
        self.cluster.start()
        _LOGGER.info(
            "Virtual cluster: %d clone(s) of %s, %.1f kWh each, SoC %s, load factor %.1f, "
            "%.1f Wh per %% discharging and %.1f charging (%s)",
            len(self.clones), self.source_name, capacity, [round(s) for s in socs], self.load_factor(),
            self.energy["wh_per_percent_out"], self.energy["wh_per_percent_in"], self.energy["source"],
        )

    async def async_stop(self) -> None:
        self.cluster.stop()
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for clone in self.clones:
            if clone.unsub is not None:
                clone.unsub()
            clone.member.leave()
            clone.controller.detach_meter()
            await clone.coordinator.async_shutdown()
        if self.meter is not None:
            self.meter.async_stop()

    @callback
    def _on_relinked(self) -> None:
        """A battery or meter came or went: follow the real battery's meters,
        and set up again if the real battery itself was set up anew."""
        runtime = self.hass.data.get(DOMAIN, {}).get(f"{self.source_id}{RUNTIME_SUFFIX}")
        # While the real battery is away (a reload under way) the cluster waits;
        # once it is back as a new controller, the cluster is set up on that one.
        if runtime is not None and runtime.get("controller") is not self.source:
            self.hass.config_entries.async_schedule_reload(self.entry.entry_id)
            return
        if self.meter is not None:
            self.meter.async_link()

    @callback
    def _on_decision(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    def reset_settings(self) -> bool:
        """Every setting of the Virtual cluster and its copies back to its default (v1.7.3).

        The copies keep running: their offsets and response times count from the next
        start or *Reset*. Returns True when the number of copies changes, because the
        entry then sets up again and the copies start afresh anyway.
        """
        before = self.clone_count()
        options = {k: v for k, v in self.entry.options.items() if k not in VIRTUAL_RESET_KEYS}
        self.hass.config_entries.async_update_entry(self.entry, options=options)
        async_dispatcher_send(self.hass, self.signal)
        _LOGGER.info("Virtual cluster settings reset to their defaults")
        return self.clone_count() != before

    async def async_reset(self) -> None:
        """Every clone back to the start: still, limits 0, the twin on the real SoC."""
        for clone, soc, offset in zip(self.clones, self.start_socs(), self.start_offsets):
            clone.device.reset(soc)
            clone.start_offset = offset
            await clone.coordinator.async_refresh()
        self.cluster.distributor.primary = None
        self.cluster.distributor.last_split = None
        _LOGGER.info("Virtual cluster reset: SoC %s", [round(c.device.soc) for c in self.clones])
        await self.cluster.async_evaluate()

    def _capacity(self) -> float:
        """Usable kWh of the real battery: measured, else the nominal pack capacity."""
        measured = getattr(self.source.measure, "usable_capacity", None) if self.source else None
        if measured:
            return float(measured)
        coordinator = self.source.coordinator if self.source else None
        data = (coordinator.data or {}) if coordinator else {}
        total = 0.0
        for index in range(1, (coordinator.pack_count if coordinator else 0) + 1):
            prefix = f"{PACK_PREFIX}{index}."
            _, kwh = pack_model(data.get(f"{prefix}sn"), data.get(f"{prefix}packType"))
            total += kwh or 0.0
        return total or 8.0

    def energy_per_percent(self) -> dict[str, Any]:
        """Wh per percent of the copies, from the real battery's measurements (v1.7.2).

        Discharging: the last measured usable capacity over the range it was
        measured on (full to empty, e.g. 100 -> 5 %). Charging: that divided by
        the measured round trip efficiency of the cells. Without a measurement
        the nominal capacity over 100 %, both ways, as before.
        """
        measure = getattr(self.source, "measure", None) if self.source else None
        capacities = list(getattr(measure, "capacities", None) or [])
        last = capacities[-1] if capacities else None
        wh = None
        span = None
        if isinstance(last, dict):
            try:
                span = float(last.get("from_soc")) - float(last.get("to_soc"))
                kwh = float(last.get("kwh"))
            except (TypeError, ValueError):
                span = None
            if span and span >= 50 and kwh > 0:
                wh = kwh * 1000.0 / span
        efficiency = 1.0
        if wh is not None and measure is not None:
            try:
                rt = measure.round_trip("dc")
            except Exception:  # noqa: BLE001 - a measurement that is not there yet
                rt = None
            if rt and 50 <= float(rt) <= 100:
                efficiency = float(rt) / 100.0
        if wh is None:
            return {"wh_per_percent": None, "charge_efficiency": 1.0, "source": "nominal",
                    "wh_per_percent_out": round(self._capacity() * 10, 1),
                    "wh_per_percent_in": round(self._capacity() * 10, 1)}
        return {"wh_per_percent": wh, "charge_efficiency": efficiency, "source": "measured",
                "wh_per_percent_out": round(wh, 1), "wh_per_percent_in": round(wh / efficiency, 1)}

    def diagnostics(self) -> dict[str, Any]:
        return {
            "source": self.source_name,
            "energy_per_percent": {k: v for k, v in getattr(self, "energy", {}).items()
                                   if k in ("source", "wh_per_percent_out", "wh_per_percent_in")},
            "mode": self.mode(),
            "load_factor": self.load_factor(),
            "meter": self.meter.diagnostics() if self.meter else None,
            "clones": [
                {"name": c.name, "soc": round(c.device.soc, 1), "power": round(c.device.power),
                 "writes": c.device.writes, "status": c.controller.state.status}
                for c in self.clones
            ],
        }
