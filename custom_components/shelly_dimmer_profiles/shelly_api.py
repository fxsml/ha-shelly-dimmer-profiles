"""Direct access to Shelly dimmers: store a brightness without turning on.

Home Assistant's light.turn_on always switches the light on, so the brightness
the wall switch will use next is written to the device directly:

* Gen1 (e.g. Dimmer 2):  GET /light/<ch>?brightness=X        (no "turn")
* Gen2+ (e.g. Dimmer Gen3): GET /rpc/Light.Set?id=<ch>&brightness=X  (no "on")

Host, port and credentials come from the existing Shelly config entry.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

import aiohttp
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

_LOGGER = logging.getLogger(__name__)

SHELLY_DOMAIN = "shelly"
REQUEST_TIMEOUT = 5

# Longest transition Home Assistant passes to a Gen1 light (device limit).
GEN1_MAX_TRANSITION = 5.0
# Gen2+ dimmers fade natively for much longer; ramps use segments of this length.
RPC_SEGMENT = 15.0
RPC_MAX_TRANSITION = 60.0
# Storing a brightness on Gen2+ without turning on: verified 2026-11-17 on a Dimmer Gen3
# (S3DM-0A101WWL, fw 2.2.0): Light.Set with only `brightness` keeps output false and the
# device reports the new brightness (response body: null).
RPC_STORE_VERIFIED = True

_CHANNEL_RE = re.compile(r"light[_:-](\d+)$")


@dataclass(frozen=True)
class ShellyTarget:
    """Where and how to reach the dimmer behind a light entity."""

    host: str
    port: int
    gen: int
    channel: int
    username: str | None = None
    password: str | None = None

    @property
    def is_gen1(self) -> bool:
        """Gen1 (block/CoIoT) device."""
        return self.gen < 2

    @property
    def integer_steps(self) -> bool:
        """Ramp in whole-percent steps (short native transitions only)."""
        return self.is_gen1

    @property
    def max_transition(self) -> float:
        """Longest transition to request per step."""
        return GEN1_MAX_TRANSITION if self.is_gen1 else RPC_MAX_TRANSITION

    @property
    def can_store(self) -> bool:
        """Whether a brightness can be stored without turning the light on."""
        return self.is_gen1 or RPC_STORE_VERIFIED

    @property
    def segment(self) -> float:
        """Segment length for natively faded ramps."""
        return RPC_SEGMENT


# Color modes whose brightness the dimmer endpoints (/light, Light.Set) control.
# Colour modes (rgb, rgbw, hs, ...) use other endpoints (e.g. /color on RGBW2) and
# on/off-only lights are relays (Plug, 1PM, ...), so both are not supported.
SUPPORTED_COLOR_MODES = {"brightness", "color_temp"}


@callback
def is_supported_light(hass: HomeAssistant, entity: er.RegistryEntry) -> bool:
    """Whether a light entity is a dimmable Shelly light channel this integration handles."""
    if entity.domain != "light" or entity.platform != SHELLY_DOMAIN or entity.disabled:
        return False
    if not _CHANNEL_RE.search(entity.unique_id or ""):
        return False  # relay / switch channel exposed as light
    modes = (entity.capabilities or {}).get("supported_color_modes")
    if modes is None and (state := hass.states.get(entity.entity_id)) is not None:
        modes = state.attributes.get("supported_color_modes")
    return bool(modes) and {str(mode) for mode in modes} <= SUPPORTED_COLOR_MODES


@callback
def supported_lights(hass: HomeAssistant) -> list[str]:
    """All supported Shelly dimmer lights."""
    registry = er.async_get(hass)
    return sorted(
        entity.entity_id
        for entity in registry.entities.values()
        if is_supported_light(hass, entity)
    )


@callback
def resolve_target(hass: HomeAssistant, entity_id: str) -> ShellyTarget | None:
    """Find the Shelly dimmer behind a light entity, or None if it is not supported."""
    entity = er.async_get(hass).async_get(entity_id)
    if entity is None or not is_supported_light(hass, entity) or not entity.config_entry_id:
        return None
    entry = hass.config_entries.async_get_entry(entity.config_entry_id)
    if entry is None or entry.domain != SHELLY_DOMAIN:
        return None
    host = entry.data.get(CONF_HOST)
    match = _CHANNEL_RE.search(entity.unique_id or "")
    if not host or match is None:
        return None
    return ShellyTarget(
        host=host,
        port=int(entry.data.get(CONF_PORT) or 80),
        gen=int(entry.data.get("gen") or 1),
        channel=int(match.group(1)),
        username=entry.data.get(CONF_USERNAME),
        password=entry.data.get(CONF_PASSWORD),
    )


async def async_store_brightness(hass: HomeAssistant, target: ShellyTarget, pct: int) -> bool:
    """Store `pct` as the brightness the light turns on with, keeping it off."""
    pct = max(1, min(100, int(pct)))
    base = f"http://{target.host}:{target.port}"
    kwargs: dict = {}
    if target.is_gen1:
        url = f"{base}/light/{target.channel}"
        params = {"brightness": pct}
        if target.username and target.password:
            kwargs["auth"] = aiohttp.BasicAuth(target.username, target.password)
    else:
        url = f"{base}/rpc/Light.Set"
        params = {"id": target.channel, "brightness": pct}
        if target.password:
            middleware = getattr(aiohttp, "DigestAuthMiddleware", None)
            if middleware is None:
                _LOGGER.error("Digest auth needs aiohttp >= 3.12 for %s", target.host)
                return False
            kwargs["middlewares"] = (middleware("admin", target.password),)

    session = async_get_clientsession(hass)
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            async with session.get(url, params=params, **kwargs) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.warning("Could not store brightness on %s: %s", target.host, err)
        return False

    if target.is_gen1 and isinstance(data, dict) and data.get("ison"):
        _LOGGER.warning("%s is on after storing brightness", target.host)
    _LOGGER.debug("Stored %s%% on %s", pct, target.host)
    return True
