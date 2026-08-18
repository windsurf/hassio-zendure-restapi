"""Persistent settings for the operation-mode controller.

Values live in the config entry options rather than in entity state, so they
survive a restart without every number and select entity needing its own
restore logic. Entities read and write through this class.
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DEFAULTS, OPERATION_MODES, OPT_OPERATION_MODE

_LOGGER = logging.getLogger(__name__)


class ZendureSettings:
    """Typed read/write access to the controller settings."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self._hass = hass
        self._entry = entry

    def get(self, key: str) -> Any:
        """Return a setting, falling back to its default.

        A stored operation mode that no longer exists falls back to the
        default rather than leaving the select on a value it cannot offer.
        This is what an entry saved under an older version looks like after a
        mode is withdrawn.
        """
        value = self._entry.options.get(key, DEFAULTS[key])
        if key == OPT_OPERATION_MODE and value not in OPERATION_MODES:
            _LOGGER.info(
                "Stored operation mode '%s' no longer exists, falling back to '%s'",
                value, DEFAULTS[key],
            )
            return DEFAULTS[key]
        return value

    def get_int(self, key: str) -> int:
        try:
            return int(self.get(key))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def set(self, key: str, value: Any) -> None:
        """Persist a setting.

        Writing options triggers the entry's update listener, which applies the
        polling interval and requests a refresh. That is intentional: a changed
        setting should take effect on the next cycle, not several cycles later.
        """
        if self.get(key) == value:
            return
        options = {**self._entry.options, key: value}
        self._hass.config_entries.async_update_entry(self._entry, options=options)
        _LOGGER.debug("Setting %s = %s", key, value)
