"""
main.py — Spike Planted: Valorant → Home Assistant light controller

Monitors the Moonlight game stream window for Valorant spike events and
triggers Home Assistant light effects accordingly.

Usage:
    python main.py                 # normal run
    python main.py --debug         # save debug frames to ./debug_frames/
    python main.py --test planted  # fire a test effect (planted/defused/exploded)
    python main.py --calibrate     # interactive ROI calibration helper
    python main.py --check         # verify HA connection and list light entities
"""

import asyncio
import argparse
import logging
import os
import re
import sys
from pathlib import Path

import yaml

from ha_client import HAClient
from detector import SpikeDetector, SpikeState, DetectionConfig
from effects import EffectsController

# ------------------------------------------------------------------
# Logging setup
# ------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("spike.main")


# ------------------------------------------------------------------
# Config loader
# ------------------------------------------------------------------

def load_dotenv(env_path: Path = Path(".env")) -> None:
    """Load key=value pairs from .env into os.environ (simple, no deps)."""
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def expand_env_vars(cfg: dict) -> dict:
    """Recursively replace ${VAR} placeholders with environment variable values."""
    def _expand(val):
        if isinstance(val, str):
            return re.sub(
                r"\$\{(\w+)\}",
                lambda m: os.environ.get(m.group(1), m.group(0)),
                val,
            )
        if isinstance(val, dict):
            return {k: _expand(v) for k, v in val.items()}
        if isinstance(val, list):
            return [_expand(v) for v in val]
        return val
    return _expand(cfg)


def load_config(path: str = "config.yaml") -> dict:
    # Load .env first so variables are available for expansion
    load_dotenv(Path(Path(path).parent, ".env"))

    cfg_path = Path(path)
    if not cfg_path.exists():
        log.error("config.yaml not found.")
        sys.exit(1)
    with open(cfg_path) as f:
        cfg = yaml.safe_load(f)

    cfg = expand_env_vars(cfg)

    # Validate essentials
    ha = cfg.get("home_assistant", {})
    token = ha.get("token", "")
    if not ha.get("url") or not token or token.startswith("${"):
        log.error(
            "HA token not set. Edit .env and replace 'your_long_lived_token_here' "
            "with your actual token from http://192.168.1.50:8123/profile"
        )
        sys.exit(1)

    return cfg


# ------------------------------------------------------------------
# Main orchestrator
# ------------------------------------------------------------------

class SpikeApp:
    def __init__(self, cfg: dict, debug: bool = False):
        ha_cfg = cfg["home_assistant"]
        self.ha = HAClient(ha_cfg["url"], ha_cfg["token"])
        self.lights = ha_cfg["lights"]

        pulse_cfg = cfg.get("spike", {})
        self.effects = EffectsController(self.ha, self.lights, pulse_cfg)

        det_cfg = cfg.get("detection", {})
        self.det_config = DetectionConfig(
            poll_interval=det_cfg.get("poll_interval", 0.3),
            spike_roi=det_cfg.get("spike_roi", {}),
            spike_color=det_cfg.get("spike_color", {}),
            defuse_confirm_frames=det_cfg.get("defuse_confirm_frames", 3),
            debug=debug or det_cfg.get("debug", False),
        )

        self.detector = SpikeDetector(self.det_config, self._on_state_change)

    async def _on_state_change(self, state: SpikeState) -> None:
        """Dispatcher called by the detector on every state transition."""
        if state == SpikeState.PLANTED:
            log.info("🔴 SPIKE PLANTED — activating red lights")
            await self.effects.on_planted()

        elif state == SpikeState.DEFUSED:
            log.info("✅ SPIKE DEFUSED — restoring lights")
            await self.effects.on_defused()

        elif state == SpikeState.EXPLODED:
            log.info("💥 SPIKE EXPLODED — boom effect!")
            await self.effects.on_exploded()

        elif state == SpikeState.IDLE:
            log.debug("State returned to IDLE")

    async def run(self) -> None:
        log.info("=" * 55)
        log.info("  Spike Planted — Valorant HA Light Controller")
        log.info("=" * 55)
        log.info("HA URL : %s", self.ha.base_url)
        log.info("Lights : %s", self.lights)
        log.info("Watching Moonlight stream for spike events…")
        log.info("Press Ctrl+C to stop.\n")
        await self.detector.start()


# ------------------------------------------------------------------
# Test / Calibration helpers
# ------------------------------------------------------------------

