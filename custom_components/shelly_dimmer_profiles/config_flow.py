"""Config flow: one entry per dimmer, one subentry per time slot."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentry,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TimeSelector,
)

from .const import (
    CONF_BRIGHTNESS,
    CONF_CHANGE_MODE,
    CONF_LIGHT,
    CONF_NAME,
    CONF_ON_TURN_ON,
    CONF_RAMP,
    CONF_RAMP_CURVE,
    CONF_RAMP_DURATION,
    CONF_RAMP_FROM,
    CONF_RAMP_REPEAT,
    CONF_RESUME_MINUTES,
    CONF_RESUME_MODE,
    CONF_SLOT_CHANGE,
    CONF_SOFT_DURATION,
    CONF_START,
    DEFAULT_RAMP_CURVE,
    DEFAULT_RAMP_DURATION,
    DEFAULT_RAMP_FROM,
    DEFAULT_RESUME_MINUTES,
    DEFAULT_SOFT_DURATION,
    DOMAIN,
    SUBENTRY_SLOT,
)
from .logic import (
    CHANGE_HARD,
    CHANGE_NONE,
    CHANGE_SOFT,
    CURVES,
    REPEAT_EVERY,
    REPEAT_ONCE,
    RESUME_CONTINUE,
    RESUME_HOLD,
    TURN_ON_RAMP,
    TURN_ON_SET,
)
from .shelly_api import resolve_target


def _select(options: list[str], key: str) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=options, translation_key=key, mode=SelectSelectorMode.DROPDOWN)
    )


def _number(
    low: float, high: float, step: float, unit: str, slider: bool = False
) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=low,
            max=high,
            step=step,
            unit_of_measurement=unit,
            mode=NumberSelectorMode.SLIDER if slider else NumberSelectorMode.BOX,
        )
    )


def _normalize_time(value: str) -> str:
    parts = value.split(":")
    while len(parts) < 3:
        parts.append("00")
    return ":".join(f"{int(p):02d}" for p in parts[:3])


def _slot_schema(data: dict[str, Any]) -> vol.Schema:
    ramp = data.get(CONF_RAMP) or {}
    change = data.get(CONF_SLOT_CHANGE) or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=data.get(CONF_NAME, "")): TextSelector(),
            vol.Required(CONF_START, default=data.get(CONF_START, "06:00:00")): TimeSelector(),
            vol.Required(CONF_BRIGHTNESS, default=data.get(CONF_BRIGHTNESS, 100)): _number(
                1, 100, 1, "%", slider=True
            ),
            vol.Required(CONF_ON_TURN_ON, default=data.get(CONF_ON_TURN_ON, TURN_ON_SET)): _select(
                [TURN_ON_SET, TURN_ON_RAMP], CONF_ON_TURN_ON
            ),
            vol.Required(CONF_RAMP): section(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_RAMP_FROM, default=ramp.get(CONF_RAMP_FROM, DEFAULT_RAMP_FROM)
                        ): _number(1, 100, 1, "%", slider=True),
                        vol.Required(
                            CONF_RAMP_DURATION,
                            default=ramp.get(CONF_RAMP_DURATION, DEFAULT_RAMP_DURATION),
                        ): _number(0.5, 120, 0.5, "min"),
                        vol.Required(
                            CONF_RAMP_CURVE, default=ramp.get(CONF_RAMP_CURVE, DEFAULT_RAMP_CURVE)
                        ): _select(list(CURVES), CONF_RAMP_CURVE),
                        vol.Required(
                            CONF_RAMP_REPEAT, default=ramp.get(CONF_RAMP_REPEAT, REPEAT_EVERY)
                        ): _select([REPEAT_EVERY, REPEAT_ONCE], CONF_RAMP_REPEAT),
                        vol.Required(
                            CONF_RESUME_MINUTES,
                            default=ramp.get(CONF_RESUME_MINUTES, DEFAULT_RESUME_MINUTES),
                        ): _number(0, 120, 0.5, "min"),
                        vol.Required(
                            CONF_RESUME_MODE,
                            default=ramp.get(CONF_RESUME_MODE, RESUME_CONTINUE),
                        ): _select([RESUME_CONTINUE, RESUME_HOLD], CONF_RESUME_MODE),
                    }
                ),
                {"collapsed": data.get(CONF_ON_TURN_ON, TURN_ON_SET) != TURN_ON_RAMP},
            ),
            vol.Required(CONF_SLOT_CHANGE): section(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_CHANGE_MODE, default=change.get(CONF_CHANGE_MODE, CHANGE_NONE)
                        ): _select([CHANGE_NONE, CHANGE_HARD, CHANGE_SOFT], CONF_CHANGE_MODE),
                        vol.Required(
                            CONF_SOFT_DURATION,
                            default=change.get(CONF_SOFT_DURATION, DEFAULT_SOFT_DURATION),
                        ): _number(1, 3600, 1, "s"),
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _clean(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    data[CONF_NAME] = str(data.get(CONF_NAME, "")).strip()
    data[CONF_START] = _normalize_time(data[CONF_START])
    data[CONF_BRIGHTNESS] = int(data[CONF_BRIGHTNESS])
    ramp = dict(data.get(CONF_RAMP) or {})
    if CONF_RAMP_FROM in ramp:
        ramp[CONF_RAMP_FROM] = int(ramp[CONF_RAMP_FROM])
    data[CONF_RAMP] = ramp
    data[CONF_SLOT_CHANGE] = dict(data.get(CONF_SLOT_CHANGE) or {})
    return data


def _title(data: dict[str, Any]) -> str:
    start = data[CONF_START][:5]
    return f"{start} {data[CONF_NAME]}".strip()


class ShellyDimmerProfilesConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create a profile for one Shelly dimmer."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the light."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_LIGHT]
            await self.async_set_unique_id(entity_id)
            self._abort_if_unique_id_configured()
            if resolve_target(self.hass, entity_id) is None:
                errors["base"] = "not_shelly"
            else:
                state = self.hass.states.get(entity_id)
                title = state.name if state else entity_id
                return self.async_create_entry(title=title, data={CONF_LIGHT: entity_id})

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LIGHT): EntitySelector(
                        EntitySelectorConfig(domain="light", integration="shelly")
                    )
                }
            ),
            errors=errors,
        )

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Time slots are subentries."""
        return {SUBENTRY_SLOT: TimeSlotSubentryFlow}


class TimeSlotSubentryFlow(ConfigSubentryFlow):
    """Add or edit a time slot."""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Add a time slot."""
        return await self._async_form("user", user_input, None)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a time slot."""
        return await self._async_form("reconfigure", user_input, self._get_reconfigure_subentry())

    async def _async_form(
        self,
        step_id: str,
        user_input: dict[str, Any] | None,
        subentry: ConfigSubentry | None,
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_entry()
        if user_input is not None:
            data = _clean(user_input)
            taken = {
                _normalize_time(sub.data[CONF_START])
                for sub in entry.subentries.values()
                if sub.subentry_type == SUBENTRY_SLOT
                and (subentry is None or sub.subentry_id != subentry.subentry_id)
            }
            if data[CONF_START] in taken:
                errors[CONF_START] = "duplicate_start"
            else:
                if subentry is not None:
                    return self.async_update_and_abort(
                        entry, subentry, title=_title(data), data=data
                    )
                return self.async_create_entry(title=_title(data), data=data)

        defaults = dict(subentry.data) if subentry is not None else {}
        if user_input is not None:
            defaults = user_input
        return self.async_show_form(
            step_id=step_id, data_schema=_slot_schema(defaults), errors=errors
        )
