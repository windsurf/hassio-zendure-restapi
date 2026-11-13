"""Generate the sample dashboards in dashboard/ from the integration's own builders (since v1.8.0).

The integration builds its dashboards itself: the battery dashboard from
custom_components/zendure_restapi/dashboard_zendure_restapi.json, the
dashboard of a battery cluster and of a Virtual cluster from that template and
cluster_dashboard.py. The YAML files in dashboard/ are the same dashboards for a
reference installation, for those who paste a dashboard by hand:

    dashboard/zendure_restapi.yaml   a SolarFlow 3000 Mix AC+ with a Zendure P1
                                     meter that steers, an external meter and
                                     Price Control
    dashboard/battery_cluster.yaml   a battery cluster of that battery, with a
                                     Price Control of its own
    dashboard/virtual_cluster.yaml   a Virtual cluster of three copies of that
                                     battery, with a Price Control of its own

The battery dashboard is built with build_config; the two cluster dashboards
with cluster_config, the very function Home Assistant calls, on a stand-in of
Home Assistant that holds the reference installation: its config entries, and
an entity registry filled with the entities the integration itself creates for
it. So the samples cannot drift from what the integration builds. The comment
block at the top of each file is kept, with the version taken from
manifest.json.

    python scripts/build_dashboard_yaml.py          # write the YAML files
    python scripts/build_dashboard_yaml.py --check  # exit 1 when one differs

tests/test_dashboard_yaml.py runs the check.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from homeassistant.const import Platform  # noqa: E402
from homeassistant.helpers import entity_registry as er  # noqa: E402
from homeassistant.util import slugify  # noqa: E402

from custom_components.zendure_restapi import dashboard as D  # noqa: E402

DASHBOARD_DIR = ROOT / "dashboard"
YAML_FILE = DASHBOARD_DIR / "zendure_restapi.yaml"
CLUSTER_FILE = DASHBOARD_DIR / "battery_cluster.yaml"
VIRTUAL_FILE = DASHBOARD_DIR / "virtual_cluster.yaml"
MANIFEST = ROOT / "custom_components" / "zendure_restapi" / "manifest.json"
# The meter that steers on the reference installation.
REFERENCE_GRID = f"sensor.{D.TEMPLATE_P1}grid_power_total"
ENTITY_ID = re.compile(r'"((?:[a-z_]+)\.[a-z0-9_]+)"')
VERSION_LINE = re.compile(r"^(# [A-Z ]+ DASHBOARD - v)[0-9.]+$", re.MULTILINE)


class _Dumper(yaml.SafeDumper):
    """Block style throughout; multi-line text as a literal block."""


def _string(dumper: yaml.SafeDumper, value: str) -> yaml.Node:
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    if value.startswith("~"):
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="'")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


_Dumper.add_representer(str, _string)


def _name_after_entity(node: Any) -> Any:
    """Put a card's name right after its entity, as a person would write it."""
    if isinstance(node, list):
        return [_name_after_entity(item) for item in node]
    if not isinstance(node, dict):
        return node
    node = {key: _name_after_entity(value) for key, value in node.items()}
    if "entity" in node and "name" in node and node.get("type") in D.NAMED_CARDS:
        ordered: dict[str, Any] = {}
        for key, value in node.items():
            if key == "name":
                continue
            ordered[key] = value
            if key == "entity":
                ordered["name"] = node["name"]
        node = ordered
    return node


def _template() -> dict[str, Any]:
    return json.loads(D.TEMPLATE.read_text(encoding="utf-8"))


def _template_ids(template: dict[str, Any]) -> tuple[set[str], set[str]]:
    """Every entity the template names, and the custom cards it uses.

    Every entity exists on the reference installation but for the external solar
    sensor: the reference battery reads its solar power from itself.
    """
    text = json.dumps(template)
    known = set(ENTITY_ID.findall(text)) - {f"sensor.{D.TEMPLATE_BATTERY}{D.SOLAR_PLACEHOLDER}"}
    return known, set(re.findall(r'"(custom:[a-z0-9-]+)"', text))


def build() -> dict[str, Any]:
    """The battery dashboard for the reference devices, every entity present."""
    template = _template()
    known, custom_cards = _template_ids(template)
    config = D.build_config(
        template,
        known=known,
        battery=D.TEMPLATE_BATTERY,
        meter=D.TEMPLATE_P1,
        external=True,
        ceiling=D.TEMPLATE_CEILING,
        price=D.TEMPLATE_PRICE,
        custom_cards=custom_cards,
        card_naming=True,
        grid=REFERENCE_GRID,
    )
    return _name_after_entity(config)


