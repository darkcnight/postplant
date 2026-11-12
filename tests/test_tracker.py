from postplant.tracker import Kind, Reading, State, Tracker, TrackerConfig

FPS = 5
DT = 1 / FPS


def feed(tracker, start, seconds, score, digits=0, whiteout=False):
    """Feed constant readings for `seconds` from `start`; return (events, next_t)."""
    events, n = [], round(seconds * FPS)
    for i in range(n):
        events += tracker.update(Reading(start + i * DT, score, digits, whiteout))
    return events, start + n * DT


def plant_round(tracker, t, plant_s, after_digits):
    """Idle 3 s, icon for plant_s, then 6 s of post-round slot with `after_digits`."""
    ev1, t = feed(tracker, t, 3, 0.0, 3)
    ev2, t = feed(tracker, t, plant_s, 0.9)
    ev3, t = feed(tracker, t, 6, 0.0, after_digits)
    return ev1 + ev2 + ev3, t


def kinds(events):
    return [e.kind for e in events]


def test_short_blip_does_not_plant():
    tr = Tracker()
    events, _ = feed(tr, 0, 0.4, 0.9)
    assert events == [] and tr.state is State.IDLE


def test_plant_is_backdated_to_first_icon_frame():
    tr = Tracker()
    _, t = feed(tr, 0, 2, 0.0)
    events, _ = feed(tr, t, 1, 0.9)
    assert kinds(events) == [Kind.PLANTED]
    assert abs(events[0].t - 2.0) < 1e-9


def test_detonation_by_duration():
    events, _ = plant_round(Tracker(), 0, 45, after_digits=3)
    assert kinds(events) == [Kind.PLANTED, Kind.DETONATED]


def test_late_defuse_is_defused_not_detonated():
    # 41.8 s defuse seen in the recording: timer readout wins over duration.
    events, _ = plant_round(Tracker(), 0, 41.8, after_digits=4)
    assert kinds(events) == [Kind.PLANTED, Kind.DEFUSED]
    assert "left" in events[1].reason


def test_early_end_without_readout_is_wipe():
    events, _ = plant_round(Tracker(), 0, 16, after_digits=3)
    assert kinds(events) == [Kind.PLANTED, Kind.WIPED]


def test_readout_on_bright_background_still_counts_if_most_frames_read():
    tr = Tracker()
    _, t = feed(tr, 0, 3, 0.0)
    feed(tr, t, 30, 0.9)
    t += 30
    pattern = [4, 1, 4, 1, 4, 4, 1, 4, 4, 4, 4, 4, 4, 4, 4]
    events = []
    for i, d in enumerate(pattern):
        events += tr.update(Reading(t + i * DT, 0.0, d))
    assert kinds(events) == [Kind.DEFUSED]


def test_dip_shorter_than_end_gap_is_ignored():
    tr = Tracker()
    _, t = feed(tr, 0, 1, 0.9)
    ev, t = feed(tr, t, 2.0, 0.1)    # ability effect hides the icon briefly
    ev2, t = feed(tr, t, 10, 0.9)
    assert ev == [] and ev2 == [] and tr.state is State.PLANTED


def test_end_time_is_first_frame_of_dip():
    tr = Tracker()
    _, t = feed(tr, 0, 30, 0.9)
    events, _ = feed(tr, t, 5, 0.0, 3)
    assert kinds(events) == [Kind.WIPED]
    assert abs(events[0].t - t) < 1e-9


def test_whiteout_in_window_fires_detonation_immediately():
    tr = Tracker()
    _, t = feed(tr, 0, 44.6, 0.9)
    events = tr.update(Reading(t, 0.9, 0, whiteout=True))
    assert kinds(events) == [Kind.DETONATED] and "Whiteout" in events[0].reason


def test_whiteout_outside_window_is_ignored():
    tr = Tracker()
    _, t = feed(tr, 0, 14, 0.9)
    assert tr.update(Reading(t, 0.9, 0, whiteout=True)) == []
    assert tr.state is State.PLANTED


def test_no_retrigger_during_cooldown_then_next_round_works():
    tr = Tracker()
    ev1, t = feed(tr, 0, 3, 0.0, 3)
    ev2, t = feed(tr, t, 30, 0.9)
    ev3, t = feed(tr, t, 2, 0.0, 3)            # round over, decided within ~0.6 s
    events = ev1 + ev2 + ev3
    assert kinds(events) == [Kind.PLANTED, Kind.WIPED]
    flicker, t = feed(tr, t, 1, 0.9)           # icon flickers back during cooldown
    assert flicker == []
    _, t = feed(tr, t, 6, 0.0)                 # cooldown elapses
    assert tr.state is State.IDLE
    events, _ = plant_round(tr, t, 45, after_digits=3)
    assert kinds(events) == [Kind.PLANTED, Kind.DETONATED]


def test_menu_covering_hud_does_not_end_plant():
    # Settings menu over the HUD: icon gone, slot shows no timer digits at all.
    tr = Tracker()
    _, t = feed(tr, 0, 10, 0.9)
    hidden, t = feed(tr, t, 5, 0.0, digits=0)
    back, t = feed(tr, t, 30.2, 0.9)          # 10 + 5 + 30.2 = 45.2 s planted
    end, t = feed(tr, t, 4, 0.0, digits=3)
    assert hidden == [] and back == []
    assert kinds(end) == [Kind.DETONATED]


def test_round_ends_while_hud_hidden_is_decided_when_timer_reappears():
    tr = Tracker()
    _, t = feed(tr, 0, 20, 0.9)
    hidden, t = feed(tr, t, 8, 0.0, digits=0)
    end, t = feed(tr, t, 3, 0.0, digits=4)   # menu closed: defuse readout visible
    assert hidden == [] and kinds(end) == [Kind.DEFUSED]


def test_hud_hidden_too_long_aborts():
    tr = Tracker()
    _, t = feed(tr, 0, 10, 0.9)
    events, t = feed(tr, t, 55, 0.0, digits=0)
    assert kinds(events) == [Kind.ABORTED] and "hidden" in events[0].reason
    assert tr.state is State.COOLDOWN


def test_reset_while_planted_aborts():
    tr = Tracker()
    feed(tr, 0, 5, 0.9)
    events = tr.reset(5.0)
    assert kinds(events) == [Kind.ABORTED] and tr.state is State.IDLE
    assert tr.reset(6.0) == []


def test_config_from_dict_ignores_unknown_keys():
    cfg = TrackerConfig.from_dict({"threshold": 0.7, "whiteout_window_s": [40, 50],
                                   "spike_roi": {"x": 1}})
    assert cfg.threshold == 0.7 and cfg.whiteout_window_s == (40, 50)


def test_outcome_is_decided_quickly_once_timer_is_readable():
    tr = Tracker()
    _, t = feed(tr, 0, 45, 0.9)
    events, _ = feed(tr, t, 3, 0.0, 3)
    assert kinds(events) == [Kind.DETONATED]
    decided_at = None
    tr2 = Tracker()
    _, t = feed(tr2, 0, 45, 0.9)
    for i in range(15):
        ev = tr2.update(Reading(t + i * DT, 0.0, 3))
        if ev:
            decided_at = i * DT
            break
    assert decided_at is not None and decided_at <= 0.65


def test_single_stray_readout_does_not_turn_detonation_into_defuse():
    tr = Tracker()
    _, t = feed(tr, 0, 45, 0.9)
    events = []
    for i, d in enumerate([4, 3, 3, 3, 3, 3]):
        events += tr.update(Reading(t + i * DT, 0.0, d))
    assert kinds(events) == [Kind.DETONATED]
