# Spike Planted 🔴💥

> Valorant spike events → Home Assistant light effects, via Moonlight stream detection.

Watches your Moonlight window for the **spike countdown bar** in Valorant's HUD. When the spike gets planted your room lights go red and pulse faster as the timer counts down. Defuse = lights restored. Explosion = white flash burst then orange glow.

---

## How it works

```
Moonlight window (fullscreen)
        │
        ▼
  Screen capture (mss)
        │
  Strip black bars → extract 16:9 game region
        │
  Crop top-centre HUD → look for red spike bar (HSV colour threshold)
        │
  Spike state machine:  IDLE → PLANTED → DEFUSED / EXPLODED → IDLE
        │
  Home Assistant REST API → light effects
```

Detection runs entirely on-screen — no memory reading, no game files, no anti-cheat risk.

---

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure `config.yaml`

```yaml
home_assistant:
  url: "http://homeassistant.local:8123"   # your HA URL
  token: "eyJ..."                           # Long-Lived Access Token
  lights: "light.bedroom"                  # entity ID(s) to control
```

**Get a token:** HA → your profile → scroll to bottom → *Long-Lived Access Tokens* → Create Token.

For multiple lights:
```yaml
  lights:
    - "light.bedroom_lamp"
    - "light.led_strip"
```

### 3. Calibrate the detection region

```bash
python main.py --calibrate
```

This saves `calibration_frame.png` — open it and check that the **red box** covers the top-centre HUD area where the spike timer bar appears. If it's off, adjust `spike_roi` values in `config.yaml`.

> **Tip:** Run calibration while Valorant is running in a round with the spike planted (or use a screenshot from a past game).

### 4. Test your lights

```bash
python main.py --test planted   # starts pulsing red for 10 s then restores
python main.py --test defused   # green flash → restore
python main.py --test exploded  # white burst → orange glow → restore
```

### 5. Run

```bash
python main.py
```

Leave it running in a terminal while you play.  Press `Ctrl+C` to stop.

---

## Light effects

| Event     | Effect |
|-----------|--------|
| **Planted** | Immediate red → slow pulse (1.2 s) → medium pulse (0.7 s) → fast pulse (0.3 s) over 45 s |
| **Defused** | Quick green flash → restore original state |
| **Exploded** | Rapid white strobe burst → orange-red glow (3 s) → restore |

All timings and colours are configurable in `config.yaml`.

---

## Tuning detection

If the spike is not detected (or false-positives occur), tweak these in `config.yaml`:

```yaml
detection:
  spike_color:
    min_pixels: 30      # lower = more sensitive, higher = less false positives
    sat_min: 150        # lower to catch less saturated reds
    val_min: 150        # lower to catch darker reds
  spike_roi:
    x_pct: 0.42         # horizontal position of detection box (fraction 0–1)
    w_pct: 0.16         # width of detection box
    h_pct: 0.08         # height of detection box
```

Enable debug mode to save frame crops:
```yaml
detection:
  debug: true
```
Or run with: `python main.py --debug`

Saved frames appear in `./debug_frames/` with filenames like `1234567_roi.png` and `1234567_mask_42px.png`.

---

## Notes

- **Moonlight must be fullscreen** on your primary display for black-bar stripping to work correctly.
- The app detects the spike bar colour, not text — it doesn't depend on resolution or language settings.
- If your monitor is not your primary display, you may need to change `sct.monitors[1]` in `detector.py` to `sct.monitors[2]`, etc.
- Vanguard-safe: uses only screen pixels, no memory or process access.

