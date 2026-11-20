"""A new Price Control expects the spot price excl. VAT (v1.8.4).

The recommended route is EPEX Spot *Market price*, which is excl. VAT. Only a
new Price Control gets it as the default; an existing one keeps what it stored,
and an entry that never stored a choice reads spot_vat as before.
"""

import inspect

from custom_components.zendure_restapi import config_flow
from custom_components.zendure_restapi.const import DEFAULTS, NEW_PRICE_CONTAINS, OPT_PRICE_CONTAINS, PRICE_CONTAINS_OPTIONS


def test_new_entries_expect_the_spot_price_excl_vat():
    assert NEW_PRICE_CONTAINS == "spot"
    assert NEW_PRICE_CONTAINS in PRICE_CONTAINS_OPTIONS


def test_existing_entries_keep_the_old_fallback():
    assert DEFAULTS[OPT_PRICE_CONTAINS] == "spot_vat"


def test_the_flow_offers_it_and_the_options_keep_the_stored_choice():
    source = inspect.getsource(config_flow)
    assert source.count("NEW_PRICE_CONTAINS") >= 3  # import, stored default, form default
    assert "default=entry.options.get(OPT_PRICE_CONTAINS, DEFAULTS[OPT_PRICE_CONTAINS])" in source
