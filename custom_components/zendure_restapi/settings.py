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

from .const import (
    COORDINATED_KEYS,
    DEFAULTS,
    MODE_FOLLOW,
    MODE_STANDBY,
    OPT_MAX_CHARGE_POWER,
    OPT_MAX_DISCHARGE_POWER,
    METER_SOURCES,
    OPERATION_MODES,
    OPT_METER_SOURCE,
    OPT_OPERATION_MODE,
    OPT_PRICE_CONTAINS,
    OPT_PRICE_DECISION,
    PRICE_CONTAINS_OPTIONS,
    PRICE_DECISIONS,
    PRICE_MODE_OPTIONS,
)

# Settings with a fixed set of values: a stored value outside it falls back
# to the default.
CHOICES = {
    OPT_METER_SOURCE: METER_SOURCES,
    OPT_PRICE_DECISION: PRICE_DECISIONS,
    OPT_PRICE_CONTAINS: PRICE_CONTAINS_OPTIONS,
    **PRICE_MODE_OPTIONS,
}

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
        if key in CHOICES and value not in CHOICES[key]:
            return DEFAULTS[key]
        return value

    def get_float(self, key: str) -> float:
        try:
            return float(self.get(key))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

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

    def set_many(self, values: dict[str, Any]) -> None:
        """Persist several settings in one write: one update, not one per key."""
        changed = {k: v for k, v in values.items() if self.get(k) != v}
        if not changed:
            return
        options = {**self._entry.options, **changed}
        self._hass.config_entries.async_update_entry(self._entry, options=options)
        _LOGGER.debug("Settings %s", changed)


# Roles a battery can be given by the battery cluster (v1.7.0); the names
# match cluster/contract.py, which must not be imported from here.
ROLE_PRIMARY = "primary"
ROLE_SECONDARY = "secondary"
ROLE_QUICK = "quick"
ROLE_STANDBY = "standby"


class SettingsView:
    """What the controller reads: its own settings, or the group's (v1.7.0).

    Outside a battery cluster this is the battery's own settings and
    nothing else, so a single battery behaves exactly as in v1.6.5. While a
    battery takes part, the cluster sets `group` and `role`:

    * the Operation mode follows the role — the group's mode for the primary
      and in the quick modes, `follow` for a secondary, `standby` otherwise;
    * buffers, start thresholds and the meter come from the cluster, so
      the primary steers on the group's settings;
    * Max charge/discharge power are capped at the battery's Max share.

    Writes always go to the battery's own settings: the cluster's are set
    on its own entities.
    """

    def __init__(self, own: ZendureSettings) -> None:
        self.own = own
        self.group: ZendureSettings | None = None
        self.group_name: str | None = None
        self.role: str | None = None
        self.max_share = 0

    @property
    def coordinated(self) -> bool:
        return self.group is not None and self.role is not None

    def get(self, key: str) -> Any:
        if self.coordinated:
            if key == OPT_OPERATION_MODE:
                if self.role in (ROLE_PRIMARY, ROLE_QUICK):
                    return self.group.get(OPT_OPERATION_MODE)
                if self.role == ROLE_SECONDARY:
                    return MODE_FOLLOW
                return MODE_STANDBY
            if key in COORDINATED_KEYS:
                return self.group.get(key)
            if key in (OPT_MAX_CHARGE_POWER, OPT_MAX_DISCHARGE_POWER) and self.max_share > 0:
                return min(self.own.get_int(key), self.max_share)
        return self.own.get(key)

    def get_float(self, key: str) -> float:
        try:
            return float(self.get(key))
        except (TypeError, ValueError):
            return float(DEFAULTS[key])

    def get_int(self, key: str) -> int:
        try:
            return int(self.get(key))
        except (TypeError, ValueError):
            return int(DEFAULTS[key])

    def get_bool(self, key: str) -> bool:
        return bool(self.get(key))

    def set(self, key: str, value: Any) -> None:
        self.own.set(key, value)

    def set_many(self, values: dict[str, Any]) -> None:
        self.own.set_many(values)
