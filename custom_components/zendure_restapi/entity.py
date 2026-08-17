"""Shared entity base for the Zendure RestAPI integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ZendureCoordinator


class ZendureEntity(CoordinatorEntity[ZendureCoordinator]):
    """Base class binding an entity to one coordinator key."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ZendureCoordinator,
        description: EntityDescription,
        device_id: str,
        model: str,
        data_key: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._device_id = device_id
        self._data_key = data_key or description.key
        self._attr_unique_id = f"{device_id}_{description.key}"

        # Meters have no serial number, so serial_number is only set when the
        # device actually reported one.
        serial = coordinator.api.sn
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            manufacturer=MANUFACTURER,
            model=model,
            name=f"Zendure {model}",
            serial_number=serial,
            sw_version=coordinator.firmware,
            configuration_url=coordinator.api.base_url,
        )

    @property
    def raw_value(self) -> Any:
        """Unconverted value straight from the last poll."""
        return (self.coordinator.data or {}).get(self._data_key)

    @property
    def available(self) -> bool:
        """Available only while the key is actually being reported."""
        return super().available and self._data_key in (self.coordinator.data or {})
