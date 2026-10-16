"""Shared entity base for the Zendure RestAPI integration."""

from __future__ import annotations

import re
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import ZendureCoordinator


def name_key(key: str) -> str:
    """Translation key for an entity name, derived from its description key.

    camelCase and leading acronyms become snake_case: `electricLevel` →
    `electric_level`, `IOTState` → `iot_state`, `hvBatVolt` → `hv_bat_volt`.
    """
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


class TranslatedName(Entity):
    """Entity names follow the language of Home Assistant; entity ids do not.

    The name comes from strings.json (entity.<platform>.<key>.name) and the
    translation files. Home Assistant builds a new entity id from the name in
    the system language for most Latin-script languages, so a Dutch or German
    installation would get Dutch or German entity ids — and the dashboard,
    the documentation and every example would stop matching. The English name
    from the entity description is therefore kept for the entity id, in every
    language. Existing entity ids are never touched: Home Assistant keeps
    them in its registry.
    """

    _object_id_name: str | None = None

    def _set_name(
        self,
        description: EntityDescription,
        prefix: str = "",
        placeholders: dict[str, str] | None = None,
        english: str | None = None,
    ) -> None:
        """Pick the translation key and remember the English name.

        A description that already carries a translation key (for translated
        states) keeps it; the name sits under the same key.
        """
        # A nameless entity takes the device name; a translation key would
        # give it a name of its own.
        if description.translation_key is None and description.name is not None:
            self._attr_translation_key = prefix + name_key(description.key)
        if placeholders:
            self._attr_translation_placeholders = placeholders
        name = english if english is not None else description.name
        self._object_id_name = name if isinstance(name, str) else None

    @property
    def suggested_object_id(self) -> str | None:
        """The English name, so entity ids are the same in every language."""
        if self._object_id_name:
            return self._object_id_name
        return super().suggested_object_id


class ZendureEntity(TranslatedName, CoordinatorEntity[ZendureCoordinator]):
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
        self._set_name(description)

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
