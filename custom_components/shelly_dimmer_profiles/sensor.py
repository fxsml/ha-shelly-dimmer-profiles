"""Sensor showing the active time slot."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import ProfileConfigEntry
from .entity import ProfileEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ProfileConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the sensor."""
    async_add_entities([ActiveSlotSensor(entry.runtime_data, "active_slot")])


class ActiveSlotSensor(ProfileEntity, SensorEntity):
    """Name of the active time slot, details as attributes."""

    @property
    def native_value(self) -> str | None:
        """Return the active slot name."""
        slot = self.controller.current_slot
        return slot.name if slot else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return details."""
        controller = self.controller
        slot = controller.current_slot
        next_change = controller.next_change
        return {
            "brightness": slot.brightness if slot else None,
            "on_turn_on": slot.on_turn_on if slot else None,
            "ramp_state": controller.ramp_state,
            "ramp_running": controller.ramp_running,
            "manual_override": controller.manual,
            "stored_brightness": controller.last_stored_pct,
            "next_change": next_change.isoformat() if next_change else None,
        }
