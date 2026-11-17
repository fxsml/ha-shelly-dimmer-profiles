"""Pure scheduling and ramp logic (no Home Assistant imports, unit-testable)."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

TURN_ON_SET = "set"
TURN_ON_RAMP = "ramp"

REPEAT_EVERY = "every"
REPEAT_ONCE = "once"

RESUME_CONTINUE = "continue"
RESUME_HOLD = "hold"

CHANGE_NONE = "none"
CHANGE_HARD = "hard"
CHANGE_SOFT = "soft"

CURVES: dict[str, float] = {"linear": 1.0, "quadratic": 2.0, "cubic": 3.0}

RAMP_IDLE = "idle"
RAMP_RUNNING = "running"
RAMP_HELD = "held"
RAMP_INTERRUPTED = "interrupted"
RAMP_DONE = "done"
RAMP_MANUAL = "manual"

DECISION_SET = "set"
DECISION_RAMP = "ramp"
DECISION_HOLD = "hold"


@dataclass(frozen=True)
class Ramp:
    """Ramp settings of a time slot."""

    from_pct: int
    duration_s: float
    curve: str = "quadratic"
    repeat: str = REPEAT_EVERY
    resume_minutes: float = 5.0
    resume_mode: str = RESUME_CONTINUE

    @property
    def exponent(self) -> float:
        """Curve exponent (1 = linear)."""
        return CURVES.get(self.curve, 2.0)


@dataclass(frozen=True)
class Slot:
    """A time slot: active from `start` until the next slot starts (cyclic)."""

    slot_id: str
    name: str
    start: time
    brightness: int
    on_turn_on: str = TURN_ON_SET
    ramp: Ramp | None = None
    on_change: str = CHANGE_NONE
    soft_duration_s: float = 10.0

    @property
    def uses_ramp(self) -> bool:
        """Whether turning on starts a ramp."""
        return self.on_turn_on == TURN_ON_RAMP and self.ramp is not None


@dataclass
class RampMemory:
    """What happened with the ramp of the current slot occurrence."""

    occurrence: str | None = None
    state: str = RAMP_IDLE
    last_pct: int | None = None
    off_at: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialize for storage."""
        data = asdict(self)
        data["off_at"] = self.off_at.isoformat() if self.off_at else None
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RampMemory:
        """Deserialize from storage."""
        if not data:
            return cls()
        off_at = data.get("off_at")
        return cls(
            occurrence=data.get("occurrence"),
            state=data.get("state", RAMP_IDLE),
            last_pct=data.get("last_pct"),
            off_at=datetime.fromisoformat(off_at) if off_at else None,
        )


@dataclass(frozen=True)
class TurnOnDecision:
    """What to do when the light is (or will be) turned on."""

    kind: str
    pct: int
    resume: bool = False
    valid_until: datetime | None = None


@dataclass(frozen=True)
class Step:
    """Wait `delay` seconds, then set `pct` with a `transition` in seconds."""

    delay: float
    pct: int
    transition: float


@dataclass(frozen=True)
class Plan:
    """A sequence of steps; `tail` is the time the last transition still needs."""

    steps: list[Step] = field(default_factory=list)
    tail: float = 0.0


def active_slot(slots: list[Slot], now: datetime) -> tuple[Slot, datetime]:
    """Return the active slot and the start of its current occurrence."""
    if not slots:
        raise ValueError("no slots")
    ordered = sorted(slots, key=lambda s: s.start)
    current: Slot | None = None
    for slot in ordered:
        if slot.start <= now.time().replace(tzinfo=None):
            current = slot
    day = now.date()
    if current is None:
        current = ordered[-1]
        day = day - timedelta(days=1)
    return current, datetime.combine(day, current.start, tzinfo=now.tzinfo)


def next_slot(slots: list[Slot], now: datetime) -> tuple[Slot, datetime]:
    """Return the next slot to become active and when."""
    ordered = sorted(slots, key=lambda s: s.start)
    for slot in ordered:
        if slot.start > now.time().replace(tzinfo=None):
            return slot, datetime.combine(now.date(), slot.start, tzinfo=now.tzinfo)
    first = ordered[0]
    return first, datetime.combine(now.date() + timedelta(days=1), first.start, tzinfo=now.tzinfo)