# ── A stand-in of Home Assistant with the reference installation ─────────────
# Only what cluster_config and dashboard_args read: config entries with their
# data, options and subentries, the integration's runtime objects in hass.data,
# and the entity registry. The registry holds the entities the integration's
# own platform code creates for the clusters, named as Home Assistant names
# them (device name and entity name, slugified), and the entities of the
# battery, its meters and Price Control as the template names them.

BATTERY_ID = "reference_battery"
BATTERY_DEVICE = "reference_battery_device"
METER_ID = "reference_meter"
METER_DEVICE = "reference_meter_device"
EXTERNAL_ID = "reference_external"
CLUSTER_ID = "reference_cluster"
PRICE_ID = "reference_price"
CLUSTER_PRICE_ID = "reference_cluster_price"
COPIES = 3


class _Entry(SimpleNamespace):
    """A config entry: what the builders read of one."""


class _RegistryEntry(SimpleNamespace):
    """One row of the entity registry."""


class _Registry:
    def __init__(self) -> None:
        self.entities: dict[str, _RegistryEntry] = {}
        self._ids: dict[tuple[str, str], str] = {}

    def add(self, entity_id: str, unique_id: str | None = None) -> None:
        from custom_components.zendure_restapi.const import DOMAIN

        unique_id = unique_id or f"template:{entity_id}"
        self.entities[entity_id] = _RegistryEntry(
            entity_id=entity_id, unique_id=unique_id, platform=DOMAIN, disabled_by=None,
        )
        self._ids[(entity_id.split(".", 1)[0], unique_id)] = entity_id

    def async_get_entity_id(self, platform: str, _domain: str, unique_id: str) -> str | None:
        return self._ids.get((platform, unique_id))


class _ConfigEntries:
    def __init__(self, entries: list[_Entry]) -> None:
        self._entries = {e.entry_id: e for e in entries}

    def async_get_entry(self, entry_id: str) -> _Entry | None:
        return self._entries.get(entry_id)

    def async_entries(self, _domain: str | None = None) -> list[_Entry]:
        return list(self._entries.values())


def _entry(entry_id: str, title: str, data: dict[str, Any], subentries: dict[str, Any] | None = None) -> _Entry:
    return _Entry(entry_id=entry_id, title=title, data=data, options={}, unique_id=entry_id,
                  subentries=subentries or {}, runtime_data=None)


def _register(registry: _Registry, platform: Platform, entities: list[Any]) -> None:
    """Name each entity as Home Assistant names a new one: device name and entity name."""
    for entity in entities:
        info = getattr(entity, "_attr_device_info", None) or {}
        name = entity.suggested_object_id or ""
        object_id = slugify(f"{info.get('name', '')} {name}".strip())
        registry.add(f"{platform.value}.{object_id}", entity.unique_id)


def _battery(registry: _Registry, store: dict[str, Any]) -> list[_Entry]:
    """The reference battery with its Zendure P1 meter and external meter, as the template names them."""
    from custom_components.zendure_restapi import RUNTIME_SUFFIX
    from custom_components.zendure_restapi.const import (
        CONF_DEVICE_TYPE,
        CONF_MODEL,
        DEVICE_TYPE_METER,
        METER_SOURCE_ZENDURE,
    )
    from custom_components.zendure_restapi.coordinator import ZendureCoordinator
    from custom_components.zendure_restapi.p1meter import ExternalMeter

    known, _ = _template_ids(_template())
    battery_ids = {e for e in known if not e.split(".", 1)[1].startswith(D.TEMPLATE_PRICE)}
    for entity_id in sorted(battery_ids):
        registry.add(entity_id)
    # The ids the builders look up by unique id.
    registry.add(f"sensor.{D.TEMPLATE_BATTERY}controller_status", f"{BATTERY_DEVICE}_controller_status")
    registry.add(f"sensor.{D.TEMPLATE_P1}grid_power_total", f"{METER_DEVICE}_total_power")
    registry.add(f"sensor.{D.TEMPLATE_BATTERY}{D.PV_ENERGY}", f"{BATTERY_DEVICE}_energy_pv")
    for key in ("power", "status", "energy_imported", "energy_exported"):
        registry.add(f"sensor.external_p1_meter_{key}", f"{EXTERNAL_ID}_{key}")

    battery = ZendureCoordinator.__new__(ZendureCoordinator)
    battery.device_id, battery.device_type = BATTERY_DEVICE, "battery"
    battery.data = {"inverseMaxPower": D.TEMPLATE_CEILING}
    meter = ZendureCoordinator.__new__(ZendureCoordinator)
    meter.device_id, meter.device_type, meter.data = METER_DEVICE, DEVICE_TYPE_METER, {}
    external = ExternalMeter.__new__(ExternalMeter)
    store[BATTERY_ID] = battery
    store[METER_ID] = meter
    store[EXTERNAL_ID] = external
    store[f"{BATTERY_ID}{RUNTIME_SUFFIX}"] = {
        "controller": SimpleNamespace(external=external, grid_source=lambda: METER_SOURCE_ZENDURE),
    }
    return [
        _entry(BATTERY_ID, "Zendure SolarFlow 3000 Mix AC+", {CONF_MODEL: "SolarFlow 3000 Mix AC+"}),
        _entry(METER_ID, "Zendure P1 Meter (3-phase)", {CONF_DEVICE_TYPE: DEVICE_TYPE_METER}),
    ]


