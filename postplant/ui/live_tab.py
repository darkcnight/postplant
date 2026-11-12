"""Live tab: what the detector sees, plus the event log."""
from __future__ import annotations

import time

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QVBoxLayout, QWidget

from . import theme


class ScoreMeter(QWidget):
    def __init__(self):
        super().__init__()
        self.score, self.threshold = 0.0, 0.6
        self.setFixedHeight(14)

    def set(self, score: float, threshold: float) -> None:
        self.score, self.threshold = score, threshold
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(0, 3, self.width(), 8)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(theme.LINE))
        p.drawRoundedRect(r, 4, 4)
        fill = QRectF(r.x(), r.y(), r.width() * max(0.0, min(1.0, self.score)), r.height())
        p.setBrush(QColor(theme.RED if self.score >= self.threshold else theme.GREEN))
        p.drawRoundedRect(fill, 4, 4)
        x = r.width() * self.threshold
        p.setBrush(QColor("white"))
        p.drawRect(QRectF(x - 1, 0, 2, 14))


def panel() -> QFrame:
    f = QFrame()
    f.setObjectName("panel")
    return f


def caption(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setObjectName("caption")
    return lbl


class LiveTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 14, 14, 14)
        row.setSpacing(12)

        left = panel()
        lv = QVBoxLayout(left)
        lv.addWidget(caption("What the detector sees"))
        self.preview = QLabel("Not capturing")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(320, 200)
        self.preview.setStyleSheet(f"background: #222833; border-radius: 4px; color: {theme.GREY};")
        lv.addWidget(self.preview, 1)
        self.meter = ScoreMeter()
        lv.addWidget(self.meter)
        self.score_text = QLabel("")
        self.score_text.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        lv.addWidget(self.score_text)
        row.addWidget(left, 13)

        right = panel()
        rv = QVBoxLayout(right)
        rv.addWidget(caption("Event log"))
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(200)
        rv.addWidget(self.log, 1)
        row.addWidget(right, 10)

        controller.reading.connect(self.on_reading)
        controller.log_line.connect(self.append)
        controller.moonlight.connect(lambda kind, msg: self.append(f"Moonlight: {msg}"))
        controller.running_changed.connect(self._on_running)

    def on_reading(self, score: float, img: QImage) -> None:
        thr = self.c.cfg["detection"]["threshold"]
        self.meter.set(score, thr)
        self.score_text.setText(f"icon match {score:.2f} · threshold {thr:.2f} · "
                                f"{self.c.cfg['capture']['analysis_fps']} fps")
        if not img.isNull():
            pix = QPixmap.fromImage(img).scaled(self.preview.size(),
                                                Qt.AspectRatioMode.KeepAspectRatio,
                                                Qt.TransformationMode.SmoothTransformation)
            self.preview.setPixmap(pix)

    def _on_running(self, running: bool) -> None:
        if not running:
            self.preview.clear()
            self.preview.setText("Not capturing")

    def append(self, line: str) -> None:
        self.log.appendPlainText(f"{time.strftime('%H:%M:%S')}  {line}")
