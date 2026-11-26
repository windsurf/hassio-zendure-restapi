"""The Price Control settings: exact to seven decimals, within their bounds.

A surcharge such as 0.1308481 (an energy tax incl. VAT plus the supplier's
part) must be stored as entered; it was once rounded to five decimals on entry.
"""

import asyncio

from custom_components.zendure_restapi.const import DEFAULTS, OPT_IMPORT_SURCHARGE
from custom_components.zendure_restapi.price_entities import PRICE_NUMBERS, PriceNumber

NUMBERS = {description.key: (description, key) for description, key in PRICE_NUMBERS}


class Settings:
    def __init__(self):
        self.stored = {}

    def set(self, key, value):
        self.stored[key] = value

    def get_float(self, key):
        return float(self.stored.get(key, DEFAULTS[key]))


def _number(key):
    description, option = NUMBERS[key]
    number = PriceNumber.__new__(PriceNumber)
    number.entity_description = description
    number._settings = Settings()
    number._key = option
    number.async_write_ha_state = lambda: None
    return number


def test_a_surcharge_is_kept_to_seven_decimals():
    number = _number("import_surcharge")
    asyncio.run(number.async_set_native_value(0.1308481))
    assert number.native_value == 0.1308481
    asyncio.run(number.async_set_native_value(0.123456789))
    assert number.native_value == 0.1234568


def test_every_price_step_takes_seven_decimals():
    for key in ("import_surcharge", "export_surcharge", "fixed_import_price", "fixed_export_price"):
        assert NUMBERS[key][0].native_step == 0.0000001, key


def test_every_default_lies_within_its_bounds():
    for description, option in PRICE_NUMBERS:
        default = DEFAULTS[option]
        assert description.native_min_value <= default <= description.native_max_value, description.key


def test_the_import_surcharge_default_is_the_documented_one():
    assert DEFAULTS[OPT_IMPORT_SURCHARGE] == 0.1308481
