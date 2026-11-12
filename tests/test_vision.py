from pathlib import Path

import cv2
import numpy as np
import pytest

from postplant.vision import (HUD_ROI, SLOT_ROI, TIMER_ROI, analyze, crop, digit_count,
                                  game_box, icon_score, is_whiteout, load_template, sub_roi)

FIX = Path(__file__).parent / "fixtures"
SLOT_IN_HUD = sub_roi(SLOT_ROI, HUD_ROI)
TIMER_IN_HUD = sub_roi(TIMER_ROI, HUD_ROI)


@pytest.fixture(scope="module")
def template():
    return load_template()


def hud(name):
    img = cv2.imread(str(FIX / name))
    assert img is not None, name
    return img


def test_planted_icon_scores_high(template):
    assert icon_score(crop(hud("planted_hud.jpg"), SLOT_IN_HUD), template) >= 0.85


def test_round_timer_next_to_red_enemy_panel_scores_low(template):
    assert icon_score(crop(hud("idle_red_panel_hud.jpg"), SLOT_IN_HUD), template) < 0.3


def test_whole_red_hud_strip_would_fool_naive_red_count():
    # Why the slot matters: the strip around the timer is full of enemy-team red.
    hsv = cv2.cvtColor(hud("idle_red_panel_hud.jpg"), cv2.COLOR_BGR2HSV)
    reddish = ((hsv[..., 0] <= 10) | (hsv[..., 0] >= 170)) & (hsv[..., 1] > 80)
    assert reddish.sum() > 1000


def test_defuse_readout_has_four_digits():
    assert digit_count(crop(hud("defuse_readout_hud.jpg"), TIMER_IN_HUD)) >= 4


def test_post_round_countdown_has_fewer_than_four_digits():
    assert digit_count(crop(hud("countdown_hud.jpg"), TIMER_IN_HUD)) < 4


def test_icon_has_no_digits():
    assert digit_count(crop(hud("planted_hud.jpg"), TIMER_IN_HUD)) < 4


def test_whiteout():
    assert is_whiteout(cv2.imread(str(FIX / "whiteout_full.jpg")))
    assert not is_whiteout(cv2.imread(str(FIX / "normal_full.jpg")))


def test_game_box_strips_letterbox():
    frame = cv2.imread(str(FIX / "letterboxed_window.jpg"))  # 860x360, 16:9 in the middle
    x0, y0, x1, y1 = game_box(frame)
    assert abs(x0 - 110) <= 3 and abs(x1 - 750) <= 3
    assert y0 <= 2 and y1 >= 358


def test_game_box_all_black_returns_full_frame():
    assert game_box(np.zeros((10, 20, 3), np.uint8)) == (0, 0, 20, 10)


def test_empty_inputs_are_safe(template):
    empty = np.zeros((0, 0, 3), np.uint8)
    assert icon_score(empty, template) == 0.0
    assert digit_count(empty) == 0
    assert icon_score(np.zeros((136, 179, 3), np.uint8), template) == 0.0


def test_analyze_on_synthetic_game_area(template):
    # Paste the recorded HUD strip into a 1440p game area at HUD_ROI and analyse it whole.
    game = np.full((1440, 2560, 3), 40, np.uint8)
    strip = hud("planted_hud.jpg")
    x0, y0 = int(0.30 * 2560), 0
    game[y0:y0 + strip.shape[0], x0:x0 + strip.shape[1]] = strip
    a = analyze(game, template)
    assert a.score >= 0.85 and not a.whiteout and a.slot.size > 0
