"""One source for the defaults (v1.8.7): the code and the tables in README2 agree.

`DEFAULTS` holds every default. The day plan takes its own from there, and the
two tables a user reads (Price Control's entities and all buffers, thresholds
and waiting times) must show the same numbers.
"""

import re
from pathlib import Path

from custom_components.zendure_restapi import dayplan
from custom_components.zendure_restapi.const import (
    DEFAULTS,
    OPT_CHARGE_EFFICIENCY,
    OPT_DISCHARGE_EFFICIENCY,
    OPT_EXPORT_FACTOR,
    OPT_EXPORT_SURCHARGE,
    OPT_HOUSE_LOAD,
    OPT_IMPORT_FACTOR,
    OPT_IMPORT_SURCHARGE,
)
from custom_components.zendure_restapi.number import SETTING_NUMBERS
from custom_components.zendure_restapi.price_entities import PRICE_NUMBERS

README2 = (Path(__file__).resolve().parent.parent / "README2.md").read_text(encoding="utf-8")
NUMBER = re.compile(r"[−-]?\d+(?:\.\d+)?")


def _table(header):
    """Rows of the table under `header`: names (first cell) and numbers (the default cell)."""
    lines = README2.splitlines()
    start = lines.index(header)
    column = [cell.strip() for cell in header.strip("|").split("|")].index("Default")
    rows = {}
    for line in lines[start + 2:]:
        if not line.startswith("|"):
            break
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        names = [name.strip(" `") for name in cells[0].split(",")]
        values = [float(v.replace("−", "-")) for v in NUMBER.findall(cells[column])]
        if not values:
            continue
        if len(values) == 1:
            values = values * len(names)
        rows.update(zip(names, values))
    return rows


def test_the_day_plan_takes_the_defaults_of_the_settings():
    tariff = dayplan.Tariff()
    assert (tariff.import_factor, tariff.import_surcharge) == (DEFAULTS[OPT_IMPORT_FACTOR], DEFAULTS[OPT_IMPORT_SURCHARGE])
    assert (tariff.export_factor, tariff.export_surcharge) == (DEFAULTS[OPT_EXPORT_FACTOR], DEFAULTS[OPT_EXPORT_SURCHARGE])
    battery = dayplan.Battery(usable_kwh=8, stored_kwh=4, charge_kw=3, discharge_kw=3)
    assert battery.eta_charge == DEFAULTS[OPT_CHARGE_EFFICIENCY] / 100
    assert battery.eta_discharge == DEFAULTS[OPT_DISCHARGE_EFFICIENCY] / 100
    assert battery.house_kw == DEFAULTS[OPT_HOUSE_LOAD] / 1000


def test_the_price_control_table_shows_the_defaults():
    rows = _table("| Entity | What it does | Default |")
    checked = 0
    for description, key in PRICE_NUMBERS:
        if description.name in rows:
            assert rows[description.name] == float(DEFAULTS[key]), description.name
            checked += 1
    assert checked >= 10


def test_the_table_of_thresholds_shows_the_defaults():
    rows = _table("| Setting | Default | What it holds back |")
    checked = 0
    for description, key in SETTING_NUMBERS:
        if description.name in rows:
            assert rows[description.name] == float(DEFAULTS[key]), description.name
            checked += 1
    assert checked >= 10


def test_the_reset_texts_name_no_numbers():
    """A default that changes is changed in one place, not in eleven languages."""
    import json

    package = Path(__file__).resolve().parent.parent / "custom_components" / "zendure_restapi"
    for path in [package / "strings.json", *sorted((package / "translations").glob("*.json"))]:
        steps = json.loads(path.read_text(encoding="utf-8"))["options"]["step"]
        for step in ("init", "virtual"):
            text = steps[step]["data_description"]["reset_settings"]
            assert not re.search(r"\d+ ?(?:W|s|%)\b", text), f"{path.name} {step}"
