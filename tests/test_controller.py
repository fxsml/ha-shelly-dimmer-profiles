"""Tests for the controller against a fake Shelly light."""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from .conftest import TZ, setup_profile, slot

GEN1_URL = "http://10.0.0.5:80/light/0"
FAST_RAMP = {"from_pct": 1, "duration_min": 0.02, "curve": "quadratic", "resume_minutes": 5}


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 11, 17, hour, minute, second, tzinfo=TZ)


def stored(aioclient_mock: AiohttpClientMocker) -> list[int]:
    return [int(call[1].query["brightness"]) for call in aioclient_mock.mock_calls]


def profile() -> list:
    return [
        slot("Morning", "06:00:00", 100, "ramp", FAST_RAMP),
        slot("Day", "07:00:00", 100),
        slot("Night", "23:00:00", 1, change={"mode": "hard"}),
    ]


async def wait_ramp(hass: HomeAssistant, entry, timeout: float = 5) -> None:
    controller = entry.runtime_data
    for _ in range(int(timeout / 0.05)):
        await hass.async_block_till_done()
        if not controller.ramp_running:
            return
        await asyncio.sleep(0.05)
    raise AssertionError("ramp still running")


async def test_set_slot_and_store_when_off(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False, "brightness": 100})
    clock(at(12))
    await setup_profile(hass, profile())
    # HA already running -> brightness of the active slot stored at startup
    assert stored(aioclient_mock) == [100]

    fake_light.by_hand("on", 100)  # wall switch: turns on at the stored value
    await hass.async_block_till_done()
    assert fake_light.pcts == [100]

    fake_light.by_hand("off")
    await hass.async_block_till_done()
    assert stored(aioclient_mock) == [100, 100]


