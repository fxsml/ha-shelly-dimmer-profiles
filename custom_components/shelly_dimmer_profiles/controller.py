"""Runtime controller: one per configured dimmer."""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Callable
from datetime import datetime, time, timedelta
from functools import partial
from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_BRIGHTNESS_PCT,
    ATTR_TRANSITION,
)
from homeassistant.components.light import (
    DOMAIN as LIGHT_DOMAIN,
)
from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_ON, STATE_OFF, STATE_ON
from homeassistant.core import (
    CALLBACK_TYPE,
    Context,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import (
    async_track_point_in_time,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

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
    STORAGE_VERSION,
    SUBENTRY_SLOT,
)
from .logic import (
    CHANGE_HARD,
    CHANGE_NONE,
    CHANGE_SOFT,
    DECISION_HOLD,
    DECISION_RAMP,
    RAMP_DONE,
    RAMP_HELD,
    RAMP_IDLE,
    RAMP_INTERRUPTED,
    RAMP_MANUAL,
    RAMP_RUNNING,
    REPEAT_EVERY,
    RESUME_CONTINUE,
    TURN_ON_SET,
    Plan,
    Ramp,
    RampMemory,
    Slot,
    active_slot,
    build_plan,
    decide_turn_on,
    in_band,
    next_slot,
    occurrence_key,
)
from .shelly_api import ShellyTarget, async_store_brightness, resolve_target

_LOGGER = logging.getLogger(__name__)


def parse_time(value: str) -> time:
    """Parse HH:MM[:SS]."""
    parts = [int(p) for p in value.split(":")]
    while len(parts) < 3:
        parts.append(0)
    return time(parts[0], parts[1], parts[2])


def slot_from_subentry(subentry: ConfigSubentry) -> Slot:
    """Build a Slot from a time slot subentry."""
    data = subentry.data
    ramp_data = data.get(CONF_RAMP) or {}
    change = data.get(CONF_SLOT_CHANGE) or {}
    ramp = Ramp(
        from_pct=int(ramp_data.get(CONF_RAMP_FROM, DEFAULT_RAMP_FROM)),
        duration_s=float(ramp_data.get(CONF_RAMP_DURATION, DEFAULT_RAMP_DURATION)) * 60,
        curve=ramp_data.get(CONF_RAMP_CURVE, DEFAULT_RAMP_CURVE),
        repeat=ramp_data.get(CONF_RAMP_REPEAT, REPEAT_EVERY),
        resume_minutes=float(ramp_data.get(CONF_RESUME_MINUTES, DEFAULT_RESUME_MINUTES)),
        resume_mode=ramp_data.get(CONF_RESUME_MODE, RESUME_CONTINUE),
    )
    return Slot(
        slot_id=subentry.subentry_id,
        name=data.get(CONF_NAME) or subentry.title,
        start=parse_time(data[CONF_START]),
        brightness=int(data[CONF_BRIGHTNESS]),
        on_turn_on=data.get(CONF_ON_TURN_ON, TURN_ON_SET),
        ramp=ramp,
        on_change=change.get(CONF_CHANGE_MODE, CHANGE_NONE),
        soft_duration_s=float(change.get(CONF_SOFT_DURATION, DEFAULT_SOFT_DURATION)),
    )


def _pct(state: State | None) -> int | None:
    if state is None:
        return None
    brightness = state.attributes.get(ATTR_BRIGHTNESS)
    if brightness is None:
        return None
    return max(1, round(brightness * 100 / 255))


class ProfileController:
    """Applies the time slots of one dimmer."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.light: str = entry.data[CONF_LIGHT]
        self.slots: list[Slot] = [
            slot_from_subentry(sub)
            for sub in entry.subentries.values()
            if sub.subentry_type == SUBENTRY_SLOT
        ]
        self.target: ShellyTarget | None = None
        self.active = True
        self.memory = RampMemory()
        self.manual = False
        self.last_stored_pct: int | None = None
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._lock = asyncio.Lock()
        self._own_contexts: deque[str] = deque(maxlen=64)
        self._band: tuple[int, int] | None = None
        self._ramp_task: asyncio.Task | None = None
        self._recheck_unsub: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ setup

    async def async_setup(self) -> None:
        """Load state and start listening."""
        stored = await self._store.async_load() or {}
        self.active = stored.get("active", True)
        self.memory = RampMemory.from_dict(stored.get("memory"))
        self.target = resolve_target(self.hass, self.light)
        if self.target is None:
            _LOGGER.warning("%s is not a Shelly light; storing brightness disabled", self.light)

        self._unsubs.append(
            async_track_state_change_event(self.hass, [self.light], self._async_light_event)
        )
        for slot in self.slots:
            self._unsubs.append(
                async_track_time_change(
                    self.hass,
                    partial(self._async_slot_time, slot),
                    hour=slot.start.hour,
                    minute=slot.start.minute,
                    second=slot.start.second,
                )
            )
        self._unsubs.append(async_at_started(self.hass, self._async_started))

    async def async_shutdown(self) -> None:
        """Stop everything."""
        self._cancel_ramp()
        self._cancel_recheck()
        while self._unsubs:
            self._unsubs.pop()()

    async def _async_started(self, _hass: HomeAssistant) -> None:
        async with self._lock:
            await self._async_store_on_device()
        self._notify()

    # ---------------------------------------------------------------- helpers

    def now(self) -> datetime:
        """Current local time (patchable in tests)."""
        return dt_util.now()

    @property
    def current_slot(self) -> Slot | None:
        """The slot active right now."""
        if not self.slots:
            return None
        return active_slot(self.slots, self.now())[0]

    @property
    def next_change(self) -> datetime | None:
        """When the next slot starts."""
        if not self.slots:
            return None
        return next_slot(self.slots, self.now())[1]

    @property
    def ramp_running(self) -> bool:
        """Whether a ramp or soft transition is running."""
        return self._ramp_task is not None and not self._ramp_task.done()

    def _current(self) -> tuple[Slot, str]:
        slot, start = active_slot(self.slots, self.now())
        return slot, occurrence_key(slot, start)

    def _state(self) -> State | None:
        return self.hass.states.get(self.light)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Register an entity update listener."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    async def _async_save(self) -> None:
        await self._store.async_save({"active": self.active, "memory": self.memory.as_dict()})

    async def _async_turn_on(self, pct: int, transition: float | None) -> None:
        context = Context()
        self._own_contexts.append(context.id)
        data: dict[str, Any] = {ATTR_ENTITY_ID: self.light, ATTR_BRIGHTNESS_PCT: pct}
        if transition is not None:
            data[ATTR_TRANSITION] = transition
        await self.hass.services.async_call(
            LIGHT_DOMAIN, SERVICE_TURN_ON, data, blocking=True, context=context
        )

    async def _async_apply(self, pct: int) -> None:
        self._band = (pct, pct)
        await self._async_turn_on(pct, 0)

    def _start_plan(self, plan: Plan, start_pct: int, *, track: bool) -> None:
        self._cancel_ramp()
        self._ramp_task = self.entry.async_create_background_task(
            self.hass,
            self._async_run_plan(plan, start_pct, track),
            f"{DOMAIN} ramp {self.light}",
        )

    @callback
    def _cancel_ramp(self) -> None:
        if self._ramp_task is not None and not self._ramp_task.done():
            self._ramp_task.cancel()
        self._ramp_task = None

    @callback
    def _cancel_recheck(self) -> None:
        if self._recheck_unsub is not None:
            self._recheck_unsub()
            self._recheck_unsub = None

    # ------------------------------------------------------------------- ramp

    async def _async_run_plan(self, plan: Plan, start_pct: int, track: bool) -> None:
        previous = start_pct
        try:
            for step in plan.steps:
                if step.delay > 0:
                    await asyncio.sleep(step.delay)
                state = self._state()
                if state is None or state.state != STATE_ON or self.manual:
                    return
                self._band = (previous, step.pct)
                await self._async_turn_on(step.pct, step.transition)
                previous = step.pct
                if track:
                    self.memory.last_pct = step.pct
            if plan.tail > 0:
                await asyncio.sleep(plan.tail)
            self._band = (previous, previous)
            if track and self.memory.state == RAMP_RUNNING:
                self.memory.state = RAMP_DONE
                await self._async_save()
        finally:
            if self._ramp_task is asyncio.current_task():
                self._ramp_task = None
            self._notify()

    # ----------------------------------------------------------------- events

    @callback
    def _async_light_event(self, event: Event[EventStateChangedData]) -> None:
        old = event.data["old_state"]
        new = event.data["new_state"]
        if new is None or old is None:
            return
        own = event.context.id in self._own_contexts

        if old.state == STATE_ON and new.state == STATE_OFF:
            self.hass.async_create_task(self._async_turned_off(old))
        elif old.state == STATE_OFF and new.state == STATE_ON:
            if not own:
                self.hass.async_create_task(self._async_turned_on(new))
        elif old.state == STATE_ON and new.state == STATE_ON and not own:
            pct = _pct(new)
            if (
                self.active
                and self._band is not None
                and pct is not None
                and not in_band(pct, self._band)
            ):
                self._manual_override(pct)

    @callback
    def _manual_override(self, pct: int) -> None:
        _LOGGER.debug("%s changed by hand to %s%%, pausing until off", self.light, pct)
        self._cancel_ramp()
        self.manual = True
        self._band = None
        if self.memory.state in (RAMP_RUNNING, RAMP_HELD):
            self.memory.state = RAMP_MANUAL
            self.hass.async_create_task(self._async_save())
        self._notify()

    async def _async_turned_on(self, new: State) -> None:
        async with self._lock:
            self._cancel_recheck()
            self.manual = False
            if not self.active or not self.slots:
                self._band = None
                return
            pct = _pct(new)
            stored = self.last_stored_pct
            if stored is not None and pct is not None and not in_band(pct, (stored, stored)):
                # Turned on with an explicit brightness (app, panel): respect it.
                _LOGGER.debug("%s turned on at %s%% (stored %s%%): manual", self.light, pct, stored)
                self.manual = True
                self._band = None
                self._notify()
                return
            slot, occurrence = self._current()
            decision = decide_turn_on(slot, occurrence, self.memory, self.now())

            if decision.kind == DECISION_RAMP and slot.ramp is not None:
                ramp = slot.ramp
                self.memory = RampMemory(occurrence, RAMP_RUNNING, decision.pct)
                await self._async_save()
                await self._async_apply(decision.pct)
                plan = self._plan(
                    ramp.from_pct,
                    slot.brightness,
                    ramp.duration_s,
                    ramp.exponent,
                    start_pct=decision.pct,
                )
                self._start_plan(plan, decision.pct, track=True)
            elif decision.kind == DECISION_HOLD:
                self.memory.state = RAMP_HELD
                await self._async_save()
                await self._async_apply(decision.pct)
            else:
                await self._async_apply(decision.pct)
        self._notify()

    async def _async_turned_off(self, old: State) -> None:
        async with self._lock:
            self._cancel_ramp()
            if self.memory.state in (RAMP_RUNNING, RAMP_HELD):
                self.memory.state = RAMP_INTERRUPTED
                self.memory.last_pct = _pct(old) or self.memory.last_pct
                self.memory.off_at = self.now()
            elif self.memory.state == RAMP_MANUAL:
                self.memory.state = RAMP_IDLE
            self.manual = False
            self._band = None
            await self._async_save()
            await self._async_store_on_device()
        self._notify()

    async def _async_slot_time(self, slot: Slot, _now: datetime) -> None:
        async with self._lock:
            if not self.active:
                return
            state = self._state()
            if state is None:
                return
            if state.state == STATE_OFF:
                await self._async_store_on_device()
            elif state.state == STATE_ON and not self.manual:
                if slot.on_change == CHANGE_HARD:
                    self._cancel_ramp()
                    await self._async_apply(slot.brightness)
                elif slot.on_change == CHANGE_SOFT:
                    current = _pct(state) or slot.brightness
                    plan = self._plan(current, slot.brightness, slot.soft_duration_s, 1.0)
                    self._start_plan(plan, current, track=False)
        self._notify()

    def _plan(
        self,
        from_pct: int,
        to_pct: int,
        duration_s: float,
        exponent: float,
        start_pct: int | None = None,
    ) -> Plan:
        target = self.target
        return build_plan(
            from_pct,
            to_pct,
            duration_s,
            exponent,
            start_pct=start_pct,
            integer_steps=target.integer_steps if target else True,
            max_transition=target.max_transition if target else 5.0,
            segment_s=target.segment if target else 15.0,
        )

    # --------------------------------------------------- brightness while off

    async def _async_store_on_device(self) -> None:
        """Store the brightness the next turn-on should start with."""
        self._cancel_recheck()
        if not self.active or not self.slots or self.target is None:
            return
        if not self.target.can_store:
            return
        state = self._state()
        if state is None or state.state != STATE_OFF:
            return
        slot, occurrence = self._current()
        decision = decide_turn_on(slot, occurrence, self.memory, self.now())
        ok = await async_store_brightness(self.hass, self.target, decision.pct)
        self.last_stored_pct = decision.pct if ok else None
        if decision.valid_until is not None:
            self._recheck_unsub = async_track_point_in_time(
                self.hass, self._async_recheck, decision.valid_until + timedelta(seconds=1)
            )

    async def _async_recheck(self, _now: datetime) -> None:
        self._recheck_unsub = None
        async with self._lock:
            await self._async_store_on_device()
        self._notify()

    # ---------------------------------------------------------------- control

    async def async_set_active(self, active: bool) -> None:
        """Enable or pause the profile."""
        async with self._lock:
            self.active = active
            await self._async_save()
            if not active:
                self._cancel_ramp()
                self._cancel_recheck()
                self._band = None
            else:
                await self._async_store_on_device()
        self._notify()

    @property
    def ramp_state(self) -> str:
        """Ramp memory state for the current slot occurrence."""
        if not self.slots:
            return RAMP_IDLE
        _slot, occurrence = self._current()
        if self.memory.occurrence != occurrence:
            return RAMP_IDLE
        return self.memory.state
