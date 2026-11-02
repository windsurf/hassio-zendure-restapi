"""Device registry calls across Home Assistant versions (v1.7.1).

Home Assistant 2026.8 deprecated `async_get_device(identifiers=...)` and
`DeviceInfo["via_device"]`; they stop working in 2027.8. Identifiers are unique
per config entry only, so the lookup now takes the entry. The integration
supports 2025.3 and newer, so it uses the new call where it exists and the old
one before that, and links a device under another one with
`async_update_device(via_device_id=...)`, which every supported version has.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr


def device_by_identifier(
    devices: dr.DeviceRegistry, identifier: tuple[str, str], entry_id: str
) -> dr.DeviceEntry | None:
    """The device with this identifier in this config entry."""
    find: Any = getattr(devices, "async_get_device_by_identifier", None)
    if find is not None:
        return find(identifier, entry_id)
    return devices.async_get_device(identifiers={identifier})


def link_under(
    devices: dr.DeviceRegistry, child: tuple[str, str], parent: tuple[str, str], entry_id: str
) -> bool:
    """Show the child device under the parent; False while either does not exist yet."""
    below = device_by_identifier(devices, child, entry_id)
    above = device_by_identifier(devices, parent, entry_id)
    if below is None or above is None:
        return False
    if below.via_device_id != above.id:
        devices.async_update_device(below.id, via_device_id=above.id)
    return True