async def test_explicit_brightness_on_turn_on_is_respected(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    clock(at(6, 10))
    entry = await setup_profile(hass, profile())
    assert stored(aioclient_mock) == [1]

    fake_light.by_hand("on", 60)  # app/panel with a chosen brightness
    await hass.async_block_till_done()
    assert fake_light.calls == []
    assert entry.runtime_data.manual
    assert not entry.runtime_data.ramp_running

    fake_light.by_hand("off")
    await hass.async_block_till_done()
    assert not entry.runtime_data.manual


async def test_ramp_runs_to_end(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    clock(at(6, 10))
    entry = await setup_profile(hass, profile())
    assert stored(aioclient_mock) == [1]

    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    await wait_ramp(hass, entry)
    assert fake_light.pcts[0] == 1
    assert fake_light.pcts[1:] == list(range(2, 101))
    assert fake_light.calls[0]["transition"] == 0
    assert entry.runtime_data.ramp_state == "done"


async def test_short_pause_resumes_long_pause_restarts(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    ramp = dict(FAST_RAMP, duration_min=0.1)  # 6 s
    clock(at(6, 10))
    entry = await setup_profile(hass, [slot("Morning", "06:00:00", 100, "ramp", ramp)])
    controller = entry.runtime_data

    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    await asyncio.sleep(3)
    last = fake_light.pcts[-1]
    assert 1 < last < 100

    fake_light.by_hand("off")
    await hass.async_block_till_done()
    assert controller.ramp_state == "interrupted"
    assert stored(aioclient_mock)[-1] == last  # wall switch resumes at last value

    # back after 2 minutes: continue from the last value
    clock(at(6, 12))
    fake_light.calls.clear()
    fake_light.by_hand("on", last)
    await hass.async_block_till_done()
    await wait_ramp(hass, entry, timeout=8)
    assert fake_light.pcts[0] == last
    assert fake_light.pcts[1] == last + 1
    assert fake_light.pcts[-1] == 100

    assert controller.ramp_state == "done"
    fake_light.by_hand("off")
    await hass.async_block_till_done()

    # ramp done + repeat "every": next turn-on ramps again from the start
    fake_light.calls.clear()
    clock(at(6, 20))
    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    assert fake_light.pcts[0] == 1
    await asyncio.sleep(2)
    fake_light.by_hand("off")
    await hass.async_block_till_done()
    assert controller.ramp_state == "interrupted"

    # pause time over: the start value is stored again, the next turn-on restarts
    clock(at(6, 26))
    async_fire_time_changed(hass, at(6, 26))
    await hass.async_block_till_done()
    assert stored(aioclient_mock)[-1] == 1
    fake_light.calls.clear()
    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    assert fake_light.pcts[0] == 1
    controller._cancel_ramp()


async def test_pause_expiry_stores_start_value(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    ramp = dict(FAST_RAMP, duration_min=0.1)
    clock(at(6, 10))
    entry = await setup_profile(hass, [slot("Morning", "06:00:00", 100, "ramp", ramp)])

    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    await asyncio.sleep(2.5)
    fake_light.by_hand("off")
    await hass.async_block_till_done()
    last = stored(aioclient_mock)[-1]
    assert last > 1

    clock(at(6, 16))
    async_fire_time_changed(hass, at(6, 16))
    await hass.async_block_till_done()
    assert stored(aioclient_mock)[-1] == 1
    assert entry.runtime_data.ramp_state == "interrupted"


async def test_manual_change_stops_ramp(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    ramp = dict(FAST_RAMP, duration_min=0.1)
    clock(at(6, 10))
    entry = await setup_profile(hass, [slot("Morning", "06:00:00", 100, "ramp", ramp)])
    controller = entry.runtime_data

    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    await asyncio.sleep(1.5)
    fake_light.by_hand("on", 90)
    await hass.async_block_till_done()
    assert controller.manual
    count = len(fake_light.calls)
    await asyncio.sleep(1)
    await hass.async_block_till_done()
    assert len(fake_light.calls) == count
    assert controller.ramp_state == "manual"


async def test_once_per_slot(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    ramp = dict(FAST_RAMP, repeat="once", resume_minutes=0)
    clock(at(6, 10))
    entry = await setup_profile(hass, [slot("Morning", "06:00:00", 80, "ramp", ramp)])

    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    await wait_ramp(hass, entry)
    fake_light.by_hand("off")
    await hass.async_block_till_done()
    assert stored(aioclient_mock)[-1] == 80

    fake_light.calls.clear()
    clock(at(6, 40))
    fake_light.by_hand("on", 80)
    await hass.async_block_till_done()
    assert fake_light.pcts == [80]


async def test_slot_change_hard_when_on(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    clock(at(22, 59))
    await setup_profile(hass, profile())
    fake_light.by_hand("on", 100)
    await hass.async_block_till_done()
    fake_light.calls.clear()

    clock(at(23))
    async_fire_time_changed(hass, at(23))
    await hass.async_block_till_done()
    assert fake_light.pcts == [1]


async def test_slot_change_none_when_on(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    clock(at(22, 59))
    await setup_profile(hass, [slot("Day", "07:00:00", 100), slot("Night", "23:00:00", 1)])
    fake_light.by_hand("on", 100)
    await hass.async_block_till_done()
    fake_light.calls.clear()

    clock(at(23))
    async_fire_time_changed(hass, at(23))
    await hass.async_block_till_done()
    assert fake_light.calls == []

    # applies from the next turn-on
    fake_light.by_hand("off")
    await hass.async_block_till_done()
    assert stored(aioclient_mock)[-1] == 1
    fake_light.by_hand("on", 1)
    await hass.async_block_till_done()
    assert fake_light.pcts == [1]


async def test_slot_change_soft_when_on(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    clock(at(22, 59))
    entry = await setup_profile(
        hass,
        [
            slot("Day", "07:00:00", 100),
            slot("Night", "23:00:00", 10, change={"mode": "soft", "soft_duration_s": 1}),
        ],
    )
    fake_light.by_hand("on", 100)
    await hass.async_block_till_done()
    fake_light.calls.clear()

    clock(at(23))
    async_fire_time_changed(hass, at(23))
    await hass.async_block_till_done()
    await wait_ramp(hass, entry)
    assert fake_light.pcts == list(range(99, 9, -1))


async def test_slot_change_stores_when_off(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    clock(at(22, 59))
    await setup_profile(hass, profile())
    assert stored(aioclient_mock) == [100]
    clock(at(23))
    async_fire_time_changed(hass, at(23))
    await hass.async_block_till_done()
    assert stored(aioclient_mock) == [100, 1]


async def test_inactive_does_nothing(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, json={"ison": False})
    clock(at(12))
    await setup_profile(hass, profile())
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.bad_profile_active"}, blocking=True
    )
    fake_light.by_hand("on", 30)
    await hass.async_block_till_done()
    assert fake_light.calls == []
    state = hass.states.get("sensor.bad_active_time_slot")
    assert state.state == "Day"


async def test_gen3_uses_rpc_and_segments(hass, clock, aioclient_mock):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from .conftest import FakeLight

    shelly = MockConfigEntry(domain="shelly", data={"host": "10.0.0.6", "port": 80, "gen": 3})
    shelly.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light", "shelly", "DCB4D9C26168-light:0", config_entry=shelly, suggested_object_id="bad"
    )
    hass.states.async_set("light.bad", "off", {"brightness": None})
    light = FakeLight(hass)
    aioclient_mock.get("http://10.0.0.6:80/rpc/Light.Set", json={"was_on": False})

    clock(at(6, 10))
    entry = await setup_profile(
        hass, [slot("Morning", "06:00:00", 100, "ramp", dict(FAST_RAMP, duration_min=0.01))]
    )
    # storing on Gen2+ is disabled until verified on a real device
    assert aioclient_mock.mock_calls == []

    light.by_hand("on", 1)
    await hass.async_block_till_done()
    await wait_ramp(hass, entry)
    # one native segment for a short ramp: 1 -> 100 with transition
    assert light.pcts == [1, 100]
    assert light.calls[1]["transition"] > 0


async def test_gen3_store_when_verified(hass, clock, aioclient_mock):
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    shelly = MockConfigEntry(domain="shelly", data={"host": "10.0.0.6", "gen": 3})
    shelly.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light", "shelly", "DCB4D9C26168-light:0", config_entry=shelly, suggested_object_id="bad"
    )
    hass.states.async_set("light.bad", "off", {"brightness": None})
    aioclient_mock.get("http://10.0.0.6:80/rpc/Light.Set", json={"was_on": False})
    clock(at(12))
    with patch("custom_components.shelly_dimmer_profiles.shelly_api.RPC_STORE_VERIFIED", True):
        await setup_profile(hass, profile())
    query = aioclient_mock.mock_calls[0][1].query
    assert (query["id"], query["brightness"]) == ("0", "100")


async def test_store_failure_is_tolerated(hass, fake_light, clock, aioclient_mock):
    aioclient_mock.get(GEN1_URL, exc=TimeoutError)
    clock(at(12))
    entry = await setup_profile(hass, profile())
    assert entry.runtime_data.last_stored_pct is None
    fake_light.by_hand("on", 37)  # stale brightness on the device
    await hass.async_block_till_done()
    assert fake_light.pcts == [100]
