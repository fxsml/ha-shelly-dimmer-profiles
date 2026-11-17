"""Fixtures."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.core import Context, HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.shelly_dimmer_profiles.const import CONF_LIGHT, DOMAIN

LIGHT = "light.bad"
TZ = ZoneInfo("Europe/Berlin")


@pytest.fixture(autouse=True)
async def auto_enable_custom_integrations(hass, enable_custom_integrations):
    """Enable custom integrations, use the time zone of the slots in the tests."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    yield


class FakeLight:
    """Minimal light: records calls, writes states with the call context."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.calls: list[dict] = []
        hass.services.async_register("light", "turn_on", self._turn_on)
        hass.services.async_register("light", "turn_off", self._turn_off)

    async def _turn_on(self, call: ServiceCall) -> None:
        self.calls.append(dict(call.data))
        pct = call.data.get("brightness_pct", 100)
        self.hass.states.async_set(
            LIGHT, "on", {"brightness": round(pct * 255 / 100)}, context=call.context
        )

    async def _turn_off(self, call: ServiceCall) -> None:
        self.hass.states.async_set(LIGHT, "off", {"brightness": None}, context=call.context)

    def by_hand(self, state: str, pct: int | None = None) -> None:
        """Simulate the wall switch / app."""
        attrs = {"brightness": round(pct * 255 / 100) if pct else None}
        self.hass.states.async_set(LIGHT, state, attrs, context=Context())

    @property
    def pcts(self) -> list[int]:
        return [c["brightness_pct"] for c in self.calls]


@pytest.fixture
def fake_light(hass: HomeAssistant) -> FakeLight:
    """Fake light registered as a Shelly Gen1 entity."""
    shelly = MockConfigEntry(domain="shelly", data={"host": "10.0.0.5", "gen": 1})
    shelly.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light",
        "shelly",
        "AABBCCDDEEFF-light_0",
        config_entry=shelly,
        suggested_object_id="bad",
        capabilities={"supported_color_modes": ["brightness"]},
    )
    hass.states.async_set(LIGHT, "off", {"brightness": None})
    return FakeLight(hass)


def slot(
    name: str,
    start: str,
    brightness: int,
    on_turn_on: str = "set",
    ramp: dict | None = None,
    change: dict | None = None,
) -> ConfigSubentryData:
    """Time slot subentry data."""
    return ConfigSubentryData(
        data={
            "name": name,
            "start": start,
            "brightness": brightness,
            "on_turn_on": on_turn_on,
            "ramp": ramp or {},
            "slot_change": change or {},
        },
        subentry_type="time_slot",
        title=f"{start[:5]} {name}",
        unique_id=None,
    )


@pytest.fixture
def clock() -> Callable[[datetime], None]:
    """Patch the controller clock; returns a setter."""
    current = {"now": datetime(2026, 11, 17, 12, 0, tzinfo=TZ)}
    patcher = patch(
        "custom_components.shelly_dimmer_profiles.controller.ProfileController.now",
        lambda self: current["now"],
    )
    patcher.start()

    def set_now(value: datetime) -> None:
        current["now"] = value

    yield set_now
    patcher.stop()


async def setup_profile(
    hass: HomeAssistant, subentries: list[ConfigSubentryData]
) -> MockConfigEntry:
    """Create and set up a profile entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_LIGHT: LIGHT},
        title="Bad",
        unique_id=LIGHT,
        subentries_data=subentries,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
