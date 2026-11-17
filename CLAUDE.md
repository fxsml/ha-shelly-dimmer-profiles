# CLAUDE.md – guide for agents working on this repo

Home Assistant custom integration (HACS) `shelly_dimmer_profiles`: time-slot based
brightness, ramps and "brightness stored while off" for **Shelly dimmers only**
(Gen1 block API and Gen2+/Gen3 RPC API). Open work: [`docs/ROADMAP.md`](docs/ROADMAP.md).

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install pytest-homeassistant-custom-component ruff
ruff check . && ruff format --check .
pytest -q
```

CI (`.github/workflows/validate.yml`) runs hassfest, the HACS action, ruff and pytest.
hassfest and the HACS action have **never been run yet** (see roadmap).

## Layout

| File | Role |
|---|---|
| `logic.py` | Pure logic, **no HA imports**: slot resolution (cyclic, start-only), curve math, step plans, turn-on decision incl. pause/resume. Unit-tested in `tests/test_logic.py`. Keep it HA-free. |
| `controller.py` | One `ProfileController` per config entry: listens to the light, slot start times, HA start; runs ramps as background tasks; persists ramp memory via `Store`. |
| `shelly_api.py` | Resolves host/port/gen/channel/credentials from the **Shelly integration's config entry** (via entity registry) and stores a brightness without turning on. |
| `config_flow.py` | Config entry = one light (unique_id = entity_id). Time slots = **config subentries** (`time_slot`), with form sections `ramp` and `slot_change`. |
| `switch.py`, `sensor.py`, `entity.py` | "Profile active" switch, "Active time slot" sensor (details as attributes). |
| `strings.json`, `translations/en.json`, `translations/de.json` | Keep all three in sync (`en.json` = copy of `strings.json`). |

## Behaviour (the contract)

- A slot is active from its `start` until the next slot's start, wrapping midnight. No end
  times. Start times are unique per profile (flow error `duplicate_start`).
- Slot times are local time of the HA instance (`dt_util.now()`; tests set Europe/Berlin).
- **Turn-on** (off→on with a foreign context): `set` → apply slot brightness; `ramp` → apply
  start value, then follow the curve to the slot brightness. Decision in
  `logic.decide_turn_on`:
  - same slot occurrence, ramp `done`, repeat `once` → set slot brightness
  - same occurrence, `interrupted`, within `resume_minutes` of `off_at` → continue from
    `last_pct` (remaining time derived from the curve) or `hold` at `last_pct`
  - otherwise → fresh ramp from `from_pct`
- **Turned on with an explicit brightness** (reported brightness ≠ last stored value ± 2) →
  treated as manual: nothing is applied until off.
- **Manual change while on** (state change with a foreign context and a brightness outside
  the band of our last commands ± 2) → ramp cancelled, `manual` until off.
- **Off** → ramp `running/held` becomes `interrupted` (`last_pct`, `off_at`), then the value of
  the next turn-on is stored on the device. While a pause window is open the last value is
  stored and a timer re-stores the start value when it expires.
- **Slot start**: off → store; on → `none` | `hard` | `soft` (linear ramp to the new value),
  skipped while `manual`.
- Own service calls carry a fresh `Context` whose id is remembered; that is how own state
  changes are told apart from foreign ones.
- Editing slots reloads the entry (update listener) → a running ramp is cancelled.

## Device facts (verified on real hardware)

| | Gen1 Dimmer 2 (SHDM-2, fw 1.14.0) | Dimmer Gen3 (S3DM-0A101WWL, fw 2.2.0) |
|---|---|---|
| Store brightness, stay off | `GET /light/0?brightness=X` → `ison:false`, brightness stored (2026-11-12) | `GET /rpc/Light.Set?id=0&brightness=X` → body `null`, `output:false`, brightness stored (2026-11-17) |
| Native transition | ≤ 5 s; HA's Shelly integration caps at 5000 ms (`BLOCK_MAX_TRANSITION_TIME_MS`). Per-command transition is honoured (power meter rose over ~5 s) | HA caps at 10800 s; long native fades **not yet tested** on the device |
| Brightness resolution | integer percent only | integer percent |
| Wall switch | turns on at the last stored brightness | presumably the same – see roadmap (`min_brightness_on_toggle`) |

HA's `light.turn_on` always sends `turn=on`/`on:true`; there is no core way to set a
brightness while off (architecture discussion `light.adjust` #537, never implemented).
That is why `shelly_api.py` talks to the device directly.

## Rules for live tests on real devices

- Never change device state without the owner's explicit OK in the chat, even for GETs
  that write (`/light/0?brightness=`, `/rpc/Light.Set`, `/settings?...`).
- Read the current state first and restore it afterwards.
- Prefer a dimmer the owner can watch. The owner's personal setup notes (which devices are
  off limits, when) live outside this public repo – ask.
- Do not put IP addresses, room names or other personal details into this repo.
