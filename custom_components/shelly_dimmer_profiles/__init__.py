"""Shelly Dimmer Profiles: time-based brightness, ramps and pre-set brightness."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .controller import ProfileController

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.SWITCH]

type ProfileConfigEntry = ConfigEntry[ProfileController]


async def async_setup_entry(hass: HomeAssistant, entry: ProfileConfigEntry) -> bool:
    """Set up a dimmer profile."""
    controller = ProfileController(hass, entry)
    await controller.async_setup()
    entry.runtime_data = controller
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: ProfileConfigEntry) -> None:
    """Reload when time slots change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ProfileConfigEntry) -> bool:
    """Unload a dimmer profile."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
