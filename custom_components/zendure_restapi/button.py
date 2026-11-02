"""Buttons: Rebuild dashboard on a cluster, Reset and Rebuild on a Virtual cluster (v1.7.1)."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CONF_DEVICE_TYPE, DEVICE_TYPE_CLUSTER, DEVICE_TYPE_VIRTUAL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    kind = entry.data.get(CONF_DEVICE_TYPE)
    if kind == DEVICE_TYPE_CLUSTER:
        from .cluster_entities import cluster_entities

        async_add_entities(cluster_entities(Platform.BUTTON, hass, entry))
    elif kind == DEVICE_TYPE_VIRTUAL:
        from .sim.entities import virtual_entities

        async_add_entities(virtual_entities(Platform.BUTTON, hass, entry))
