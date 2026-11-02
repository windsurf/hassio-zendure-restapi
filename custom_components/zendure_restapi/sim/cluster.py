"""A virtual cluster: copies of a real battery under a cluster of their own (v1.7.1).

    real (unchanged)                       virtual (writes nothing real)
    P1 meter ──┐                           virtual P1 = house x factor - clones
               ├─► house load ───────────►       │
    battery ───┘   (P1 + battery power)    cluster (cluster/, unchanged)    
                                            ├─ clone 1  twin, real SoC at start
                                            ├─ clone 2  twin SoC + offset
                                            └─ clone 3  twin SoC + offset

Each clone is the integration's own coordinator and controller on a SimDevice
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
    OPT_SOC_OFFSET_2,
    OPT_SOC_OFFSET_3,
    OPT_RESPONSE_OFFSET_2,
    OPT_RESPONSE_OFFSET_3,
    OPT_START_CHARGE_AT,
    OPT_START_DISCHARGE_AT,
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
from .api import SimApi
from .device import SimDevice
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


class SimCoordinator(ZendureCoordinator):
    """The poll of a clone. No config entry of its own: no stored measurements,
    no repairs, no Price Control for a battery that does not exist."""

    def __init__(self, hass: HomeAssistant, api: SimApi, scan_interval: int) -> None:
        super().__init__(hass, api, scan_interval)
        self.config_entry = None


class Clone:
    """One copy of the real battery."""

    def __init__(self, index: int, name: str, model: str, device: SimDevice, coordinator: SimCoordinator,
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
        self.meter: VirtualMeter | None = None
        self._unsubs: list[Any] = []

    # ── What the entities read ──────────────────────────────────────────

    @property
    def status(self) -> str:
        return self.cluster.status

    @property
    def attributes(self) -> dict[str, Any]:
        return self.cluster.attributes

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
        )

    def load_factor(self) -> float:
        try:
            return max(1.0, min(3.0, float(self.settings.get(OPT_LOAD_FACTOR))))
        except (TypeError, ValueError):
            return 1.0

    def clone_count(self) -> int:
        try:
            return max(1, min(MAX_CLONES, int(self.settings.get(OPT_CLONES))))
        except (TypeError, ValueError):
            return MAX_CLONES

    def start_socs(self) -> list[float]:
        """Twin on the real state of charge, the others offset from it."""
        twin = self._source_soc()
        offsets = [0.0, float(self.settings.get(OPT_SOC_OFFSET_2) or 0), float(self.settings.get(OPT_SOC_OFFSET_3) or 0)]
        return [max(SOC_MIN, min(SOC_MAX, twin + off)) for off in offsets[: self.clone_count()]]

    def response_offset(self, index: int) -> float:
        """Extra seconds before a write is in clone `index`; the twin has none."""
        key = {2: OPT_RESPONSE_OFFSET_2, 3: OPT_RESPONSE_OFFSET_3}.get(index)
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
        short = "Mix" if "Mix" in model else "battery"
        capacity = self._capacity()
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
            device = SimDevice(source_coord.raw, serial, soc, capacity,
                               extra_delay=lambda i=index: self.response_offset(i))
            coordinator = SimCoordinator(self.hass, SimApi(device), scan)
            settings = MemorySettings(own)
            view = SettingsView(settings)
            controller = ZendureController(coordinator, view)
            controller.virtual = True
            name = f"Virtual {short} {index}"
            data = {CONF_PHASE: f"l{index}", CONF_PRIORITY: 6 if index == 1 else 5, CONF_MAX_SHARE: 0}
            member = ZendureMember(f"clone{index}", name, data, controller, view, settings)
            self.clones.append(Clone(index, name, model, device, coordinator, settings, view, controller, member))

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
            "Virtual cluster: %d clone(s) of %s, %.1f kWh each, SoC %s, load factor %.1f",
            len(self.clones), self.source_name, capacity, [round(s) for s in socs], self.load_factor(),
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

    async def async_reset(self) -> None:
        """Every clone back to the start: still, limits 0, the twin on the real SoC."""
        for clone, soc in zip(self.clones, self.start_socs()):
            clone.device.reset(soc)
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

    def diagnostics(self) -> dict[str, Any]:
        return {
            "source": self.source_name,
            "mode": self.mode(),
            "load_factor": self.load_factor(),
            "meter": self.meter.diagnostics() if self.meter else None,
            "clones": [
                {"name": c.name, "soc": round(c.device.soc, 1), "power": round(c.device.power),
                 "writes": c.device.writes, "status": c.controller.state.status}
                for c in self.clones
            ],
        }
