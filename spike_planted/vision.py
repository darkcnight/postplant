"""Stateless image measurements on the Valorant game area.

All regions are (x0, y0, x1, y1) fractions of the 16:9 game area. Crops are resized to
their size at 1440p before measuring, so thresholds don't depend on stream resolution.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

Roi = tuple[float, float, float, float]
Box = tuple[int, int, int, int]

SLOT_ROI: Roi = (0.465, 0.000, 0.535, 0.095)   # round timer / spike icon slot
SLOT_REF = (179, 136)                          # (w, h) at 1440p
TIMER_ROI: Roi = (0.464, 0.014, 0.536, 0.076)  # timer digits inside the slot
TIMER_REF = (184, 90)
HUD_ROI: Roi = (0.30, 0.0, 0.70, 0.14)         # what tools/record.py and debug saves keep

TEMPLATE_PATH = Path(__file__).parent / "assets" / "spike_icon_mask.png"


def game_box(frame: np.ndarray, black: int = 12) -> Box:
    """Bounding box of non-black content (strips Moonlight's letterbox bars)."""
    h, w = frame.shape[:2]
    cols = np.flatnonzero(frame.max(axis=(0, 2)) > black)
    rows = np.flatnonzero(frame.max(axis=(1, 2)) > black)
    if cols.size == 0 or rows.size == 0:
        return (0, 0, w, h)
    return (int(cols[0]), int(rows[0]), int(cols[-1]) + 1, int(rows[-1]) + 1)


def crop(img: np.ndarray, roi: Roi) -> np.ndarray:
    h, w = img.shape[:2]
    x0, y0, x1, y1 = roi
    return img[int(round(y0 * h)):int(round(y1 * h)), int(round(x0 * w)):int(round(x1 * w))]


def sub_roi(inner: Roi, outer: Roi) -> Roi:
    """Express a game-area ROI as fractions of another game-area ROI (e.g. HUD_ROI)."""
    ox0, oy0, ox1, oy1 = outer
    ow, oh = ox1 - ox0, oy1 - oy0
    x0, y0, x1, y1 = inner
    return ((x0 - ox0) / ow, (y0 - oy0) / oh, (x1 - ox0) / ow, (y1 - oy0) / oh)


def spike_red_mask(slot: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(slot, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    mask = ((h <= 8) | (h >= 172)) & (s >= 150) & (v >= 110)
    return mask.astype(np.uint8) * 255


def load_template(path: Path = TEMPLATE_PATH) -> np.ndarray:
    tpl = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if tpl is None:
        raise FileNotFoundError(path)
    return (tpl > 127).astype(np.float32) * 255


def icon_score(slot: np.ndarray, template: np.ndarray) -> float:
    """0..1 match between the slot's spike-red shape and the icon template."""
    if slot.size == 0:
        return 0.0
    mask = spike_red_mask(cv2.resize(slot, SLOT_REF, interpolation=cv2.INTER_AREA))
    if not mask.any():
        return 0.0
    res = cv2.matchTemplate(mask.astype(np.float32), template, cv2.TM_CCOEFF_NORMED)
    res = np.nan_to_num(res, nan=0.0, posinf=0.0, neginf=0.0)
    return float(np.clip(res.max(), 0.0, 1.0))


def digit_count(timer: np.ndarray) -> int:
    """Tall bright glyphs in the timer: 4 for a defuse readout (SS.hh), 3 for M:SS."""
    if timer.size == 0:
        return 0
    grey = cv2.cvtColor(cv2.resize(timer, TIMER_REF, interpolation=cv2.INTER_AREA),
                        cv2.COLOR_BGR2GRAY)
    _, binary = cv2.threshold(grey, 200, 255, cv2.THRESH_BINARY)
    _, _, stats, _ = cv2.connectedComponentsWithStats(binary)
    heights = [int(s[cv2.CC_STAT_HEIGHT]) for s in stats[1:] if s[cv2.CC_STAT_AREA] > 15]
    if not heights or max(heights) < 25:
        return 0
    tallest = max(heights)
    return sum(1 for h in heights if h >= 0.6 * tallest)


def is_whiteout(game: np.ndarray, frac: float = 0.70, level: int = 235) -> bool:
    small = cv2.resize(game, None, fx=0.125, fy=0.125, interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return float((grey >= level).mean()) >= frac


@dataclass(frozen=True)
class Analysis:
    score: float
    digits: int
    whiteout: bool
    slot: np.ndarray


def analyze(game: np.ndarray, template: np.ndarray) -> Analysis:
    slot = crop(game, SLOT_ROI)
    return Analysis(
        score=icon_score(slot, template),
        digits=digit_count(crop(game, TIMER_ROI)),
        whiteout=is_whiteout(game),
        slot=slot,
    )
