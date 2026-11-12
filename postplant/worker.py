"""Threads and glue: detection thread, asyncio runner for HA/effects, and the Controller
the UI talks to. Qt signals carry everything back to the GUI thread."""
from __future__ import annotations

import asyncio
import collections
import concurrent.futures
import logging
import threading
import time
from pathlib import Path

import cv2
import numpy as np
from PyQt6.QtCore import QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage

from . import launcher
from .config import load_config, save_config, save_token
from .effects import Effects
from .ha_client import HAClient
from .tracker import Kind, Reading, Tracker, TrackerConfig

log = logging.getLogger("spike.worker")


class AsyncRunner:
    """A long-lived asyncio loop on its own thread (HA calls and effects)."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, name="ha-loop", daemon=True)
        self._thread.start()

    def submit(self, coro) -> concurrent.futures.Future:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=2)


def to_qimage(bgr: np.ndarray) -> QImage:
    rgb = np.ascontiguousarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
    h, w = rgb.shape[:2]
    return QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()


class DetectionThread(QThread):
    reading = pyqtSignal(float, QImage)        # score, slot preview
    state = pyqtSignal(str, float)             # tracker state, plant time (0 if none)
    event = pyqtSignal(str, str)               # kind, reason
    moonlight = pyqtSignal(str, str)           # "ok" | "waiting" | "error", message

    def __init__(self, root: Path, cfg: dict, force_picker: bool = False):
        super().__init__()
        self.root, self.cfg, self.force_picker = root, cfg, force_picker
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _wait(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def run(self) -> None:
        # Imported here so the UI can start (and tests can run) without GStreamer.
        from .capture import CaptureCancelled, CaptureError, PortalCapture, moonlight_running
        from .vision import HUD_ROI, analyze, crop, game_box, load_template

        template = load_template()
        tracker = Tracker(TrackerConfig.from_dict(self.cfg["detection"]))
        period = 1.0 / max(1, min(15, int(self.cfg["capture"]["analysis_fps"])))
        save_debug = bool(self.cfg["debug"]["save_round_frames"])
        history: collections.deque = collections.deque(maxlen=int(12 / period))
        cap = None
        box, box_t, last_frame = None, 0.0, 0.0

        while not self._stop.is_set():
            if cap is None:
                if not moonlight_running():
                    self.moonlight.emit("waiting", "Moonlight not running")
                    self._wait(5)
                    continue
                cap = PortalCapture(self.root / ".state" / "portal_token")
                try:
                    if not cap.has_token() or self.force_picker:
                        self.moonlight.emit("waiting", "Pick the Moonlight window in the dialog")
                    cap.open(force_picker=self.force_picker)
                    self.force_picker = False
                    last_frame = time.monotonic()
                    self.moonlight.emit("ok", "Streaming")
                except CaptureCancelled:
                    self.moonlight.emit("error", "Window not picked. Use Pick window again")
                    cap = None
                    return
                except CaptureError as e:
                    self.moonlight.emit("error", f"Capture failed: {e}")
                    cap = None
                    self._wait(5)
                    continue

            tick = time.monotonic()
            frame = cap.read(0.5)
            now = time.monotonic()
            if frame is None:
                if now - last_frame > 3:
                    self._emit_events(tracker.reset(now))
                    cap.close()
                    cap = None
                    self.moonlight.emit("waiting", "Moonlight: not streaming")
                    self._wait(5)
                continue
            last_frame = now
            if box is None or now - box_t > 5:
                box, box_t = game_box(frame), now
            x0, y0, x1, y1 = box
            game = frame[y0:y1, x0:x1]
            if game.size == 0:
                box = None
                continue
            a = analyze(game, template)
            events = tracker.update(Reading(now, a.score, a.digits, a.whiteout))
            self.reading.emit(a.score, to_qimage(a.slot))
            self.state.emit(tracker.state.value, tracker.plant_t or 0.0)
            if save_debug:
                history.append((time.time(), crop(game, HUD_ROI).copy()))
            self._emit_events(events)
            if save_debug and any(e.kind is not Kind.PLANTED for e in events):
                self._save_history(history, events[-1].kind.value)
            self._wait(max(0.0, period - (time.monotonic() - tick)))

        if cap is not None:
            cap.close()
        self.moonlight.emit("waiting", "Stopped")

    def _emit_events(self, events) -> None:
        for e in events:
            self.event.emit(e.kind.value, e.reason)

    def _save_history(self, history, kind: str) -> None:
        out = self.root / "debug" / f"{time.strftime('%Y%m%d-%H%M%S')}_{kind}" / "hud"
        out.mkdir(parents=True, exist_ok=True)
        for t, img in list(history):
            cv2.imwrite(str(out / f"{t:.2f}.jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 90])
        log.info("Saved %d debug frames to %s", len(history), out.parent)


class CS2Thread(QThread):
    """Listens for CS2 Game State Integration POSTs; same signals as DetectionThread."""

    reading = pyqtSignal(float, QImage)
    state = pyqtSignal(str, float)
    event = pyqtSignal(str, str)
    moonlight = pyqtSignal(str, str)           # source status: "ok" | "waiting" | "error"

    STALE_S = 30.0                             # CS2 sends a heartbeat every 10 s

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        from .cs2 import GSIServer, GSITracker

        c = self.cfg["cs2"]
        tracker = GSITracker()
        last = {"t": 0.0}

        def on_payload(payload, t):
            if not last["t"]:
                self.moonlight.emit("ok", "CS2 connected")
            last["t"] = t
            for e in tracker.update(payload, t):
                self.event.emit(e.kind.value, e.reason)
            self.state.emit(tracker.state, tracker.plant_t or 0.0)

        try:
            server = GSIServer(c["listen"], int(c["port"]), c["token"], on_payload)
        except OSError as e:
            self.moonlight.emit("error", f"Can't listen on {c['listen']}:{c['port']}: {e}")
            return
        where = f"{c['listen']}:{c['port']}"
        self.moonlight.emit("waiting", f"Listening on {where}, waiting for CS2")
        try:
            while not self._stop.is_set():
                server.handle_request()
                if last["t"] and time.monotonic() - last["t"] > self.STALE_S:
                    last["t"] = 0.0
                    for e in tracker.reset(time.monotonic()):
                        self.event.emit(e.kind.value, e.reason)
                    self.state.emit(tracker.state, 0.0)
                    self.moonlight.emit("waiting", f"CS2 went quiet. Listening on {where}")
        finally:
            server.server_close()
        for e in tracker.reset(time.monotonic()):
            self.event.emit(e.kind.value, e.reason)
        self.moonlight.emit("waiting", "Stopped")


class Controller(QObject):
    """Owns config, the HA client, effects and the detection thread."""

    reading = pyqtSignal(float, QImage)
    state = pyqtSignal(str, float)
    event = pyqtSignal(str, str)
    moonlight = pyqtSignal(str, str)
    ha_health = pyqtSignal(bool)
    running_changed = pyqtSignal(bool)
    log_line = pyqtSignal(str)
    lights_loaded = pyqtSignal(list)          # [Light]
    connection_checked = pyqtSignal(bool, str)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.cfg = load_config(root)
        self.runner = AsyncRunner()
        self.ha: HAClient | None = None
        self.effects: Effects | None = None
        self.known_lights: list = []
        self.known_scripts: list = []
        self.url_error = ""
        self._thread: DetectionThread | CS2Thread | None = None
        if not self.cfg["cs2"]["token"]:
            from .cs2 import new_token
            self.cfg["cs2"]["token"] = new_token()
            self.save()
        self._rebuild_ha()

    @property
    def game(self) -> str:
        return "cs2" if self.cfg.get("game") == "cs2" else "valorant"

    @property
    def source_name(self) -> str:
        return "CS2" if self.game == "cs2" else "Moonlight"

    def set_game(self, game: str) -> None:
        if game == self.game:
            return
        self.cfg["game"] = game
        self.save()
        self.log_line.emit(f"Game: {'CS2' if game == 'cs2' else 'Valorant'}")
        self.restart()

    # ------------------------------------------------------------------ config
    def save(self) -> None:
        save_config(self.root, self.cfg)

    def set_ha(self, url: str, token: str) -> None:
        url, token = url.strip(), token.strip()
        changed_token = token != self.cfg["home_assistant"]["token"]
        self.cfg["home_assistant"]["url"] = url
        self.cfg["home_assistant"]["token"] = token
        self.save()
        if changed_token:
            save_token(self.root, token)
        self._rebuild_ha()

    def set_open_on_login(self, on: bool) -> None:
        self.cfg["capture"]["open_on_login"] = on
        self.save()
        launcher.set_autostart(self.root, on)

    def _rebuild_ha(self) -> None:
        old = self.ha
        ha_cfg = self.cfg["home_assistant"]
        try:
            self.ha = HAClient(ha_cfg["url"], ha_cfg["token"], on_health=self.ha_health.emit)
            self.effects = Effects(self.ha, lambda: self.cfg, on_log=self.log_line.emit)
            self.url_error = ""
        except ValueError as e:
            self.ha = self.effects = None
            self.url_error = str(e)
            self.log_line.emit(f"Home Assistant URL rejected: {e}")
        if old is not None:
            self.runner.submit(old.aclose())

    # ------------------------------------------------------------------ HA actions
    def _done(self, fut, on_ok):
        def cb(f):
            try:
                on_ok(f.result())
            except Exception as e:  # noqa: BLE001 - report anything to the log pane
                log.exception("Background task failed")
                self.log_line.emit(f"Error: {e}")
        fut.add_done_callback(cb)

    def check_connection(self) -> None:
        if self.ha is None:
            self.connection_checked.emit(False, self.url_error or "Not configured")
            return

        async def go():
            ok, msg = await self.ha.check()
            lights = await self.ha.list_lights() if ok else []
            scripts = await self.ha.list_scripts() if ok else []
            return ok, msg, lights, scripts

        def done(res):
            ok, msg, lights, scripts = res
            if ok:
                self.known_lights = lights
                self.known_scripts = scripts
                msg = f"{msg} · {len(lights)} lights"
                self.lights_loaded.emit(lights)
            self.connection_checked.emit(ok, msg)

        self._done(self.runner.submit(go()), done)

    def test_effect(self, name: str) -> None:
        if self.effects is None:
            self.log_line.emit("Set up Home Assistant on the Settings tab first")
            return
        self.log_line.emit(f"Testing {name} effect…")
        self._done(self.runner.submit(self.effects.test(name)),
                   lambda _: self.log_line.emit(f"Test {name} finished, lights restored"))

    # ------------------------------------------------------------------ detection
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.isRunning()

    def start(self, force_picker: bool = False) -> None:
        if self.running:
            return
        if self.game == "cs2":
            t = CS2Thread(self.cfg)
        else:
            t = DetectionThread(self.root, self.cfg, force_picker=force_picker)
        t.reading.connect(self.reading.emit)
        t.state.connect(self.state.emit)
        t.moonlight.connect(self.moonlight.emit)
        t.event.connect(self._on_event)
        t.finished.connect(self._on_finished)
        self._thread = t
        t.start()
        self.running_changed.emit(True)
        self.log_line.emit(f"Detection started ({self.source_name})")

    def stop(self, wait_for_lights: bool = False) -> None:
        if self._thread is not None:
            self._thread.stop()
            self._thread.wait(3000)
        if self.effects is not None:
            fut = self.runner.submit(self.effects.stop_and_restore())
            if wait_for_lights:
                try:
                    fut.result(timeout=12)
                except Exception:  # noqa: BLE001 - timeout or HA down: give up on exit
                    log.warning("Lights not confirmed restored before exit")

    def restore_now(self) -> None:
        if self.effects is None:
            self.log_line.emit("Set up Home Assistant on the Settings tab first")
            return
        self.log_line.emit("Restoring lights…")
        self._done(self.runner.submit(self.effects.restore_now()),
                   lambda ok: self.log_line.emit("Lights restored" if ok else
                                                 "Restore not confirmed, see log above"))

    def restart(self, force_picker: bool = False) -> None:
        was = self.running or force_picker
        self.stop()
        if was:
            self.start(force_picker=force_picker)

    def _on_event(self, kind: str, reason: str) -> None:
        self.event.emit(kind, reason)
        self.log_line.emit(f"{kind.upper()}: {reason}")
        if self.effects is not None:
            self.runner.submit(self.effects.handle(Kind(kind)))

    def _on_finished(self) -> None:
        self._thread = None
        self.running_changed.emit(False)
        self.state.emit("idle", 0.0)
        self.log_line.emit("Detection stopped")

    def shutdown(self) -> None:
        self.stop(wait_for_lights=True)
        if self.ha is not None:
            try:
                self.runner.submit(self.ha.aclose()).result(timeout=2)
            except Exception:  # noqa: BLE001
                pass
        self.runner.stop()
