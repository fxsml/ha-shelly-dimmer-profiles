# Shelly Dimmer Profiles

Time-based brightness for Shelly dimmers (Gen1 and Gen2+/Gen3) in Home Assistant:

- **Time slots** – any number per dimmer. A slot is active from its start time until the
  next slot starts (wrapping around midnight), so there are no gaps or overlaps.
- **On turn-on** – set the slot's brightness, or start a **ramp** (start brightness,
  duration, curve).
- **Short pauses** – turned on again within the pause time, the ramp continues from the
  last brightness (or stays there) instead of starting over. Ramp on every turn-on or only
  once per slot.
- **Slot start while on** – none (applies from the next turn-on), hard or soft transition.
- **No flash at the wall switch** – while the light is off, the brightness of the next
  turn-on is stored on the dimmer directly, without switching it on. No `rest_command`
  needed: host and credentials are taken from the Shelly integration.
- **Manual changes win** – changing the brightness by hand (switch, app) stops the ramp
  until the light is turned off.
- English and German UI (follows the Home Assistant user language).

## How it works on the devices

| | Gen1 (e.g. Dimmer 2) | Gen2+ (e.g. Dimmer Gen3) |
|---|---|---|
| Store brightness while off | `GET /light/<ch>?brightness=X` | `GET /rpc/Light.Set?id=<ch>&brightness=X` |
| Ramp | every whole percent on its own, timed along the curve, ≤ 5 s native transition per step (device limit) | curve split into 15 s segments, each faded natively |

Home Assistant's `light.turn_on` always switches a light on, which is why storing the
brightness for the next turn-on talks to the device directly.

## Installation

[![Open your Home Assistant instance and open this repository inside HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=fxsml&repository=ha-shelly-dimmer-profiles&category=integration)

Or manually: HACS → ⋮ → *Custom repositories* → add
`https://github.com/fxsml/ha-shelly-dimmer-profiles` as *Integration*.

Install, restart Home Assistant, then add the integration:

[![Open your Home Assistant instance and start setting up Shelly Dimmer Profiles.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=shelly_dimmer_profiles)

(or *Settings → Devices & services → Add integration → Shelly Dimmer Profiles*). Pick a
Shelly light and add time slots on the integration entry (*+ Add time slot*).

Requires Home Assistant 2025.7 or newer (config subentries).

## Example: bathroom

| Name | Start | Brightness | On turn-on | Slot start while on |
|---|---|---|---|---|
| Morning | 06:00 | 100 % | ramp 1 → 100 % in 5 min, quadratic, pause time 5 min | none |
| Day | 07:00 | 100 % | set | none |
| Night | 23:00 | 1 % | set | soft, 10 s |

## Entities

- `switch.<name>_profile_active` – pause the profile (the light behaves as without it)
- `sensor.<name>_active_time_slot` – active slot; attributes: ramp state, manual
  override, stored brightness, next slot start

## Development

```bash
pip install pytest-homeassistant-custom-component ruff
ruff check . && pytest -q
```
