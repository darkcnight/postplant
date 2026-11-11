"""Main window: tabs plus an always-visible status strip."""
from __future__ import annotations

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QMainWindow, QPushButton, QTabWidget,
                             QVBoxLayout, QWidget)

from . import theme
from .effects_tab import EffectsTab
from .live_tab import LiveTab
from .settings_tab import SettingsTab


def dot(color: str) -> str:
    return f"<span style='color:{color}; font-size:14px'>●</span>"


class MainWindow(QMainWindow):
    def __init__(self, controller, icon: QIcon | None = None):
        super().__init__()
        self.c = controller
        self.setWindowTitle("Spike Planted")
        if icon:
            self.setWindowIcon(icon)
        self.resize(820, 560)
        self.setStyleSheet(theme.STYLESHEET)

        central = QWidget()
        v = QVBoxLayout(central)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.tabs = QTabWidget()
        self.live = LiveTab(controller)
        self.tabs.addTab(self.live, "Live")
        self.tabs.addTab(EffectsTab(controller), "Effects")
        self.tabs.addTab(SettingsTab(controller), "Settings")
        v.addWidget(self.tabs, 1)
        v.addWidget(self._strip())
        self.setCentralWidget(central)

        self._state, self._plant_t, self._score = "idle", 0.0, 0.0
        self._ha_ok: bool | None = None
        self._ml = ("waiting", "Not started")
        controller.state.connect(self._on_state)
        controller.reading.connect(lambda s, _img: self._set_score(s))
        controller.ha_health.connect(self._on_ha)
        controller.moonlight.connect(self._on_moonlight)
        controller.running_changed.connect(self._on_running)
        controller.event.connect(self._on_event)
        self._flash_until = 0.0
        self._flash_kind = ""
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._refresh)
        self._timer.start(250)
        self._refresh()

    def _strip(self):
        strip = QFrame()
        strip.setObjectName("strip")
        h = QHBoxLayout(strip)
        h.setContentsMargins(12, 8, 12, 8)
        self.state_label = QLabel()
        self.state_label.setStyleSheet("font-weight: 700;")
        self.score_label = QLabel()
        self.score_label.setStyleSheet(f"color: {theme.GREY}; font-size: 12px;")
        self.ha_label = QLabel()
        self.ml_label = QLabel()
        self.btn = QPushButton("▶  Start")
        self.btn.setObjectName("primary")
        self.btn.clicked.connect(lambda: self.c.stop() if self.c.running else self.c.start())
        h.addWidget(self.state_label)
        h.addWidget(self.score_label)
        h.addStretch()
        h.addWidget(self.ha_label)
        h.addSpacing(8)
        h.addWidget(self.ml_label)
        h.addSpacing(8)
        h.addWidget(self.btn)
        return strip

    # ------------------------------------------------------------------ slots
    def _on_state(self, state: str, plant_t: float):
        self._state, self._plant_t = state, plant_t

    def _set_score(self, s: float):
        self._score = s

    def _on_ha(self, ok: bool):
        self._ha_ok = ok

    def _on_moonlight(self, kind: str, msg: str):
        self._ml = (kind, msg)

    def _on_running(self, running: bool):
        self.btn.setText("■  Stop" if running else "▶  Start")
        if not running:
            self._score = 0.0

    def _on_event(self, kind: str, _reason: str):
        if kind != "planted":
            self._flash_kind, self._flash_until = kind, time.monotonic() + 4

    def _refresh(self):
        now = time.monotonic()
        if now < self._flash_until:
            text, color = self._flash_kind.upper(), theme.STATE_COLORS.get(self._flash_kind, theme.TEXT)
        elif self._state == "planted" and self._plant_t:
            text, color = f"PLANTED {now - self._plant_t:.0f}s", theme.RED
        elif not self.c.running:
            text, color = "STOPPED", theme.GREY
        else:
            text, color = "WATCHING", theme.MUTED
        self.state_label.setText(f"{dot(color)} <span style='color:{color}'>{text}</span>")
        self.score_label.setText(f"match {self._score:.2f}" if self.c.running else "")
        ha_color = {None: theme.GREY, True: theme.GREEN, False: theme.AMBER}[self._ha_ok]
        self.ha_label.setText(f"{dot(ha_color)} HA")
        ml_color = {"ok": theme.GREEN, "waiting": theme.GREY, "error": theme.RED}[self._ml[0]]
        self.ml_label.setText(f"{dot(ml_color)} Moonlight")
        self.ml_label.setToolTip(self._ml[1])

    def closeEvent(self, event):
        self.hide()                    # disappear at once; restoring lights can take a few s
        QApplication.processEvents()
        self.c.shutdown()
        event.accept()