def _price(registry: _Registry, price_id: str, cluster_id: str, cluster_name: str) -> _Entry:
    """The cluster's own Price Control: the template's Price Control entities under its device name."""
    from custom_components.zendure_restapi.const import (
        CONF_BATTERY_ENTRY,
        CONF_DEVICE_TYPE,
        DEVICE_TYPE_PRICE,
        PRICE_DEVICE_NAME,
    )

    prefix = f"{slugify(f'{PRICE_DEVICE_NAME} ({cluster_name})')}_"
    known, _ = _template_ids(_template())
    for entity_id in sorted(known):
        platform, object_id = entity_id.split(".", 1)
        if object_id.startswith(D.TEMPLATE_PRICE) and not object_id.startswith(D.TEMPLATE_PRICE + "virtual"):
            registry.add(f"{platform}.{prefix}{object_id[len(D.TEMPLATE_PRICE):]}")
        elif entity_id == f"switch.{D.TEMPLATE_PRICE[:-1]}":
            registry.add(f"switch.{prefix[:-1]}")
    registry.add(f"sensor.{prefix}status", f"{price_id}_price_control_status")
    return _entry(price_id, f"{PRICE_DEVICE_NAME} ({cluster_name})",
                  {CONF_DEVICE_TYPE: DEVICE_TYPE_PRICE, CONF_BATTERY_ENTRY: cluster_id})


def _hass(entries: list[_Entry], store: dict[str, Any]) -> Any:
    from custom_components.zendure_restapi.const import DOMAIN

    return SimpleNamespace(
        data={DOMAIN: store},
        config_entries=_ConfigEntries(entries),
        # The reference battery has PV of its own: its PV energy reads above
        # the mark from which a cluster shows its PV series.
        states=SimpleNamespace(get=lambda entity_id: SimpleNamespace(state="10")
                               if str(entity_id).endswith("_pv_energy") else None),
    )


def _cluster_entities(module: Any, hass: Any, entry: _Entry, registry: _Registry) -> None:
    for platform in (Platform.SELECT, Platform.NUMBER, Platform.BUTTON, Platform.SWITCH, Platform.SENSOR):
        _register(registry, platform, module(platform, hass, entry))


def _with_registry(registry: _Registry, build_it: Any) -> Any:
    """Run a builder with the stand-in registry where Home Assistant's would be."""
    original = er.async_get
    er.async_get = lambda _hass: registry
    try:
        return build_it()
    finally:
        er.async_get = original


def build_cluster() -> dict[str, Any]:
    """The dashboard of a battery cluster of the reference battery, with its own Price Control."""
    from custom_components.zendure_restapi import cluster_dashboard as CD
    from custom_components.zendure_restapi.cluster_entities import cluster_entities
    from custom_components.zendure_restapi.const import (
        CLUSTER_DEVICE_NAME,
        CONF_BATTERY_ENTRY,
        CONF_DEVICE_NAME,
        CONF_DEVICE_TYPE,
        DEVICE_TYPE_CLUSTER,
        SUBENTRY_BATTERY,
    )

    registry, store = _Registry(), {}
    entries = _battery(registry, store)
    member = SimpleNamespace(subentry_type=SUBENTRY_BATTERY, data={CONF_BATTERY_ENTRY: BATTERY_ID})
    cluster = _entry(CLUSTER_ID, CLUSTER_DEVICE_NAME,
                     {CONF_DEVICE_TYPE: DEVICE_TYPE_CLUSTER, CONF_DEVICE_NAME: CLUSTER_DEVICE_NAME},
                     {"member": member})
    entries += [cluster, _price(registry, CLUSTER_PRICE_ID, CLUSTER_ID, CLUSTER_DEVICE_NAME)]
    store[CLUSTER_ID] = SimpleNamespace(trace=SimpleNamespace(active=False, rows=0, path=None))
    hass = _hass(entries, store)

    def make() -> dict[str, Any]:
        _cluster_entities(cluster_entities, hass, cluster, registry)
        template = _template()
        _, custom_cards = _template_ids(template)
        return CD.cluster_config(hass, cluster, template, custom_cards)

    return _name_after_entity(_with_registry(registry, make))


