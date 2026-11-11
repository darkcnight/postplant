# Spike Planted

Valorant spike events → Home Assistant lights, detected from the Moonlight stream on
this Linux PC. Nothing runs on the gaming PC. The app only looks at pixels in the
Moonlight window.

| Event | How it's detected | Default lights |
|---|---|---|
| Planted | Spike icon replaces the round timer | Red, pulsing faster over 45 s |
| Defused | After the icon goes, the slot freezes on the time left (`03.09`) | Green flash, then restore |
| Detonated | Icon lasted ≥ 43 s, or whiteout 44–46.5 s after plant | White strobe, orange glow |
| Defenders wiped | Round ended early, no defuse readout | Dark red, fade out |

Design and evidence: [docs/superpowers/specs/2026-11-11-spike-planted-rebuild-design.md](docs/superpowers/specs/2026-11-11-spike-planted-rebuild-design.md).

## Requirements

KDE Plasma (Wayland) with `xdg-desktop-portal-kde` and PipeWire.

- System packages: `python-pyqt6 python-gobject python-jeepney python-yaml
  gst-plugin-pipewire gst-plugins-base`
- pip (already in `~/.local` here): `pip install --user -r requirements.txt`
  (httpx, opencv-python, numpy)

## Run

```bash
python3 -m spike_planted
```

Or install the app-menu entry once and launch "Spike Planted" from the menu:

```bash
python3 -m spike_planted.launcher
```

First run:
1. **Settings** → Home Assistant URL + long-lived token → *Save & test connection*.
   Tick the lights to control.
2. **Start**. KDE asks which window to share: pick **Moonlight**. KDE remembers the
   choice (`.state/portal_token`), so later starts don't ask again.
3. **Effects** → tune each effect and use *Test on lights*.

The window must stay open while you play. Closing it stops detection and restores the lights.

## Development

```bash
python3 -m venv --system-site-packages .venv && .venv/bin/pip install pytest
.venv/bin/python -m pytest
```

- `tests/test_replay.py` replays `recordings/*/hud` against `labels.yaml`, when the
  recordings are present (they're gitignored, except the labels).
- `python -m spike_planted.replay recordings/session1` prints every event found.
- `python tools/record.py recordings/<name>` records new HUD footage while you play.
  Label it from the round-end banners, then add `labels.yaml`.
- Settings → *Save frames around each round end* writes the same format to
  `debug/`, so a misdetection can be replayed and turned into a test.
- `python tools/build_template.py <hud dir> <t0> <t1>` rebuilds the icon template.
