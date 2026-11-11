# Spike Planted — Rebuild Design

Date: 2026-11-11 · Status: approved (rev 2: outcome is read from the timer slot, not the banner)

## Goal

A desktop app on this Linux PC (CachyOS, KDE Plasma on Wayland) that watches the
Moonlight stream window, detects Valorant spike events from the picture alone, and
drives Home Assistant lights. Nothing runs on the Windows gaming PC.

Events and their light effects:

| Event | Meaning | Default effect |
|---|---|---|
| Planted | Spike icon replaced the round timer | Red, pulsing faster across the 45 s timer |
| Defused | Spike defused (by either team) | Green flash, then restore |
| Detonated | Spike exploded | White strobe, orange glow ~3 s, then restore |
| Defenders wiped | Round ended with the spike live, not defused, before detonation (attackers won by elimination) | Solid dark red ~2 s, then fade back |

## Evidence this design is based on

From recording `recordings/session1` (Swiftplay, Breeze, 3440×1440 window, 16:9 game
area 2560×1440 between black bars). Six plants were labelled from the round-end banners:

| Plant (t − 1794400000) | Icon visible | Banner | Outcome |
|---|---|---|---|
| 2211.7 | 44.8 s | LOST · SPIKE DETONATED | detonated |
| 2318.1 | 29.8 s | LOST · TEAM ELIMINATED | wiped |
| 2416.4 | 15.8 s | LOST · TEAM ELIMINATED | wiped |
| 2519.1 | 41.8 s | WON · SPIKE DEFUSED | defused |
| 2655.1 | 31.5 s | FLAWLESS · ATTACKERS | wiped |
| 2742.7 | 37.4 s | LOST · SPIKE DEFUSED | defused |

- When planted, the round timer at top-centre is replaced by a red triangular spike
  icon. It sits in a fixed slot and pulses in brightness.
- The enemy team panel next to the slot is also red, so "count red pixels in the
  top-centre" (the old approach) is unusable.
- A shape-matching score on the slot separates cleanly. Across 8,656 frames, plant frames
  score ≥ 0.7, almost all others score < 0.4, and nothing falls between 0.5 and 0.6.
- **Outcome is visible in the same slot.** When the icon disappears:
  - after a **defuse**, the slot freezes on the spike time that was left, in `SS.hh`
    format ("03.09", "07.41"; 45 − 3.09 = 41.9 s matches the defuse time). That is
    **4 tall digit glyphs**, every frame, in both defused rounds.
  - after a **detonation or a wipe**, the slot shows the post-round countdown `M:SS`
    ("0:06"), which is **3 tall digit glyphs** (1 on some bright-background frames).
- A late defuse (41.8 s) comes within 1.2 s of the 44.8 s detonation, so timing alone
  can't separate defuse from detonation. The timer readout can.
- Banners vary ("FLAWLESS · ATTACKERS" has no reason line), so they are not used.
- The only full whiteout (78 % of pixels near white) came after a round ended by
  elimination, not at a detonation. The real detonation had no whiteout (the player
  was away). Whiteout is a fast-path hint only, gated by timing.
- The icon can drop out for up to ~2 s during ability effects while still planted.
- Capture through the portal ran at 84 fps at full resolution, and the restore token
  worked without showing a dialog.

## Architecture

Python 3.14 + PyQt6, one process. Runtime dependencies are all installed system
packages: PyQt6, OpenCV 5, NumPy, GStreamer + PipeWire plugin, PyGObject,
dbus-python, httpx, PyYAML. Tests need pytest, installed into a project `.venv` created
with `--system-site-packages`.

```
spike_planted/
  __main__.py    entry point (python -m spike_planted)
  config.py      load/save config.yaml + .env, defaults, deep-merge
  vision.py      stateless image functions: game-area box, slot crop, icon score,
                 timer digit count, whiteout
  tracker.py     round state machine: readings over time → events
  ha_client.py   async HA REST client (one shared connection)
  effects.py     effect sequences driven by config
  capture.py     portal ScreenCast session + PipeWire → BGR frames
  worker.py      QThread: capture → vision → tracker; asyncio loop for HA/effects
  ui/
    main_window.py  tabs + status strip
    live_tab.py     slot preview, score meter, event log
    effects_tab.py  per-effect editors + Test buttons
    settings_tab.py HA, lights, capture, detection, debug
  assets/spike_icon_mask.png
tests/
  test_config.py  test_vision.py  test_tracker.py  test_effects.py
  test_ha_client.py  test_replay.py
tools/
  record.py          footage recorder (kept for collecting test data)
  build_template.py  builds assets/spike_icon_mask.png from a recording
recordings/session1/labels.yaml
```

