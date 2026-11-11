"""Offscreen smoke test: the window builds, tabs work, signals update the strip."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from spike_planted.ha_client import Light  # noqa: E402
from spike_planted.ui.main_window import MainWindow  # noqa: E402
from spike_planted.worker import Controller, to_qimage  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_window_builds_and_reacts(app, tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    c = Controller(tmp_path)
    win = MainWindow(c)
    try:
        win.show()
        for i in range(win.tabs.count()):
            win.tabs.setCurrentIndex(i)
            app.processEvents()

        c.reading.emit(0.91, to_qimage(np.zeros((136, 179, 3), np.uint8)))
        c.state.emit("planted", 1.0)
        app.processEvents()
        win._refresh()
        assert "PLANTED" in win.state_label.text()

        c.event.emit("defused", "test")
        win._refresh()
        assert "DEFUSED" in win.state_label.text()

        c.lights_loaded.emit([Light("light.room", "Room", "on")])
        app.processEvents()

        effects_tab = win.tabs.widget(1)
        effects_tab.list.setCurrentRow(2)
        c.cfg["effects"]["detonated"]["strobe_count"] = 9
        effects_tab.reset()
        assert c.cfg["effects"]["detonated"]["strobe_count"] == 5
        assert (tmp_path / "config.yaml").exists()

        c.test_effect("planted")   # no HA token: logged, nothing raised
    finally:
        win.close()


def test_controller_relays_signals_from_detection_thread(app, tmp_path, monkeypatch):
    """Signals emitted on the worker thread must reach controller listeners (queued)."""
    import time

    from spike_planted import worker

    def fake_run(self):
        self.moonlight.emit("ok", "Streaming")
        self.reading.emit(0.8, to_qimage(np.zeros((4, 4, 3), np.uint8)))
        self.state.emit("planted", 1.0)
        self.event.emit("planted", "fake")

    monkeypatch.setattr(worker.DetectionThread, "run", fake_run)
    monkeypatch.delenv("HA_TOKEN", raising=False)
    c = Controller(tmp_path)
    got = []
    c.moonlight.connect(lambda k, m: got.append(("moonlight", k)))
    c.reading.connect(lambda s, _i: got.append(("reading", s)))
    c.state.connect(lambda s, _p: got.append(("state", s)))
    c.event.connect(lambda k, _r: got.append(("event", k)))
    c.start()
    deadline = time.time() + 5
    while time.time() < deadline and c.running or len(got) < 4 and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    c.shutdown()
    assert ("moonlight", "ok") in got and ("reading", 0.8) in got
    assert ("state", "planted") in got and ("event", "planted") in got