def occurrence_key(slot: Slot, start: datetime) -> str:
    """Identify one occurrence of a slot (slot id + start)."""
    return f"{slot.slot_id}@{start.isoformat()}"


def curve_pct(from_pct: float, to_pct: float, exponent: float, progress: float) -> float:
    """Brightness at `progress` (0..1) of a curve from `from_pct` to `to_pct`."""
    progress = min(max(progress, 0.0), 1.0)
    return from_pct + (to_pct - from_pct) * progress**exponent


def progress_for(from_pct: float, to_pct: float, exponent: float, pct: float) -> float:
    """Inverse of curve_pct: progress (0..1) at which `pct` is reached."""
    if to_pct == from_pct:
        return 1.0
    ratio = (pct - from_pct) / (to_pct - from_pct)
    ratio = min(max(ratio, 0.0), 1.0)
    return ratio ** (1.0 / exponent)


def _levels(start: int, end: int) -> range:
    step = 1 if end > start else -1
    return range(start + step, end + step, step)


def build_plan(
    from_pct: int,
    to_pct: int,
    duration_s: float,
    exponent: float,
    *,
    start_pct: int | None = None,
    integer_steps: bool = True,
    max_transition: float = 5.0,
    segment_s: float = 15.0,
) -> Plan:
    """Plan a ramp along the curve from `from_pct` to `to_pct` over `duration_s`.

    `start_pct` resumes the ramp at that brightness (time left is derived from
    the curve). With `integer_steps` every whole percent is set on its own at
    the moment the curve reaches it (devices limited to short transitions, e.g.
    Shelly Gen1). Otherwise the curve is split into segments that the device
    fades natively (Shelly Gen2+).
    """
    start = from_pct if start_pct is None else start_pct
    if start == to_pct:
        return Plan()
    if duration_s <= 0:
        return Plan([Step(0.0, to_pct, 0.0)])

    t_start = duration_s * progress_for(from_pct, to_pct, exponent, start)

    if integer_steps:
        steps: list[Step] = []
        previous = t_start
        for level in _levels(int(round(start)), to_pct):
            t_level = duration_s * progress_for(from_pct, to_pct, exponent, level)
            delay = max(t_level - previous, 0.0)
            steps.append(Step(delay, level, round(min(delay, max_transition), 2)))
            previous = t_level
        return Plan(steps)

    remaining = duration_s - t_start
    count = max(1, math.ceil(remaining / segment_s))
    segment = remaining / count
    steps = []
    delay = 0.0
    for index in range(1, count + 1):
        progress = (t_start + segment * index) / duration_s
        pct = int(round(curve_pct(from_pct, to_pct, exponent, progress)))
        steps.append(Step(delay, pct, round(min(segment, max_transition), 2)))
        delay = segment
    return Plan(steps, tail=steps[-1].transition)


def decide_turn_on(
    slot: Slot, occurrence: str, memory: RampMemory, now: datetime
) -> TurnOnDecision:
    """Decide the behaviour on turn-on (also the value to store while off)."""
    if not slot.uses_ramp:
        return TurnOnDecision(DECISION_SET, slot.brightness)
    ramp = slot.ramp
    assert ramp is not None
    same = memory.occurrence == occurrence

    if same and memory.state == RAMP_DONE and ramp.repeat == REPEAT_ONCE:
        return TurnOnDecision(DECISION_SET, slot.brightness)

    if (
        same
        and memory.state == RAMP_INTERRUPTED
        and ramp.resume_minutes > 0
        and memory.off_at is not None
        and memory.last_pct is not None
    ):
        until = memory.off_at + timedelta(minutes=ramp.resume_minutes)
        if now <= until:
            kind = DECISION_HOLD if ramp.resume_mode == RESUME_HOLD else DECISION_RAMP
            return TurnOnDecision(kind, memory.last_pct, resume=True, valid_until=until)

    return TurnOnDecision(DECISION_RAMP, ramp.from_pct)


def in_band(pct: int, band: tuple[int, int], tolerance: int = 2) -> bool:
    """Whether a reported brightness is explained by our own commands."""
    low, high = min(band), max(band)
    return low - tolerance <= pct <= high + tolerance
