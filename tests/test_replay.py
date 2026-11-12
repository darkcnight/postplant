"""Replay labelled recordings through vision + tracker. Skipped when no recordings exist."""
from pathlib import Path

import pytest
import yaml

from postplant.replay import replay
from postplant.tracker import Kind

ROOT = Path(__file__).resolve().parents[1]
LABELS = sorted(p for p in (ROOT / "recordings").glob("*/labels.yaml") if (p.parent / "hud").is_dir())


@pytest.mark.skipif(not LABELS, reason="no labelled recordings on this machine")
@pytest.mark.parametrize("labels_path", LABELS, ids=lambda p: p.parent.name)
def test_replay_matches_labels(labels_path):
    labels = yaml.safe_load(labels_path.read_text())
    events = replay(labels_path.parent, until=labels["until"])
    plants = [e for e in events if e.kind is Kind.PLANTED]
    outcomes = [e for e in events if e.kind is not Kind.PLANTED]
    expected = labels["plants"]

    assert len(plants) == len(expected), [(e.t, e.kind.value) for e in events]
    assert len(outcomes) == len(expected)
    for plant, outcome, exp in zip(plants, outcomes, expected):
        assert abs(plant.t - exp["t"]) <= 1.0, (plant.t, exp)
        assert outcome.kind.value == exp["outcome"], (exp, outcome.reason)
        assert outcome.plant_t == plant.t
