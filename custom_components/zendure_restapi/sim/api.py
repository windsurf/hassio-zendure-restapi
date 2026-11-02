"""The local API of a simulated battery (v1.7.1).

Offers what ZendureLocalApi offers to the coordinator and the controller —
report, write one, write several — and answers from a SimDevice instead of
the network. Nothing leaves Home Assistant.
"""

from __future__ import annotations

from typing import Any

from .device import SimDevice


class SimApi:
    """ZendureLocalApi for a battery that exists only in the model."""

    def __init__(self, device: SimDevice) -> None:
        self.device = device
        self.sn: str | None = device.serial
        self.host = "simulated"
        self.port = 0

    @property
    def base_url(self) -> None:
        # No device to open in a browser.
        return None

    async def async_get_report(self) -> dict[str, Any]:
        return self.device.report()

    async def async_write_property(self, key: str, value: Any) -> dict[str, Any]:
        self.device.write({key: value})
        return {"code": 200, "success": True}

    async def async_write_properties(self, properties: dict[str, Any]) -> dict[str, Any]:
        self.device.write(dict(properties))
        return {"code": 200, "success": True}

    async def async_get_mqtt_status(self) -> dict[str, Any]:
        return {}

    async def async_get_mqtt_config(self) -> dict[str, Any]:
        return {}

    async def async_rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return {}
