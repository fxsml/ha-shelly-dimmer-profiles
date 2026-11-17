"""Tests for the pure logic."""

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.shelly_dimmer_profiles.logic import (
    DECISION_HOLD,
    DECISION_RAMP,
    DECISION_SET,
    RAMP_DONE,
    RAMP_INTERRUPTED,
    REPEAT_ONCE,
    RESUME_HOLD,
    TURN_ON_RAMP,
    Ramp,
    RampMemory,
    Slot,
    active_slot,
    build_plan,
    curve_pct,
    decide_turn_on,
    in_band,
    next_slot,
    occurrence_key,
    progress_for,
)

TZ = ZoneInfo("Europe/Berlin")
RAMP = Ramp(from_pct=1, duration_s=300, curve="quadratic", resume_minutes=5)
MORNING = Slot("m", "Morning", time(6), 100, TURN_ON_RAMP, RAMP)
DAY = Slot("d", "Day", time(7), 100)
NIGHT = Slot("n", "Night", time(23), 1)
SLOTS = [DAY, NIGHT, MORNING]


def at(hour: int, minute: int = 0, day: int = 17) -> datetime:
    return datetime(2026, 11, day, hour, minute, tzinfo=TZ)


@pytest.mark.parametrize(
    ("now", "slot", "start"),
    [
        (at(5, 59), NIGHT, at(23, 0, 16)),
        (at(6, 0), MORNING, at(6)),
        (at(6, 59), MORNING, at(6)),
        (at(7, 0), DAY, at(7)),
        (at(22, 59), DAY, at(7)),
        (at(23, 0), NIGHT, at(23)),
        (at(0, 30), NIGHT, at(23, 0, 16)),
    ],
)
def test_active_slot(now, slot, start):
    assert active_slot(SLOTS, now) == (slot, start)


def test_next_slot_wraps():
    assert next_slot(SLOTS, at(23, 30)) == (MORNING, at(6, 0, 18))
    assert next_slot(SLOTS, at(6, 30)) == (DAY, at(7))


def test_curve_roundtrip():
    for pct in (1, 2, 26, 50, 100):
        assert round(curve_pct(1, 100, 2, progress_for(1, 100, 2, pct))) == pct
    assert progress_for(5, 5, 2, 5) == 1.0
    assert progress_for(100, 10, 1, 55) == pytest.approx(0.5)


def test_integer_plan_matches_tested_ramp():
    plan = build_plan(1, 100, 300, 2.0)
    assert [s.pct for s in plan.steps] == list(range(2, 101))
    assert plan.steps[0].delay == pytest.approx(30.15, abs=0.05)
    assert plan.steps[0].transition == 5.0
    assert plan.steps[-1].delay == pytest.approx(1.5, abs=0.05)
    assert sum(s.delay for s in plan.steps) == pytest.approx(300)
    assert plan.tail == 0


def test_integer_plan_resume_uses_remaining_time():
    plan = build_plan(1, 100, 300, 2.0, start_pct=26)
    assert plan.steps[0].pct == 27
    elapsed = 300 * progress_for(1, 100, 2.0, 26)
    assert sum(s.delay for s in plan.steps) == pytest.approx(300 - elapsed)


def test_integer_plan_down():
    plan = build_plan(100, 1, 10, 1.0)
    assert [s.pct for s in plan.steps] == list(range(99, 0, -1))


def test_segment_plan():
    plan = build_plan(1, 100, 300, 2.0, integer_steps=False, segment_s=15, max_transition=60)
    assert len(plan.steps) == 20
    assert plan.steps[0].delay == 0
    assert all(s.transition == 15 for s in plan.steps)
    assert plan.steps[-1].pct == 100
    assert plan.tail == 15
    pcts = [s.pct for s in plan.steps]
    assert pcts == sorted(pcts)


def test_plan_edge_cases():
    assert build_plan(50, 50, 10, 1).steps == []
    assert [s.pct for s in build_plan(1, 50, 0, 1).steps] == [50]


def test_decide_set_slot():
    assert decide_turn_on(DAY, "x", RampMemory(), at(8)).kind == DECISION_SET
    assert decide_turn_on(DAY, "x", RampMemory(), at(8)).pct == 100


def test_decide_fresh_ramp():
    decision = decide_turn_on(MORNING, "occ", RampMemory(), at(6, 10))
    assert (decision.kind, decision.pct, decision.resume) == (DECISION_RAMP, 1, False)


def test_decide_resume_within_pause():
    memory = RampMemory("occ", RAMP_INTERRUPTED, 20, at(6, 12))
    decision = decide_turn_on(MORNING, "occ", memory, at(6, 14))
    assert (decision.kind, decision.pct, decision.resume) == (DECISION_RAMP, 20, True)
    assert decision.valid_until == at(6, 17)


def test_decide_restart_after_pause():
    memory = RampMemory("occ", RAMP_INTERRUPTED, 20, at(6, 12))
    decision = decide_turn_on(MORNING, "occ", memory, at(6, 18))
    assert (decision.kind, decision.pct) == (DECISION_RAMP, 1)


def test_decide_other_occurrence_restarts():
    memory = RampMemory("old", RAMP_INTERRUPTED, 20, at(6, 12))
    assert decide_turn_on(MORNING, "occ", memory, at(6, 13)).pct == 1


def test_decide_hold_mode():
    slot = Slot("m", "M", time(6), 100, TURN_ON_RAMP, Ramp(1, 300, resume_mode=RESUME_HOLD))
    memory = RampMemory("occ", RAMP_INTERRUPTED, 40, at(6, 12))
    decision = decide_turn_on(slot, "occ", memory, at(6, 13))
    assert (decision.kind, decision.pct) == (DECISION_HOLD, 40)


def test_decide_once_done():
    slot = Slot("m", "M", time(6), 80, TURN_ON_RAMP, Ramp(1, 300, repeat=REPEAT_ONCE))
    memory = RampMemory("occ", RAMP_DONE, 80)
    assert decide_turn_on(slot, "occ", memory, at(6, 30)).kind == DECISION_SET
    # repeat every: ramps again
    assert decide_turn_on(MORNING, "occ", memory, at(6, 30)).kind == DECISION_RAMP


def test_resume_disabled():
    slot = Slot("m", "M", time(6), 100, TURN_ON_RAMP, Ramp(1, 300, resume_minutes=0))
    memory = RampMemory("occ", RAMP_INTERRUPTED, 40, at(6, 12))
    assert decide_turn_on(slot, "occ", memory, at(6, 12)).pct == 1


def test_memory_roundtrip():
    memory = RampMemory("occ", RAMP_INTERRUPTED, 40, at(6, 12))
    assert RampMemory.from_dict(memory.as_dict()) == memory
    assert RampMemory.from_dict(None) == RampMemory()


def test_in_band_and_occurrence():
    assert in_band(51, (40, 50))
    assert not in_band(60, (40, 50))
    assert occurrence_key(MORNING, at(6)) == "m@" + at(6).isoformat()
    assert at(6) + timedelta(0) == at(6)
