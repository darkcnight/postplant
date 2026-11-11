"""
app.py — Spike Planted GUI
A compact system-tray + window app. Double-click the AppImage to run.
"""

import asyncio
import os
import re
import sys
import threading
import time
from pathlib import Path

from PyQt6.QtCore import (
    Qt, QThread, pyqtSignal, QTimer, QSize, QPropertyAnimation,
    QEasingCurve, pyqtProperty, QObject
)
from PyQt6.QtGui import (
    QColor, QFont, QIcon, QPainter, QPainterPath, QPixmap,
    QLinearGradient, QRadialGradient, QPen, QBrush, QFontDatabase
)
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QSystemTrayIcon, QMenu, QFrame, QSizePolicy,
    QGraphicsDropShadowEffect
)

import yaml
import httpx

# ── resolve base dir (works both from source and PyInstaller bundle) ──────────
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys._MEIPASS)
    WORK_DIR = Path(sys.executable).parent  # next to the AppImage/binary
else:
    BASE_DIR = Path(__file__).parent
    WORK_DIR = BASE_DIR

CONFIG_PATH = WORK_DIR / "config.yaml"
ENV_PATH = WORK_DIR / ".env"


# ═══════════════════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════════════════

def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())


def expand_env(cfg: dict) -> dict:
    def _ex(v):
        if isinstance(v, str):
            return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), m.group(0)), v)
        if isinstance(v, dict):
            return {k: _ex(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_ex(i) for i in v]
        return v
    return _ex(cfg)


def load_config() -> dict:
    load_dotenv(ENV_PATH)
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f) or {}
    return expand_env(cfg)


def save_lights_to_config(entity_ids: list[str]) -> None:
    if not CONFIG_PATH.exists():
        return
    text = CONFIG_PATH.read_text()
    # Replace the lights: line
    new_lights = entity_ids[0] if len(entity_ids) == 1 else entity_ids
    import yaml as _y
    cfg = _y.safe_load(text) or {}
    cfg.setdefault("home_assistant", {})["lights"] = new_lights
    CONFIG_PATH.write_text(_y.dump(cfg, default_flow_style=False, allow_unicode=True))


# ═══════════════════════════════════════════════════════════════════════════════
#  Async worker thread — runs the event loop + detector + HA effects
# ═══════════════════════════════════════════════════════════════════════════════

class WorkerThread(QThread):
    state_changed = pyqtSignal(str)   # "idle" | "planted" | "defused" | "exploded"
    log_line      = pyqtSignal(str)
    error         = pyqtSignal(str)

    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self._loop.close()

    def stop(self):
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)

    async def _main(self):
        from ha_client import HAClient
        from detector import SpikeDetector, SpikeState, DetectionConfig
        from effects import EffectsController

        ha_cfg = self.cfg.get("home_assistant", {})
        ha = HAClient(ha_cfg["url"], ha_cfg["token"])
        lights = ha_cfg.get("lights", [])
        spike_cfg = self.cfg.get("spike", {})
        effects = EffectsController(ha, lights, spike_cfg)

        det_cfg = self.cfg.get("detection", {})
        det_config = DetectionConfig(
            poll_interval=det_cfg.get("poll_interval", 0.3),
            spike_roi=det_cfg.get("spike_roi", {}),
            spike_color=det_cfg.get("spike_color", {}),
            defuse_confirm_frames=det_cfg.get("defuse_confirm_frames", 3),
            debug=det_cfg.get("debug", False),
        )

        async def on_state(state: SpikeState):
            name = state.name.lower()
            self.state_changed.emit(name)
            self.log_line.emit(f"Spike → {name.upper()}")
            if state.name == "PLANTED":
                await effects.on_planted()
            elif state.name == "DEFUSED":
                await effects.on_defused()
            elif state.name == "EXPLODED":
                await effects.on_exploded()

        self._stop_event = asyncio.Event()
        detector = SpikeDetector(det_config, on_state)

        self.log_line.emit("Detection started — watching Moonlight window…")
        detect_task = asyncio.create_task(detector.start())
        await self._stop_event.wait()
        detector.stop()
        detect_task.cancel()
        self.log_line.emit("Stopped.")


# ═══════════════════════════════════════════════════════════════════════════════
#  Spike status indicator widget
# ═══════════════════════════════════════════════════════════════════════════════

