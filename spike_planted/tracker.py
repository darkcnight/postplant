"""Round state machine: per-frame readings in, spike events out. Pure logic, no I/O."""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from enum import Enum

SPIKE_TIMER_S = 45.0


class Kind(Enum):
    PLANTED = "planted"
    DEFUSED = "defused"
    DETONATED = "detonated"
    WIPED = "wiped"
    ABORTED = "aborted"


@dataclass(frozen=True)
class Reading:
    t: float
    score: float
    digits: int = 0
    whiteout: bool = False


@dataclass(frozen=True)
class Event:
    kind: Kind
    t: float
    reason: str
    plant_t: float | None = None


@dataclass
class TrackerConfig:
    threshold: float = 0.60
    plant_confirm_s: float = 0.5
    end_gap_s: float = 0.4           # icon must be gone at least this long
    end_wait_max_s: float = 2.5      # ...and we decide by here if the timer is ambiguous
    detonation_cutoff_s: float = 43.0
    whiteout_window_s: tuple[float, float] = (44.0, 46.5)
    end_evidence_frames: int = 3     # frames showing a timer (>=3 digits) before we call it over
    defuse_min_frames: int = 3       # frames showing the SS.hh readout (>=4 digits)
    hidden_timeout_s: float = 60.0   # HUD hidden this long after plant -> give up (Aborted)
    cooldown_s: float = 5.0

    @classmethod
    def from_dict(cls, d: dict) -> "TrackerConfig":
        known = {f.name for f in fields(cls)}
        kw = {k: v for k, v in d.items() if k in known}
        if "whiteout_window_s" in kw:
            kw["whiteout_window_s"] = tuple(kw["whiteout_window_s"])
        return cls(**kw)


class State(Enum):
    IDLE = "idle"
    PLANTED = "planted"
    COOLDOWN = "cooldown"


@dataclass
class Tracker:
    cfg: TrackerConfig = field(default_factory=TrackerConfig)
    state: State = State.IDLE
    plant_t: float | None = None
    _run_start: float | None = None
    _dip: list[Reading] = field(default_factory=list)
    _below_since: float | None = None

    # ------------------------------------------------------------------ public
    def update(self, r: Reading) -> list[Event]:
        handler = {
            State.IDLE: self._idle,
            State.PLANTED: self._planted,
            State.COOLDOWN: self._cooldown,
        }[self.state]
        return handler(r)

    def reset(self, t: float) -> list[Event]:
        """Capture lost: abandon any live plant."""
        events = []
        if self.state is State.PLANTED:
            events.append(Event(Kind.ABORTED, t, "Capture lost during a plant", self.plant_t))
        self._clear()
        self.state = State.IDLE
        return events

    # ------------------------------------------------------------------ states
    def _idle(self, r: Reading) -> list[Event]:
        if r.score < self.cfg.threshold:
            self._run_start = None
            return []
        if self._run_start is None:
            self._run_start = r.t
        if r.t - self._run_start < self.cfg.plant_confirm_s:
            return []
        self.state = State.PLANTED
        self.plant_t = self._run_start
        self._dip = []
        return [Event(Kind.PLANTED, self.plant_t, f"Spike icon seen (match {r.score:.2f})",
                      self.plant_t)]

    def _planted(self, r: Reading) -> list[Event]:
        since = r.t - self.plant_t
        lo, hi = self.cfg.whiteout_window_s
        if r.whiteout and lo <= since <= hi:
            return self._finish(Kind.DETONATED, r.t, f"Whiteout {since:.1f}s after plant")
        if r.score >= self.cfg.threshold:
            self._dip = []
            return []
        self._dip.append(r)
        gap = r.t - self._dip[0].t
        if gap < self.cfg.end_gap_s:
            return []
        # Only call the round over once the slot shows a timer again: the defuse readout
        # (SS.hh, 4 digits) or the post-round countdown (M:SS, 3 digits). An empty slot
        # means the HUD is covered (menu, ability effect) and the spike may still be live.
        # Deciding as soon as the timer is clearly readable keeps the lights in sync.
        readouts = sum(1 for x in self._dip if x.digits >= 4)
        countdown = sum(1 for x in self._dip if x.digits == 3)
        clear_defuse = readouts >= self.cfg.defuse_min_frames
        clear_countdown = countdown >= self.cfg.end_evidence_frames and countdown > 2 * readouts
        waited = (gap >= self.cfg.end_wait_max_s
                  and readouts + countdown >= self.cfg.end_evidence_frames)
        if clear_defuse or clear_countdown or waited:
            return self._decide()
        if since >= self.cfg.hidden_timeout_s:
            return self._finish(Kind.ABORTED, r.t,
                                f"HUD hidden {r.t - self._dip[0].t:.0f}s, outcome not seen")
        return []

    def _cooldown(self, r: Reading) -> list[Event]:
        if r.score >= self.cfg.threshold:
            self._below_since = None
            return []
        if self._below_since is None:
            self._below_since = r.t
        if r.t - self._below_since >= self.cfg.cooldown_s:
            self._clear()
            self.state = State.IDLE
        return []

    # ------------------------------------------------------------------ helpers
    def _decide(self) -> list[Event]:
        end = self._dip[0].t
        duration = end - self.plant_t
        readouts = sum(1 for x in self._dip if x.digits >= 4)
        if readouts >= self.cfg.defuse_min_frames:
            left = max(0.0, SPIKE_TIMER_S - duration)
            return self._finish(Kind.DEFUSED, end,
                                f"Timer readout ({readouts} frames), icon {duration:.1f}s, "
                                f"~{left:.1f}s left")
        if duration >= self.cfg.detonation_cutoff_s:
            return self._finish(Kind.DETONATED, end, f"Icon lasted {duration:.1f}s")
        return self._finish(Kind.WIPED, end,
                            f"Round ended {duration:.1f}s after plant, no defuse readout")

    def _finish(self, kind: Kind, t: float, reason: str) -> list[Event]:
        event = Event(kind, t, reason, self.plant_t)
        self.state = State.COOLDOWN
        self._below_since = None
        self._dip = []
        return [event]

    def _clear(self) -> None:
        self.plant_t = None
        self._run_start = None
        self._dip = []
        self._below_since = None
