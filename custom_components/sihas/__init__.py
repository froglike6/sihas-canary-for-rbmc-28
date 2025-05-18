"""The sihas integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = [
    "button",
    "climate",
    "cover",
    "light",
    "sensor",
    "switch",
    "select",
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    # NOTE: how about checking supported type at here?
    _LOGGER.info(f"entry setuped: {entry.data}")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry):
    _LOGGER.info(f"entry unloadded: {entry.data}")
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return unload_ok
