"""Config flow tests."""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType, InvalidData
from homeassistant.helpers import entity_registry as er

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


async def test_user_flow_without_supported_lights(hass: HomeAssistant) -> None:
    hass.states.async_set("light.other", "off", {"supported_color_modes": ["brightness"]})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_supported_lights"


async def test_only_supported_lights_offered(hass: HomeAssistant, fake_light) -> None:
    registry = er.async_get(hass)
    shelly = hass.config_entries.async_entries("shelly")[0]

    def add(unique_id: str, object_id: str, modes: list[str]) -> str:
        return registry.async_get_or_create(
            "light",
            "shelly",
            unique_id,
            config_entry=shelly,
            suggested_object_id=object_id,
            capabilities={"supported_color_modes": modes},
        ).entity_id

    relay = add("AA-relay_0", "plug", ["onoff"])
    relay_dimmable = add("AB-switch:0", "mini", ["brightness"])
    onoff_channel = add("AC-light_1", "onoff", ["onoff"])
    color = add("AD-light_0", "rgbw", ["rgbw"])
    gen3 = add("AE-light:0", "gen3", ["brightness"])
    other = registry.async_get_or_create(
        "light",
        "hue",
        "x",
        suggested_object_id="hue",
        capabilities={"supported_color_modes": ["brightness"]},
    ).entity_id

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    selector = result["data_schema"].schema["light_entity_id"]
    offered = selector.config["include_entities"]
    assert offered == sorted([LIGHT, gen3])
    for entity_id in (relay, relay_dimmable, onoff_channel, color, other):
        assert entity_id not in offered

    # submitted anyway (e.g. via API): the selector already rejects it
    with pytest.raises(InvalidData):
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {"light_entity_id": color}
        )


async def test_configured_light_not_offered_again(
    hass: HomeAssistant, fake_light, aioclient_mock
) -> None:
    aioclient_mock.get("http://10.0.0.5:80/light/0", json={"ison": False})
    await setup_profile(hass, [slot("Day", "07:00:00", 100)])
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_supported_lights"


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
