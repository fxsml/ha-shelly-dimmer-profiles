# Roadmap / open points

State as of 2026-11-17 (v0.1.0, not yet installed on a real Home Assistant).
42 tests pass against a fake Shelly light in HA 2026.10 (pytest-homeassistant-custom-component).

## Must do before the first release

1. **First live install.** Install via HACS (custom repository), add a profile for a dimmer
   the owner can watch, verify end to end: config flow + subentry UI (sections, selectors,
   translations in EN and DE), switch/sensor, storing while off, ramp, pause/resume, slot
   change hard/soft, manual override. Then replace the owner's existing YAML automation
   for the first room.
2. **Run hassfest and the HACS action** (CI) and fix what they report. Likely candidates:
   translation schema for subentry `sections` / `data_description`, selector translation
   keys, `hacs.json` (`homeassistant` minimum is a guess: `2025.7.0`, chosen for config
   subentries incl. reconfigure – verify against the HA changelog).
3. **LICENSE** – none yet; owner decides (MIT suggested).
4. **Brand/icon** – optional, for HACS listing.

## Gen3 / Gen2+ (RPC) – verify on the device

- **Native long fades:** ramps are split into 15 s segments (`RPC_SEGMENT`), each sent as
  `light.turn_on` with `transition: 15`. Verify the device fades smoothly and that HA's
  Shelly integration passes the transition (`transition_duration`, min 0.5 s).
- **False manual override during native fades:** the device may push intermediate
  brightness values while fading. They should lie inside the band (previous → target ± 2),
  but verify in the logs (`debug` for `custom_components.shelly_dimmer_profiles`).
- **`min_brightness_on_toggle` (default 3) and `range_map` (default [5, 100])** in
  `Light.GetConfig`: a stored 1 % may turn on at a higher level via the button. Check what
  the wall switch actually does after storing 1 %, and whether the "explicit brightness"
  check (stored ± 2) then misfires → if so, compare against
  `max(stored, min_brightness_on_toggle)` or read the config once per device.
- **Credentials:** Gen2+ digest auth (`aiohttp.DigestAuthMiddleware`, user `admin`) and Gen1
  basic auth are implemented but **untested** (owner's devices have no password).

## Behaviour gaps / ideas

- **Manual override is "until off" only.** Open question from the design: optionally hand
  control back to the profile after N minutes.
- **HA restart while a ramp runs:** the ramp is not resumed; the light stays at its current
  brightness until turned off. Memory is persisted (`running` → `interrupted` on the next off),
  so the pause/resume logic still works afterwards.
- **Editing time slots reloads the entry** and cancels a running ramp.
- **Shelly host changes** (DHCP) are picked up only on reload of this entry. Option: listen
  for updates of the Shelly config entry and reload.
- **Gen1 RGBW2 (white mode)** channels (`light_<n>`) should work but are untested.
- **Services** (e.g. `start_ramp`, `apply_slot`) and a **diagnostics** platform are not
  implemented.
- **One light per profile.** Groups / several lights per profile are out of scope for now.
- **Storing also for "soft" targets:** when a soft slot change is interrupted by turning off,
  the stored value is the slot value (correct), but there is no resume for soft changes.

## Done

- Gen1 storing verified (2026-11-12), Gen3 storing verified (2026-11-17), Gen1 per-command
  transition verified via power meter (2026-11-17).
- Gen1 ramp in 1 % steps along the curve matches the owner's tested YAML automation
  (1 % → 100 % in 5 min, quadratic).