`vision` and `tracker` are pure (no Qt, no I/O), and the replay test drives them from
recorded JPEGs.

## Capture (`capture.py`)

- `org.freedesktop.portal.ScreenCast`: `CreateSession` → `SelectSources` (types = window,
  cursor hidden, persist_mode = 2, restore_token if saved) → `Start` →
  `OpenPipeWireRemote`.
- The restore token lives in `.state/portal_token` and is replaced whenever the portal
  returns a new one. The KDE picker only appears on first run, on "Pick window again…",
  or when KDE rejects the token.
- GStreamer: `pipewiresrc fd=… path=… always-copy=true ! videoconvert !
  video/x-raw,format=BGR ! appsink max-buffers=1 drop=true sync=false`. The worker pulls
  the newest frame at the analysis rate (default 5 fps, configurable 2–15).
- Game-area box: the non-black bounding box of the frame, recomputed every 5 s.
- Failure handling: if no frame arrives for 3 s, the status shows "Moonlight: not
  streaming" and capture restarts every 5 s using the token. If the portal refuses (user
  cancelled or token rejected), the status shows "Pick window again" and retries stop.

## Vision (`vision.py`)

All regions are fractions of the game area. Crops are resized to a 1440p reference size
before measuring, so thresholds don't depend on the stream resolution.

- **Icon slot**: x 0.465–0.535, y 0.000–0.095 (reference size 179×136).
- **Icon score** (0–1): an HSV "spike red" mask (hue ≤ 8 or ≥ 172, sat ≥ 150, val ≥ 110)
  is template-matched (`TM_CCOEFF_NORMED`) against `assets/spike_icon_mask.png`
  (binary icon shape built from session1). Score = peak, 0 if the mask is empty.
  Default threshold 0.60.
- **Timer digit count**: timer region x 0.464–0.536, y 0.014–0.076 (reference 184×90).
  Binarise grey > 200, take connected components with area > 15, and count those
  with height ≥ 0.6 × the tallest when the tallest is ≥ 25 px. Result 4+ = defuse
  readout (`SS.hh`), 3 = countdown (`M:SS`).
- **Whiteout**: ≥ 70 % of pixels have grey ≥ 235, on a 1/8-scale game area.

## Round state machine (`tracker.py`)

Input per frame: `Reading(t, icon_score, digits, whiteout)`. Output: a list of `Event`s.

States: `IDLE`, `PLANTED`, `COOLDOWN`.

- `IDLE → PLANTED`: score ≥ threshold for 0.5 s. Plant time = the first frame of the run.
  Emits **Planted**.
- `PLANTED`:
  - A whiteout 44.0–46.5 s after the plant emits **Detonated** at once (fast path).
    Whiteouts at any other time are ignored.
  - A dip below threshold shorter than 2.5 s is ignored.
  - Once the dip has lasted 2.5 s, the round only counts as over when the slot shows a
    timer again (≥ 3 dip frames with digits ≥ 3). An empty slot means the HUD is
    covered, for example by the settings menu (seen at ~4529 s in session1), and the
    spike may still be live: keep waiting. End time = the first frame of the dip.
    Duration = end time − plant time.
    - ≥ 3 dip frames with digits ≥ 4 → **Defused**, logging `remaining = 45 − duration`.
    - else duration ≥ 43.0 s → **Detonated**; else **Defenders wiped**.
  - Still no timer 60 s after the plant → **Aborted** ("HUD hidden"), and the lights
    restore.
- After any outcome → `COOLDOWN` until the score has stayed below threshold for 5 s,
  then `IDLE`.
- `reset()` (capture lost) while `PLANTED` emits **Aborted**: effects stop and the
  lights restore.
- Every event carries a human-readable reason, e.g. "Defused: timer readout 4 digits,
  icon 41.8 s". The reasons feed the Live tab log.

