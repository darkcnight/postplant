<img src="postplant/assets/icon.svg" width="96" alt="">

# Postplant

Valorant and CS2 bomb events → Home Assistant lights, on Linux.

- **Valorant**: detected from the Moonlight stream on this Linux PC. Nothing runs on
  the gaming PC; the app only looks at pixels in the Moonlight window.
- **CS2**: read from Valve's official Game State Integration (GSI) while CS2 runs on
  this Linux PC. No screen capture, and exact bomb events.

| Event | How it's detected | Default lights |
|---|---|---|
| Planted | Spike icon replaces the round timer | Red, pulsing faster in step with the spike's beeps |
| Defused | After the icon goes, the slot freezes on the time left (`03.09`) | Green flash, then restore |
| Detonated | Icon lasted ≥ 43 s, or whiteout 44–46.5 s after plant | White flash, orange glow |
| Defenders wiped | Round ended early, no defuse readout | Dark red, fade out |

Design and evidence: [docs/superpowers/specs/2026-11-11-spike-planted-rebuild-design.md](docs/superpowers/specs/2026-11-11-spike-planted-rebuild-design.md).

## Install

1. Download `Postplant-<version>-x86_64.AppImage` from
   [Releases](https://github.com/darkcnight/postplant/releases).
2. Allow it to run once: in Dolphin, right-click → *Properties* → *Permissions* → tick
   *Is executable*.
3. Double-click it. Settings → *Add Postplant to the app menu* adds a menu entry if you
   want one.

Needs KDE Plasma (Wayland) with PipeWire and GStreamer's PipeWire plugin, which most
Plasma desktops already have (Arch/CachyOS: `gst-plugin-pipewire`). CS2 mode needs nothing
extra. Settings are stored in `~/.config/postplant`.

First run:
1. **Settings** → Home Assistant URL + long-lived token → *Save & test connection*.
   Tick the lights to control.
2. **Start**. KDE asks which window to share: pick **Moonlight**. KDE remembers the
   choice (`.state/portal_token`), so later starts don't ask again.
3. **Effects** → tune each effect and use *Test on lights*.

The window must stay open while you play. Closing it stops detection and restores the lights.

### CS2

1. **Settings → Game** → *CS2 (Game State Integration)*.
2. *Install config into CS2 on this PC* writes `gamestate_integration_postplant.cfg`
   (with a private token) into CS2's `cfg` folder in your Steam library. Restart CS2.
3. **Start**. The status strip shows *CS2* green once the game sends its first update.

CS2 reports `planted` / `defused` / `exploded` directly; a Terrorist win with the bomb
down is *Defenders wiped*. The listener only accepts posts on 127.0.0.1 carrying the
token from the cfg file.

### Bomb timing

The planted pulse follows each game's bomb sound, slowed to what Zigbee and Tuya lights can
follow (about one command a second). Each pulse step only changes brightness; the colour
is set once when the bomb is planted, which halves the commands and roughly halves the delay
on Zigbee lights:

| | Timer | Beeps | Default pulse steps |
|---|---|---|---|
| Valorant | 45 s | 1/s to 25 s, 2/s to 35 s, 4/s to 40 s, 8/s to 45 s ([Mobalytics](https://mobalytics.gg/blog/valorant-spike-guide/), [GameRiv](https://gameriv.com/how-long-does-it-take-to-defuse-the-spike-in-valorant/)) | 2.0 s to 25 s, 1.4 s to 35 s, 1.0 s to 45 s |
| CS2 | 40 s | Speeds up, with a distinct sound for the last 10 s ([bo3.gg](https://bo3.gg/news/new-sound-for-10-second-warning-before-bomb-detonation-valve-has-released-a-new-update-for-cs2)) | Valorant's steps scaled to 40 s |

Settings live in `config.yaml` (created on first save, not committed) and the token in
`.env` (`HA_TOKEN=…`, mode 600). For lights whose look a saved state can't describe, set a
restore rule in `config.yaml`:

```yaml
home_assistant:
  restore:
    light.logo: {mode: script, script: script.logo_default}      # run a script after each round
    light.room_group: {mode: members, members: [light.a, light.b]}  # restore each member light
```

## Development

Run from source (system packages: `python-pyqt6 python-jeepney python-yaml
gst-plugin-pipewire`, plus `pip install --user -r requirements.txt`):

```bash
python3 -m postplant
python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest
```

Build the AppImage (downloads a portable Python and the dependencies):

```bash
tools/build_appimage.sh
```

- `tests/test_replay.py` replays `recordings/*/hud` against `labels.yaml`, when the
  recordings are present (they're gitignored, except the labels).
- `python -m postplant.replay recordings/session1` prints every event found.
- `python tools/record.py recordings/<name>` records new HUD footage while you play.
  Label it from the round-end banners, then add `labels.yaml`.
- Settings → *Save frames around each round end* writes the same format to
  `debug/`, so a misdetection can be replayed and turned into a test.
- `python tools/build_template.py <hud dir> <t0> <t1>` rebuilds the icon template.

## Future goals

- CS2 on another PC (e.g. Windows) sending GSI to Postplant over the local network.
- A Windows build of Postplant.
- Settings screen for per-light restore rules (today: `config.yaml`).
- Per-game pulse phases (CS2 currently reuses Valorant's, scaled to 40 s).

## License

[GPL-3.0](LICENSE). You can use, change and share Postplant; if you distribute a modified
version, it must stay open source under the same license.

Not affiliated with or endorsed by Riot Games. Valorant is a trademark of Riot Games, Inc.