async def run_test(cfg: dict, effect: str) -> None:
    """Fire a single test effect without detection."""
    ha = HAClient(cfg["home_assistant"]["url"], cfg["home_assistant"]["token"])
    lights = cfg["home_assistant"]["lights"]
    effects = EffectsController(ha, lights, cfg.get("spike", {}))

    await ha.save_state(lights)

    if effect == "planted":
        log.info("Testing: planted sequence (runs for 10 s then cancels)")
        await effects.on_planted()
        await asyncio.sleep(10)
        await effects.on_defused()

    elif effect == "defused":
        log.info("Testing: defuse effect")
        await ha.turn_on(lights, rgb=(255, 0, 0), brightness=200)
        await asyncio.sleep(1)
        await effects.on_defused()

    elif effect == "exploded":
        log.info("Testing: explosion effect")
        await ha.turn_on(lights, rgb=(255, 0, 0), brightness=200)
        await asyncio.sleep(1)
        await effects.on_exploded()

    else:
        log.error("Unknown test effect. Use: planted, defused, or exploded")


async def run_check(cfg: dict) -> None:
    """
    Verify HA connectivity and print all available light entities so you can
    copy the right entity ID into config.yaml.
    """
    ha = HAClient(cfg["home_assistant"]["url"], cfg["home_assistant"]["token"])

    log.info("Checking connection to %s …", ha.base_url)
    states = await ha._get("states")

    if states is None:
        log.error("❌  Could not reach Home Assistant. Check your URL and token.")
        return

    lights = [s for s in states if s["entity_id"].startswith("light.")]

    if not lights:
        log.warning("Connected OK but no light entities found.")
        return

    log.info("✅  Connected! Found %d light(s):\n", len(lights))
    col_w = max(len(s["entity_id"]) for s in lights) + 2
    print(f"  {'Entity ID':<{col_w}}  State   Friendly Name")
    print(f"  {'-' * col_w}  ------  -------------")
    for s in sorted(lights, key=lambda x: x["entity_id"]):
        name = s.get("attributes", {}).get("friendly_name", "")
        state = s["state"]
        marker = "🟡" if state == "on" else "⚫"
        print(f"  {marker} {s['entity_id']:<{col_w - 2}}  {state:<6}  {name}")

    configured = cfg["home_assistant"].get("lights", "")
    print(f"\n  Currently configured in config.yaml: {configured!r}")
    if isinstance(configured, str) and configured == "light.your_room_light":
        print("  ⚠️  Looks like a placeholder — update lights: in config.yaml with one of the above IDs.")


async def run_calibrate() -> None:
    """
    Calibration helper — captures a frame and shows the detected game region
    and spike ROI so you can tune config.yaml without guessing.
    """
    import mss
    import numpy as np
    import cv2

    log.info("Calibration mode — capturing screen…")
    with mss.mss() as sct:
        monitor = sct.monitors[1]
        img = sct.grab(monitor)
        frame = np.array(img)
        frame = cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)

    h, w = frame.shape[:2]
    log.info("Captured frame: %d × %d", w, h)

    # Try to find game region (strip black bars)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    centre_row = gray[h // 2, :]
    col_mask = centre_row > 10
    left_x = int(np.argmax(col_mask)) if np.any(col_mask) else 0
    right_x = int(w - np.argmax(col_mask[::-1]) - 1) if np.any(col_mask) else w
    game_w = right_x - left_x
    game_h = h

    log.info("Detected game region: x=%d–%d  (width=%d, height=%d)", left_x, right_x, game_w, game_h)
    log.info("Spike ROI default: x=%.0f–%.0f, y=0–%.0f (px)", game_w*0.42+left_x, game_w*0.58+left_x, game_h*0.08)

    # Draw on a copy
    out = frame.copy()
    cv2.rectangle(out, (left_x, 0), (right_x, h), (0, 255, 0), 3)

    roi_x1 = int(left_x + game_w * 0.42)
    roi_x2 = int(left_x + game_w * 0.58)
    roi_y2 = int(game_h * 0.08)
    cv2.rectangle(out, (roi_x1, 0), (roi_x2, roi_y2), (0, 0, 255), 3)
    cv2.putText(out, "Green=game region  Red=spike ROI", (20, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 0), 2)

    out_path = "calibration_frame.png"
    cv2.imwrite(out_path, out)
    log.info("Saved annotated frame to: %s", out_path)
    log.info("Open it to verify the red box covers the top-centre HUD area.")
    log.info("Adjust spike_roi in config.yaml if needed, then re-run --calibrate.")


# ------------------------------------------------------------------
# Entry point
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spike Planted — Valorant spike detector → Home Assistant lights"
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config file")
    parser.add_argument("--debug", action="store_true", help="Save debug frames")
    parser.add_argument(
        "--test",
        choices=["planted", "defused", "exploded"],
        metavar="EFFECT",
        help="Fire a test effect without detection (planted/defused/exploded)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify HA connection and list all available light entities",
    )
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="Capture a frame and annotate the detection regions for tuning",
    )
    args = parser.parse_args()

    if args.calibrate:
        asyncio.run(run_calibrate())
        return

    cfg = load_config(args.config)

    if args.check:
        asyncio.run(run_check(cfg))
        return

    if args.test:
        asyncio.run(run_test(cfg, args.test))
        return

    app = SpikeApp(cfg, debug=args.debug)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        log.info("Stopped by user.")


if __name__ == "__main__":
    main()
