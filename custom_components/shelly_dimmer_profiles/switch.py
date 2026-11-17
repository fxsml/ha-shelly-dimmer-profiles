"""Switch to enable or pause a profile."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProfileConfigEntry
from .entity import ProfileEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch."""
    async_add_entities([ProfileActiveSwitch(entry.runtime_data, "active")])


class ProfileActiveSwitch(ProfileEntity, SwitchEntity):
    """Profile active (off = the light behaves as without this integration)."""

    @property
    def is_on(self) -> bool:
        """Return whether the profile is active."""
        return self.controller.active

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate."""
        await self.controller.async_set_active(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Pause."""
        await self.controller.async_set_active(False)
