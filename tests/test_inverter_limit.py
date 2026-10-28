"""The Inverter power limit is lowered from Home Assistant, never raised (v1.6.10).

`inverseMaxPower` is the battery's ceiling for feeding into the grid. Its
slider reaches as far as the value the battery reports; a higher value is
refused and nothing is written. Without a reported value nothing is written
either. The other ceiling, `chargeMaxLimit`, keeps its range.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.zendure_restapi.const import POWER_CEILING
from custom_components.zendure_restapi.number import NUMBERS, ZendureNumber


def make_number(key: str, data: dict) -> ZendureNumber:
    """A number entity without Home Assistant: description, data and a writer."""
    number = ZendureNumber.__new__(ZendureNumber)
    number.entity_description = next(d for d in NUMBERS if d.key == key)
    number._data_key = key
    number.coordinator = MagicMock()
    number.coordinator.data = data
    number.coordinator.async_write = AsyncMock()
    return number


def test_the_slider_reaches_the_reported_value():
    number = make_number("inverseMaxPower", {"inverseMaxPower": 800})
    assert number.native_max_value == 800


def test_raising_is_refused():
    number = make_number("inverseMaxPower", {"inverseMaxPower": 800})
    with pytest.raises(HomeAssistantError):
        asyncio.run(number.async_set_native_value(2400))
    number.coordinator.async_write.assert_not_called()


def test_lowering_is_written():
    number = make_number("inverseMaxPower", {"inverseMaxPower": 2400})
    asyncio.run(number.async_set_native_value(800))
    number.coordinator.async_write.assert_awaited_once_with("inverseMaxPower", 800)


def test_without_a_reported_value_nothing_is_written():
    number = make_number("inverseMaxPower", {})
    with pytest.raises(HomeAssistantError) as error:
        asyncio.run(number.async_set_native_value(800))
    number.coordinator.async_write.assert_not_called()
    # The same text as v1.7.3: it says the battery has not reported the value yet.
    assert "has not reported it yet" in str(error.value)
    assert "Safe power setting" in str(error.value)


def test_a_raise_names_the_reported_value_in_the_message():
    number = make_number("inverseMaxPower", {"inverseMaxPower": 800})
    with pytest.raises(HomeAssistantError) as error:
        asyncio.run(number.async_set_native_value(2400))
    assert "not raised above 800 W" in str(error.value)
    assert "has not reported it yet" not in str(error.value)


def test_the_other_ceiling_keeps_its_range():
    number = make_number("chargeMaxLimit", {"chargeMaxLimit": 800})
    assert number.native_max_value == POWER_CEILING
    asyncio.run(number.async_set_native_value(2400))
    number.coordinator.async_write.assert_awaited_once_with("chargeMaxLimit", 2400)
