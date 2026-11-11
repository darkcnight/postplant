"""Build spike_planted/assets/spike_icon_mask.png from recorded HUD frames.

Averages the spike-red mask of the icon slot over frames where the spike is known to be
planted, keeps pixels lit in most of them, and trims to the shape's bounding box.

    python tools/build_template.py recordings/session1/hud 1794402338 1794402347
"""
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spike_planted.vision import (HUD_ROI, SLOT_REF, SLOT_ROI, TEMPLATE_PATH, crop,  # noqa: E402
                                  spike_red_mask, sub_roi)


def main(hud_dir: str, t0: float, t1: float) -> None:
    roi = sub_roi(SLOT_ROI, HUD_ROI)
    masks = []
    for f in sorted(Path(hud_dir).glob("*.jpg")):
        if t0 <= float(f.stem) <= t1:
            img = cv2.imread(str(f))
            slot = cv2.resize(crop(img, roi), SLOT_REF, interpolation=cv2.INTER_AREA)
            masks.append(spike_red_mask(slot) / 255.0)
    if not masks:
        sys.exit("no frames in that time range")
    tpl = (np.mean(masks, axis=0) > 0.5).astype(np.uint8) * 255
    ys, xs = np.nonzero(tpl)
    tpl = tpl[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    TEMPLATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(TEMPLATE_PATH), tpl)
    print(f"{len(masks)} frames -> {TEMPLATE_PATH} {tpl.shape[1]}x{tpl.shape[0]}")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]), float(sys.argv[3]))
