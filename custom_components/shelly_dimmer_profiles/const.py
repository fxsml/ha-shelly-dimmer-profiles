"""Constants for Shelly Dimmer Profiles."""

from __future__ import annotations

DOMAIN = "shelly_dimmer_profiles"

CONF_LIGHT = "light_entity_id"
SUBENTRY_SLOT = "time_slot"

# Time slot fields
CONF_NAME = "name"
CONF_START = "start"
CONF_BRIGHTNESS = "brightness"
CONF_ON_TURN_ON = "on_turn_on"
CONF_RAMP = "ramp"
CONF_RAMP_FROM = "from_pct"
CONF_RAMP_DURATION = "duration_min"
CONF_RAMP_CURVE = "curve"
CONF_RAMP_REPEAT = "repeat"
CONF_RESUME_MINUTES = "resume_minutes"
CONF_RESUME_MODE = "resume_mode"
CONF_SLOT_CHANGE = "slot_change"
CONF_CHANGE_MODE = "mode"
CONF_SOFT_DURATION = "soft_duration_s"

DEFAULT_RAMP_FROM = 1
DEFAULT_RAMP_DURATION = 5.0
DEFAULT_RAMP_CURVE = "quadratic"
DEFAULT_RESUME_MINUTES = 5.0
DEFAULT_SOFT_DURATION = 10.0

STORAGE_VERSION = 1