COLORS = {
    "idle":     ("#1a1a2e", "#16213e", "IDLE",     "#6c757d"),
    "planted":  ("#3d0000", "#8b0000", "PLANTED",  "#ff4444"),
    "defused":  ("#003d1a", "#006633", "DEFUSED",  "#44ff88"),
    "exploded": ("#3d1a00", "#8b4500", "EXPLODED", "#ff8c00"),
}


class PulseCircle(QWidget):
    """Animated pulsing circle that shows current spike state."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = "idle"
        self._pulse = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(30)
        self._t = 0.0
        self.setFixedSize(160, 160)

    def set_state(self, state: str):
        self._state = state
        self._t = 0.0

    def _tick(self):
        self._t += 0.03
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        w, h = self.width(), self.height()
        cx, cy, r = w / 2, h / 2, 60

        # Pulse speed by state
        speeds = {"idle": 0.5, "planted": 1.5 + self._t * 0.03, "defused": 1.0, "exploded": 4.0}
        speed = min(speeds.get(self._state, 1.0), 5.0)
        import math
        pulse = (math.sin(self._t * speed) + 1) / 2  # 0 → 1

        colors = {
            "idle":     (QColor("#3a3a5c"), QColor("#5a5a8c")),
            "planted":  (QColor("#8b0000"), QColor("#ff4444")),
            "defused":  (QColor("#006633"), QColor("#44ff88")),
            "exploded": (QColor("#8b4500"), QColor("#ffaa00")),
        }
        c_dark, c_bright = colors.get(self._state, colors["idle"])

        # Outer glow ring
        if self._state != "idle":
            glow_r = r + 15 + pulse * 18
            glow = QRadialGradient(cx, cy, glow_r)
            gc = QColor(c_bright)
            gc.setAlpha(int(60 * pulse))
            glow.setColorAt(0.6, gc)
            glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            p.setBrush(QBrush(glow))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(int(cx - glow_r), int(cy - glow_r),
                          int(glow_r * 2), int(glow_r * 2))

        # Main circle gradient
        grad = QRadialGradient(cx - 15, cy - 15, r * 1.4)
        mixed = QColor(
            int(c_dark.red()   + (c_bright.red()   - c_dark.red())   * pulse),
            int(c_dark.green() + (c_bright.green() - c_dark.green()) * pulse),
            int(c_dark.blue()  + (c_bright.blue()  - c_dark.blue())  * pulse),
        )
        grad.setColorAt(0, mixed.lighter(140))
        grad.setColorAt(1, c_dark.darker(120))
        p.setBrush(QBrush(grad))
        p.setPen(QPen(c_bright, 2))
        p.drawEllipse(int(cx - r), int(cy - r), int(r * 2), int(r * 2))

        # State text
        labels = {"idle": "●", "planted": "!", "defused": "✓", "exploded": "✕"}
        txt = labels.get(self._state, "●")
        p.setPen(QPen(QColor("white")))
        font = QFont("Arial", 32, QFont.Weight.Bold)
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, txt)
        p.end()


# ═══════════════════════════════════════════════════════════════════════════════
#  Main window
# ═══════════════════════════════════════════════════════════════════════════════

STYLESHEET = """
QWidget {
    background-color: #0d0d1a;
    color: #e0e0ff;
    font-family: 'Segoe UI', Arial, sans-serif;
}
QLabel#title {
    font-size: 20px;
    font-weight: bold;
    color: #ffffff;
    letter-spacing: 2px;
}
QLabel#subtitle {
    font-size: 11px;
    color: #6060a0;
    letter-spacing: 1px;
}
QLabel#status_label {
    font-size: 13px;
    font-weight: bold;
    letter-spacing: 3px;
    padding: 6px 18px;
    border-radius: 12px;
}
QPushButton#btn_start {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #ff4444, stop:1 #cc0000);
    color: white;
    border: none;
    border-radius: 10px;
    font-size: 13px;
    font-weight: bold;
    padding: 10px 28px;
    letter-spacing: 1px;
}
QPushButton#btn_start:hover {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 #ff6666, stop:1 #ee1111);
}
QPushButton#btn_start:disabled {
    background: #333355;
    color: #666688;
}
QPushButton#btn_stop {
    background: #1a1a3a;
    color: #8888bb;
    border: 1px solid #333366;
    border-radius: 10px;
    font-size: 13px;
    padding: 10px 28px;
    letter-spacing: 1px;
}
QPushButton#btn_stop:hover {
    background: #222244;
    color: #aaaadd;
    border-color: #555599;
}
QPushButton#btn_stop:disabled {
    color: #333355;
    border-color: #222233;
}
QPushButton#btn_test {
    background: transparent;
    color: #5555aa;
    border: 1px solid #333366;
    border-radius: 8px;
    font-size: 11px;
    padding: 5px 12px;
}
QPushButton#btn_test:hover {
    color: #8888dd;
    border-color: #5555aa;
}
QFrame#log_frame {
    background: #080812;
    border: 1px solid #1a1a3a;
    border-radius: 8px;
}
QLabel#log_text {
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 11px;
    color: #4466aa;
    padding: 8px;
}
QLabel#entity_label {
    font-size: 11px;
    color: #4a4a7a;
    padding: 2px 0;
}
QFrame#divider {
    background: #1a1a3a;
    max-height: 1px;
}
"""


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self._worker: WorkerThread | None = None
        self._log_lines: list[str] = []
        self._state = "idle"

        self.setWindowTitle("Spike Planted")
        self.setFixedSize(420, 580)
        self.setStyleSheet(STYLESHEET)
        self._build_ui()
        self._setup_tray()

        # Update entity display from config
        lights = self.cfg.get("home_assistant", {}).get("lights", "")
        if isinstance(lights, list):
            lights_str = "\n".join(lights)
        else:
            lights_str = str(lights)
        self.entity_label.setText(lights_str)

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(0)

        # Header
        hdr = QHBoxLayout()
        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        t = QLabel("SPIKE PLANTED")
        t.setObjectName("title")
        s = QLabel("VALORANT  ·  HOME ASSISTANT")
        s.setObjectName("subtitle")
        title_col.addWidget(t)
        title_col.addWidget(s)
        hdr.addLayout(title_col)
        hdr.addStretch()
        root.addLayout(hdr)
        root.addSpacing(20)

        # Divider
        div = QFrame(); div.setObjectName("divider"); div.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(div)
        root.addSpacing(20)

        # Pulse circle + status
        circle_row = QHBoxLayout()
        circle_row.addStretch()
        col = QVBoxLayout()
        col.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.pulse_circle = PulseCircle()
        col.addWidget(self.pulse_circle, alignment=Qt.AlignmentFlag.AlignCenter)
        col.addSpacing(12)

        self.status_label = QLabel("IDLE")
        self.status_label.setObjectName("status_label")
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._apply_status_color("idle")
        col.addWidget(self.status_label, alignment=Qt.AlignmentFlag.AlignCenter)

        circle_row.addLayout(col)
        circle_row.addStretch()
        root.addLayout(circle_row)
        root.addSpacing(20)

        # Lights info
        lights_row = QHBoxLayout()
        lights_icon = QLabel("💡")
        lights_icon.setFixedWidth(20)
        self.entity_label = QLabel("…")
        self.entity_label.setObjectName("entity_label")
        self.entity_label.setWordWrap(True)
        lights_row.addWidget(lights_icon)
        lights_row.addWidget(self.entity_label)
        lights_row.addStretch()
        root.addLayout(lights_row)
        root.addSpacing(16)

        # Buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self.btn_start = QPushButton("▶  START")
        self.btn_start.setObjectName("btn_start")
        self.btn_start.clicked.connect(self._start)

        self.btn_stop = QPushButton("■  STOP")
        self.btn_stop.setObjectName("btn_stop")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop)

        btn_row.addWidget(self.btn_start)
        btn_row.addWidget(self.btn_stop)
        root.addLayout(btn_row)
        root.addSpacing(10)

        # Test buttons
        test_row = QHBoxLayout()
        test_row.setSpacing(8)
        for label, effect in [("Test Plant", "planted"), ("Test Defuse", "defused"), ("Test Boom", "exploded")]:
            b = QPushButton(label)
            b.setObjectName("btn_test")
            b.clicked.connect(lambda _, e=effect: self._run_test(e))
            test_row.addWidget(b)
        root.addLayout(test_row)
        root.addSpacing(16)

        # Log frame
        log_frame = QFrame()
        log_frame.setObjectName("log_frame")
        log_frame.setFixedHeight(110)
        log_layout = QVBoxLayout(log_frame)
        log_layout.setContentsMargins(0, 0, 0, 0)
        self.log_label = QLabel("Ready.")
        self.log_label.setObjectName("log_text")
        self.log_label.setAlignment(Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft)
        self.log_label.setWordWrap(True)
        self.log_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        log_layout.addWidget(self.log_label)
        root.addWidget(log_frame)

    def _apply_status_color(self, state: str):
        colors = {
            "idle":     ("color: #6c757d; background: #1a1a2e;"),
            "planted":  ("color: #ff4444; background: #3d0000;"),
            "defused":  ("color: #44ff88; background: #003d1a;"),
            "exploded": ("color: #ffaa00; background: #3d1a00;"),
        }
        style = colors.get(state, colors["idle"])
        self.status_label.setStyleSheet(f"QLabel#status_label {{ {style} border-radius: 12px; padding: 6px 18px; font-size: 13px; font-weight: bold; letter-spacing: 3px; }}")

    # ── Tray ─────────────────────────────────────────────────────────────────

    def _setup_tray(self):
        # Simple coloured circle icon
        px = QPixmap(32, 32)
        px.fill(Qt.GlobalColor.transparent)
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QBrush(QColor("#ff4444")))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(4, 4, 24, 24)
        p.end()

        self.tray = QSystemTrayIcon(QIcon(px), self)
        menu = QMenu()
        menu.addAction("Show", self.show)
        menu.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self.tray.show()

    # ── Worker control ────────────────────────────────────────────────────────

    def _start(self):
        if self._worker and self._worker.isRunning():
            return
        self.cfg = load_config()
        self._worker = WorkerThread(self.cfg)
        self._worker.state_changed.connect(self._on_state)
        self._worker.log_line.connect(self._append_log)
        self._worker.error.connect(lambda e: self._append_log(f"ERROR: {e}"))
        self._worker.finished.connect(self._on_worker_done)
        self._worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._append_log("Starting detection…")

    def _stop(self):
        if self._worker:
            self._worker.stop()
        self.btn_stop.setEnabled(False)

    def _on_worker_done(self):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._on_state("idle")

    def _run_test(self, effect: str):
        self._append_log(f"Testing: {effect}…")

        def _do():
            import asyncio as _a
            from ha_client import HAClient
            from effects import EffectsController
            cfg = load_config()
            ha_cfg = cfg.get("home_assistant", {})
            ha = HAClient(ha_cfg["url"], ha_cfg["token"])
            lights = ha_cfg.get("lights", [])
            effects = EffectsController(ha, lights, cfg.get("spike", {}))

            loop = _a.new_event_loop()
            loop.run_until_complete(ha.save_state(lights))

            if effect == "planted":
                loop.run_until_complete(effects.on_planted())
                loop.run_until_complete(_a.sleep(8))
                loop.run_until_complete(effects.on_defused())
            elif effect == "defused":
                loop.run_until_complete(ha.turn_on(lights, rgb=(255, 0, 0), brightness=200))
                loop.run_until_complete(_a.sleep(1))
                loop.run_until_complete(effects.on_defused())
            elif effect == "exploded":
                loop.run_until_complete(ha.turn_on(lights, rgb=(255, 0, 0), brightness=200))
                loop.run_until_complete(_a.sleep(1))
                loop.run_until_complete(effects.on_exploded())
            loop.close()

        t = threading.Thread(target=_do, daemon=True)
        t.start()

    # ── State / log updates ───────────────────────────────────────────────────

    def _on_state(self, state: str):
        self._state = state
        self.pulse_circle.set_state(state)
        self.status_label.setText(state.upper())
        self._apply_status_color(state)

        # Tray tooltip
        tips = {"idle": "Watching…", "planted": "🔴 SPIKE PLANTED", "defused": "✅ Defused", "exploded": "💥 EXPLODED"}
        self.tray.setToolTip(tips.get(state, state))

    def _append_log(self, line: str):
        ts = time.strftime("%H:%M:%S")
        self._log_lines.append(f"[{ts}] {line}")
        self._log_lines = self._log_lines[-6:]  # keep last 6 lines
        self.log_label.setText("\n".join(self._log_lines))

    # ── Window events ─────────────────────────────────────────────────────────

    def closeEvent(self, event):
        # Minimise to tray instead of closing
        event.ignore()
        self.hide()
        self.tray.showMessage("Spike Planted", "Still running in the tray.", QSystemTrayIcon.MessageIcon.Information, 2000)


# ═══════════════════════════════════════════════════════════════════════════════
#  Entry
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Spike Planted")
    app.setQuitOnLastWindowClosed(False)  # keep alive in tray
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

