"""Config flow tests."""

from __future__ import annotations

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.shelly_dimmer_profiles.const import DOMAIN

from .conftest import LIGHT, setup_profile, slot


async def test_user_flow(hass: HomeAssistant, fake_light, aioclient_mock) -> None:
    aioclient_mock.get("http://10.0.0.5:80/light/0", json={"ison": False})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"light_entity_id": LIGHT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"light_entity_id": LIGHT}


async def test_user_flow_not_shelly(hass: HomeAssistant) -> None:
    hass.states.async_set("light.other", "off")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"light_entity_id": "light.other"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "not_shelly"}


SLOT_INPUT = {
    "name": "Morning",
    "start": "06:00",
    "brightness": 100,
    "on_turn_on": "ramp",
    "ramp": {
        "from_pct": 1,
        "duration_min": 5,
        "curve": "quadratic",
        "repeat": "every",
        "resume_minutes": 5,
        "resume_mode": "continue",
    },
    "slot_change": {"mode": "none", "soft_duration_s": 10},
}


async def test_add_and_edit_time_slot(hass: HomeAssistant, fake_light, aioclient_mock) -> None:
    aioclient_mock.get("http://10.0.0.5:80/light/0", json={"ison": False})
    entry = await setup_profile(hass, [slot("Day", "07:00:00", 100)])

    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, "time_slot"), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM

    duplicate = dict(SLOT_INPUT, start="07:00:00")
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], duplicate)
    assert result["errors"] == {"start": "duplicate_start"}

    result = await hass.config_entries.subentries.async_configure(result["flow_id"], SLOT_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    entry = hass.config_entries.async_get_entry(entry.entry_id)
    morning = next(s for s in entry.subentries.values() if s.data["name"] == "Morning")
    assert morning.title == "06:00 Morning"
    assert morning.data["start"] == "06:00:00"
    assert morning.data["ramp"]["from_pct"] == 1
    # entry reloaded with the new slot
    assert len(entry.runtime_data.slots) == 2

    result = await entry.start_subentry_reconfigure_flow(hass, morning.subentry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], dict(SLOT_INPUT, brightness=90)
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    entry = hass.config_entries.async_get_entry(entry.entry_id)
    assert entry.subentries[morning.subentry_id].data["brightness"] == 90
