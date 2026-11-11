"""
detector.py — Screen-based spike state detection via the Moonlight window.

Watches the game's HUD for the spike planted indicator (the red countdown bar
that appears in the top-centre of the Valorant HUD when the spike is live).

Detection pipeline:
  1. Grab the full display frame with mss.
  2. Auto-detect the game region inside the Moonlight window (strip black bars).
  3. Crop the spike-ROI from the HUD.
  4. Threshold for the spike bar's distinctive red-orange colour in HSV.
  5. If enough coloured pixels → spike is PLANTED.
  6. When the bar disappears → DEFUSED or round ended.
  7. If the bar disappears instantly (< 1 s) + a bright white flash is detected
     → EXPLODED (the whole screen whites out momentarily on detonation).
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Callable, Awaitable

import cv2
import mss
import numpy as np

log = logging.getLogger("spike.detector")


class SpikeState(Enum):
    IDLE = auto()
    PLANTED = auto()
    DEFUSED = auto()
    EXPLODED = auto()


@dataclass
class DetectionConfig:
    poll_interval: float = 0.3
    spike_roi: dict = field(default_factory=lambda: {
        "x_pct": 0.42, "y_pct": 0.00, "w_pct": 0.16, "h_pct": 0.08
    })
    spike_color: dict = field(default_factory=lambda: {
        "hue_min": 0, "hue_max": 20,
        "sat_min": 150, "val_min": 150,
        "min_pixels": 30
    })
    defuse_confirm_frames: int = 3
    debug: bool = False


Callback = Callable[[SpikeState], Awaitable[None]]


class SpikeDetector:
    """
    Async screen-capture detector.  Call start() to run; it notifies the
    provided callback whenever the spike state changes.
    """

    def __init__(self, config: DetectionConfig, on_state_change: Callback):
        self.cfg = config
        self._on_change = on_state_change
        self._state = SpikeState.IDLE
        self._defuse_counter = 0
        self._plant_time: float | None = None
        self._running = False

        if config.debug:
            os.makedirs("debug_frames", exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(self) -> None:
        self._running = True
        log.info("Detector started — watching for spike events")
        with mss.mss() as sct:
            while self._running:
                try:
                    await self._tick(sct)
                except Exception as e:
                    log.error("Detector error: %s", e)
                await asyncio.sleep(self.cfg.poll_interval)

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Core frame logic
    # ------------------------------------------------------------------

    async def _tick(self, sct: mss.mss) -> None:
        frame = self._grab_frame(sct)
        if frame is None:
            return

        game_frame = self._extract_game_region(frame)
        if game_frame is None:
            return

        spike_visible = self._detect_spike_bar(game_frame)
        explosion = self._detect_explosion(game_frame) if not spike_visible else False

        await self._update_state(spike_visible, explosion)

    # ------------------------------------------------------------------
    # Screen capture
    # ------------------------------------------------------------------

    def _grab_frame(self, sct: mss.mss) -> np.ndarray | None:
        """Capture the primary monitor."""
        try:
            monitor = sct.monitors[1]  # primary display
            img = sct.grab(monitor)
            frame = np.array(img)
            return cv2.cvtColor(frame, cv2.COLOR_BGRA2BGR)
        except Exception as e:
            log.debug("Grab failed: %s", e)
            return None

    # ------------------------------------------------------------------
    # Game region extraction (strip Moonlight black bars)
    # ------------------------------------------------------------------

    def _extract_game_region(self, frame: np.ndarray) -> np.ndarray | None:
        """
        Moonlight renders a 16:9 game stream with black bars on a widescreen
        monitor (e.g. 3440×1440).  We detect the non-black content region.
        """
        h, w = frame.shape[:2]

        # Convert to grayscale and find columns/rows with content
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Look at horizontal strips near centre to find left/right black bars
        centre_row = gray[h // 2, :]
        col_mask = centre_row > 10

        if not np.any(col_mask):
            return frame  # no bars detected, return full frame

        left_x = int(np.argmax(col_mask))
        right_x = int(w - np.argmax(col_mask[::-1]) - 1)

        # Look at vertical strips near centre to find top/bottom black bars
        centre_col = gray[:, w // 2]
        row_mask = centre_col > 10

        if not np.any(row_mask):
            return frame

        top_y = int(np.argmax(row_mask))
        bot_y = int(h - np.argmax(row_mask[::-1]) - 1)

        game = frame[top_y:bot_y, left_x:right_x]
        if game.size == 0:
            return frame
        return game

    # ------------------------------------------------------------------
    # Spike bar detection
    # ------------------------------------------------------------------

    def _detect_spike_bar(self, game: np.ndarray) -> bool:
        """
        Returns True when the spike countdown bar is visible in the HUD.

        The spike bar in Valorant is a red/orange progress bar centred at the
        top of the screen.  We isolate its region and threshold for that colour.
        """
        roi = self._get_roi(game)
        if roi is None or roi.size == 0:
            return False

        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        sc = self.cfg.spike_color

        # Red wraps in HSV — check two ranges
        mask1 = cv2.inRange(
            hsv,
            np.array([sc["hue_min"], sc["sat_min"], sc["val_min"]]),
            np.array([sc["hue_max"], 255, 255]),
        )
        # Second red range (hue 170–180)
        mask2 = cv2.inRange(
            hsv,
            np.array([170, sc["sat_min"], sc["val_min"]]),
            np.array([180, 255, 255]),
        )
        combined = cv2.bitwise_or(mask1, mask2)
        pixel_count = int(np.count_nonzero(combined))

        if self.cfg.debug:
            self._save_debug(roi, combined, pixel_count)

        return pixel_count >= sc["min_pixels"]

    def _detect_explosion(self, game: np.ndarray) -> bool:
        """
        Detects the bright white flash that happens the instant the spike
        detonates.  Returns True if the average brightness of the frame
        exceeds a high threshold.
        """
        # Only check if we were in PLANTED state (avoid false positives)
        if self._state != SpikeState.PLANTED:
            return False

        gray = cv2.cvtColor(game, cv2.COLOR_BGR2GRAY)
        mean_brightness = float(np.mean(gray))
        return mean_brightness > 220  # very bright = explosion flash

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    async def _update_state(self, spike_visible: bool, explosion: bool) -> None:
        old = self._state

        if self._state == SpikeState.IDLE:
            if spike_visible:
                self._state = SpikeState.PLANTED
                self._plant_time = time.monotonic()
                self._defuse_counter = 0
                log.info("🔴 Spike PLANTED detected!")

        elif self._state == SpikeState.PLANTED:
            if explosion:
                self._state = SpikeState.EXPLODED
                log.info("💥 Spike EXPLODED detected!")
            elif not spike_visible:
                self._defuse_counter += 1
                if self._defuse_counter >= self.cfg.defuse_confirm_frames:
                    # Check how long the spike was live —
                    # if it's close to 45 s it probably blew up,
                    # but we already handle that via explosion flash.
                    # Reaching here = defused (or round ended cleanly).
                    self._state = SpikeState.DEFUSED
                    log.info("✅ Spike DEFUSED / round ended detected!")
            else:
                self._defuse_counter = 0  # reset if bar reappears

        elif self._state in (SpikeState.DEFUSED, SpikeState.EXPLODED):
            # Reset once spike bar is fully gone for a few frames
            if not spike_visible:
                self._defuse_counter += 1
                if self._defuse_counter >= self.cfg.defuse_confirm_frames + 2:
                    self._state = SpikeState.IDLE
                    self._defuse_counter = 0
                    log.debug("State reset to IDLE")
            else:
                self._defuse_counter = 0

        if self._state != old:
            await self._on_change(self._state)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_roi(self, game: np.ndarray) -> np.ndarray | None:
        h, w = game.shape[:2]
        r = self.cfg.spike_roi
        x = int(w * r["x_pct"])
        y = int(h * r["y_pct"])
        bw = int(w * r["w_pct"])
        bh = int(h * r["h_pct"])
        if bw <= 0 or bh <= 0:
            return None
        return game[y:y + bh, x:x + bw]

    def _save_debug(self, roi: np.ndarray, mask: np.ndarray, px: int) -> None:
        ts = int(time.time() * 1000)
        cv2.imwrite(f"debug_frames/{ts}_roi.png", roi)
        cv2.imwrite(f"debug_frames/{ts}_mask_{px}px.png", mask)

