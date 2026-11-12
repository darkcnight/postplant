"""Run vision + tracker over a recorded HUD folder (tools/record.py or debug saves).

    python -m postplant.replay recordings/session1
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2

from .config import DEFAULTS
from .tracker import Event, Reading, Tracker, TrackerConfig
from .vision import HUD_ROI, SLOT_ROI, TIMER_ROI, crop, digit_count, icon_score, load_template, sub_roi

SLOT_IN_HUD = sub_roi(SLOT_ROI, HUD_ROI)
TIMER_IN_HUD = sub_roi(TIMER_ROI, HUD_ROI)


def replay(session: Path, until: float | None = None, detection: dict | None = None) -> list[Event]:
    template = load_template()
    tracker = Tracker(TrackerConfig.from_dict(detection or DEFAULTS["detection"]))
    events: list[Event] = []
    frames = sorted((session / "hud").glob("*.jpg"), key=lambda p: float(p.stem))
    for f in frames:
        t = float(f.stem)
        if until is not None and t > until:
            break
        img = cv2.imread(str(f))
        if img is None:
            continue
        events += tracker.update(Reading(t, icon_score(crop(img, SLOT_IN_HUD), template),
                                         digit_count(crop(img, TIMER_IN_HUD))))
    return events


def main() -> None:
    session = Path(sys.argv[1])
    for e in replay(session):
        print(f"{e.t:.2f}  {e.kind.value:<9}  {e.reason}")


if __name__ == "__main__":
    main()