def build_virtual() -> dict[str, Any]:
    """The dashboard of a Virtual cluster of three copies of the reference battery, with its own Price Control."""
    from custom_components.zendure_restapi import cluster_dashboard as CD
    from custom_components.zendure_restapi.const import (
        CONF_DEVICE_NAME,
        CONF_DEVICE_TYPE,
        DEVICE_TYPE_VIRTUAL,
        VIRTUAL_DEVICE_NAME,
    )
    from custom_components.zendure_restapi.coordinator import ZendureCoordinator
    from custom_components.zendure_restapi.settings import ZendureSettings
    from custom_components.zendure_restapi.virtual.cluster import COPY_NAME
    from custom_components.zendure_restapi.virtual.entities import virtual_entities

    registry, store = _Registry(), {}
    entries = _battery(registry, store)
    virtual = _entry(CLUSTER_ID, VIRTUAL_DEVICE_NAME,
                     {CONF_DEVICE_TYPE: DEVICE_TYPE_VIRTUAL, CONF_DEVICE_NAME: VIRTUAL_DEVICE_NAME})
    entries += [virtual, _price(registry, CLUSTER_PRICE_ID, CLUSTER_ID, VIRTUAL_DEVICE_NAME)]
    hass = _hass(entries, store)
    clones = []
    for index in range(1, COPIES + 1):
        coordinator = ZendureCoordinator.__new__(ZendureCoordinator)
        coordinator.device_id, coordinator.device_type, coordinator.data = f"reference_copy_{index}", "battery", {}
        coordinator.hass = hass
        coordinator.api = SimpleNamespace(sn=None, base_url=None)
        coordinator.firmware = None
        clones.append(SimpleNamespace(
            index=index, name=f"{COPY_NAME} {index}", device_id=f"reference_copy_{index}",
            model="SolarFlow 3000 Mix AC+", coordinator=coordinator,
            controller=SimpleNamespace(measure=None), start_offset=0,
        ))
    store[CLUSTER_ID] = SimpleNamespace(
        settings=ZendureSettings(hass, virtual), clones=clones, source_id=BATTERY_ID,
        cluster=SimpleNamespace(trace=SimpleNamespace(active=False, rows=0, path=None)),
    )

    def make() -> dict[str, Any]:
        _cluster_entities(virtual_entities, hass, virtual, registry)
        template = _template()
        _, custom_cards = _template_ids(template)
        return CD.cluster_config(hass, virtual, template, custom_cards)

    return _name_after_entity(_with_registry(registry, make))


# ── Files ───────────────────────────────────────────────────────────────────

SAMPLES = (
    (YAML_FILE, build),
    (CLUSTER_FILE, build_cluster),
    (VIRTUAL_FILE, build_virtual),
)


def render(current: str, builder: Any = build) -> str:
    """The YAML file: the comment block of `current`, then the generated body."""
    header = current[: current.index("\ntitle:") + 1] if "\ntitle:" in current else current
    version = json.loads(MANIFEST.read_text(encoding="utf-8"))["version"]
    header = VERSION_LINE.sub(rf"\g<1>{version}", header)
    body = yaml.dump(
        builder(), Dumper=_Dumper, sort_keys=False, allow_unicode=True,
        default_flow_style=False, width=float("inf"),
    )
    return header + body


def main(argv: list[str]) -> int:
    failed = 0
    for path, builder in SAMPLES:
        current = path.read_text(encoding="utf-8")
        wanted = render(current, builder)
        if "--check" in argv:
            if current != wanted:
                print(f"{path.relative_to(ROOT)} differs from what the integration builds; run {Path(__file__).name}")
                failed = 1
            continue
        if current != wanted:
            path.write_text(wanted, encoding="utf-8")
            print(f"{path.relative_to(ROOT)} written")
    return failed


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
