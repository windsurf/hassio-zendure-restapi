"""The sample dashboards in dashboard/ are generated (since v1.8.0).

The YAML files for those who paste a dashboard by hand are built with the
integration's own build_config and cluster_config by
scripts/build_dashboard_yaml.py. A change to the template or the dashboard
code without running the script fails here.
"""

import importlib.util
import json
from pathlib import Path

from custom_components.zendure_restapi import dashboard as D

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "build_dashboard_yaml.py"


def load_script():
    spec = importlib.util.spec_from_file_location("build_dashboard_yaml", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_reads_the_template_the_integration_reads():
    assert D.TEMPLATE == ROOT / "custom_components" / "zendure_restapi" / "dashboard_zendure_restapi.json"
    assert D.TEMPLATE.is_file()


def test_the_yaml_is_what_the_template_gives():
    script = load_script()
    current = script.YAML_FILE.read_text(encoding="utf-8")
    assert current == script.render(current), "run scripts/build_dashboard_yaml.py"


def test_the_yaml_carries_the_version_of_the_manifest():
    script = load_script()
    version = json.loads(script.MANIFEST.read_text(encoding="utf-8"))["version"]
    assert f"# ZENDURE RESTAPI DASHBOARD - v{version}\n" in script.YAML_FILE.read_text(encoding="utf-8")


# ── Cluster samples (since v1.8.0) ───────────────────────────────────────────
# dashboard/battery_cluster.yaml and dashboard/virtual_cluster.yaml are built
# by the same script with the integration's own cluster_config, on a stand-in
# Home Assistant with one SolarFlow 3000 Mix AC+ and its P1 meter.


def test_the_cluster_samples_are_what_the_integration_builds():
    script = load_script()
    for path, builder in script.SAMPLES:
        current = path.read_text(encoding="utf-8")
        assert current == script.render(current, builder), f"run scripts/build_dashboard_yaml.py ({path.name})"


def test_the_cluster_samples_carry_the_version_of_the_manifest():
    script = load_script()
    version = json.loads(script.MANIFEST.read_text(encoding="utf-8"))["version"]
    assert f"# BATTERY CLUSTER DASHBOARD - v{version}\n" in script.CLUSTER_FILE.read_text(encoding="utf-8")
    assert f"# VIRTUAL CLUSTER DASHBOARD - v{version}\n" in script.VIRTUAL_FILE.read_text(encoding="utf-8")


def test_both_clusters_have_their_own_price_control_tab():
    script = load_script()
    cluster, virtual = script.build_cluster(), script.build_virtual()
    assert [v["title"] for v in cluster["views"]] == ["Cluster", "Price Control", "Zendure SolarFlow 3000 Mix AC+"]
    assert [v["title"] for v in virtual["views"]] == [
        "Cluster", "Price Control", "Virtual copy 1", "Virtual copy 2", "Virtual copy 3"]
    assert "switch.price_control_battery_cluster" in json.dumps(cluster["views"][1])
    assert "switch.price_control_virtual_cluster" in json.dumps(virtual["views"][1])