Latency: Planted fires about 0.5 s after the icon appears, outcomes about 2.5 s after
round end. A detonation inside the blast fires on the whiteout frame.

## Home Assistant (`ha_client.py`)

- One `httpx.AsyncClient`, 3 s timeout, bearer token.
- `list_lights()` → `[Light(entity_id, name, state)]` for the Settings list.
- `snapshot(entities)` / `restore(snapshot)`: restores on/off and brightness, plus
  colour in the light's own `color_mode` (hs, xy, rgb, rgbw, rgbww, color_temp →
  `color_temp_kelvin`), or `effect` where one was active.
- `turn_on(entities, rgb, brightness, transition)` / `turn_off(entities)` send one call for
  all entities.
- Request failures are logged and reported through an `on_health(ok: bool)` callback
  (status strip dot turns amber). They never raise into effects.

## Effects (`effects.py`)

- Driven by the `effects:` section of config. Each effect has colour, brightness and
  timings, plus an optional `lights` override (empty = all configured lights).
- Planted: phases list (default 15 s / 1.2 s, 15 s / 0.7 s, 15 s / 0.3 s), alternating
  low/high brightness with a short HA transition.
- Defused, Detonated, Wiped: short scripted sequences, then restore the snapshot taken
  at plant.
- Aborted: restore immediately.
- Starting any effect cancels the running one. The planted sequence never fires an
  outcome by itself.

## UI (layout B: tabs + status strip)

- **Status strip** (every tab): state + seconds since plant, live match score, HA dot,
  Moonlight dot, Start/Stop.
- **Live tab**: slot preview (scaled up, refreshed at the analysis rate), score meter with
  a threshold mark, event log (last 200 lines with reasons).
- **Effects tab**: a list of four effects and an editor for the selected one (colour,
  brightness, timings or phase table, lights subset), plus Test on lights and Reset to
  default. Edits save to config at once and apply to the next event.
- **Settings tab**: HA URL; token (masked, Show button, saved to `.env`); Test
  connection; a searchable checklist of lights from HA. Capture: token status, "Pick
  window again…". Toggles for start detecting on launch and open on login. Detection:
  sensitivity, detonation cutoff, analysis rate. Debug: save frames around each round
  end to `debug/`.
- Closing the window quits. No tray icon.
- Launcher: `~/.local/share/applications/spike-planted.desktop` runs
  `python3 -m spike_planted` in this folder. "Open on login" copies it to
  `~/.config/autostart/`.

## Config

- `config.yaml`: everything except the token, written by the UI. It is deep-merged over
  built-in defaults, so missing keys get defaults.
- `.env`: `HA_TOKEN` (the existing file is reused).
- `.state/`: portal token. Gitignored, along with `.env`, `recordings/`, `debug/`,
  `.superpowers/` and `.venv/`.

## Testing

- `test_vision.py`: on saved fixture crops from session1 (planted slot, idle slot,
  enemy-red-panel frame, defuse readout, countdown readout, whiteout frame).
- `test_tracker.py`: synthetic sequences covering plant debounce, dips under 2.5 s,
  defuse by digits (including a 41.8 s late defuse), detonation by duration, whiteout
  inside and outside the window, wipe, abort, and no re-trigger in cooldown.
- `test_effects.py`, `test_ha_client.py`: against a fake HA (httpx `MockTransport`).
- `test_replay.py`: runs vision + tracker over `recordings/session1/hud` and checks the
  events against `labels.yaml` (plant time ±1 s, outcome exact). Skipped if the
  recordings aren't present.
- Manual: Test buttons on real lights, then a live game.

## Removed from the old codebase

`main.py`, the old `app.py`, `detector.py`, `effects.py`, `ha_client.py`,
`build_appimage.sh`, `SpikePlanted.AppImage` (206 MB), `requirements.txt`. Ideas worth
keeping move into the new modules.

## Out of scope

Round history, anything on the Windows PC, a tray icon, packaging beyond the
`.desktop` launcher, and detecting anything other than spike events.

## Known limitations

- If the app starts or the icon is first seen late in a plant, the measured duration
  is short. A detonation could then be reported as Defenders wiped. Defuses are
  unaffected, because the timer readout decides them.
- Replay only checks one session (Breeze). More recordings are needed to confirm
  other maps.
