"""Effects tab: pick an effect, edit it, try it on the lights."""
from __future__ import annotations

import copy

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (QColorDialog, QDoubleSpinBox, QFormLayout, QFrame, QGridLayout,
                             QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton,
                             QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from ..config import DEFAULTS
from . import theme

TITLES = {"planted": "Planted", "defused": "Defused", "detonated": "Detonated",
          "wiped": "Defenders wiped"}
SWATCH_KEY = {"planted": "color", "defused": "color", "detonated": "strobe_color",
              "wiped": "color"}

# (key, label, kind, min, max, step)
FIELDS = {
    "planted": [
        ("color", "Colour", "color"),
        ("brightness_low", "Pulse low brightness", "int", 0, 255, 5),
        ("brightness_high", "Pulse high brightness", "int", 0, 255, 5),
        ("transition", "Pulse fade (s)", "float", 0.0, 1.0, 0.05),
        ("phases", "Pulse speed over the 45s timer", "phases"),
    ],
    "defused": [
        ("color", "Colour", "color"),
        ("brightness", "Brightness", "int", 1, 255, 5),
        ("flash_s", "Flash (s)", "float", 0.1, 5.0, 0.1),
        ("hold_s", "Hold before restore (s)", "float", 0.0, 10.0, 0.1),
    ],
    "detonated": [
        ("strobe_color", "Strobe colour", "color"),
        ("strobe_count", "Strobe flashes", "int", 0, 20, 1),
        ("strobe_interval", "Strobe speed (s)", "float", 0.03, 1.0, 0.01),
        ("glow_color", "Glow colour", "color"),
        ("glow_brightness", "Glow brightness", "int", 1, 255, 5),
        ("glow_s", "Glow (s)", "float", 0.0, 15.0, 0.5),
    ],
    "wiped": [
        ("color", "Colour", "color"),
        ("brightness", "Brightness", "int", 1, 255, 5),
        ("hold_s", "Hold (s)", "float", 0.0, 10.0, 0.1),
        ("fade_s", "Fade out (s)", "float", 0.0, 10.0, 0.1),
    ],
}


def swatch_style(rgb) -> str:
    return (f"background: rgb({rgb[0]},{rgb[1]},{rgb[2]}); border: 1px solid {theme.LINE}; "
            "border-radius: 4px; min-width: 60px; min-height: 22px;")


class EffectEditor(QWidget):
    def __init__(self, controller, name: str, on_changed):
        super().__init__()
        self.c, self.name, self.on_changed = controller, name, on_changed
        self.v = QVBoxLayout(self)
        self.v.setContentsMargins(0, 0, 0, 0)
        self.build()

    @property
    def data(self) -> dict:
        return self.c.cfg["effects"][self.name]

    def save(self) -> None:
        self.c.save()
        self.on_changed()

    def build(self) -> None:
        while self.v.count():
            w = self.v.takeAt(0).widget()
            if w:
                w.deleteLater()
        body = QWidget()
        form = QFormLayout(body)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.setVerticalSpacing(8)
        for spec in FIELDS[self.name]:
            key, label, kind = spec[:3]
            if kind == "color":
                form.addRow(label, self._color(key))
            elif kind == "int":
                form.addRow(label, self._spin(key, QSpinBox(), *spec[3:]))
            elif kind == "float":
                form.addRow(label, self._spin(key, QDoubleSpinBox(), *spec[3:]))
            elif kind == "phases":
                form.addRow(QLabel(label))
                form.addRow(self._phases())
        form.addRow(QLabel("Lights (none ticked = the lights picked in Settings)"))
        self.lights = QListWidget()
        self.lights.setMaximumHeight(110)
        self.lights.itemChanged.connect(self._lights_changed)
        form.addRow(self.lights)
        self.refresh_lights()
        self.v.addWidget(body)

    def _color(self, key):
        btn = QPushButton()
        btn.setStyleSheet(swatch_style(self.data[key]))

        def pick():
            r, g, b = self.data[key]
            col = QColorDialog.getColor(QColor(r, g, b), self, "Pick colour")
            if col.isValid():
                self.data[key] = [col.red(), col.green(), col.blue()]
                btn.setStyleSheet(swatch_style(self.data[key]))
                self.save()

        btn.clicked.connect(pick)
        return btn

    def _spin(self, key, box, lo, hi, step):
        box.setRange(lo, hi)
        box.setSingleStep(step)
        if isinstance(box, QDoubleSpinBox):
            box.setDecimals(2)
        box.setValue(self.data[key])

        def changed(v):
            self.data[key] = v
            self.save()

        box.valueChanged.connect(changed)
        return box

    def _phases(self):
        w = QWidget()
        g = QGridLayout(w)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(QLabel("Phase"), 0, 0)
        g.addWidget(QLabel("Length (s)"), 0, 1)
        g.addWidget(QLabel("Pulse every (s)"), 0, 2)
        for i, (length, interval) in enumerate(self.data["phases"]):
            g.addWidget(QLabel(f"{i + 1}"), i + 1, 0)
            for col, (val, lo, hi) in enumerate([(length, 1.0, 60.0), (interval, 0.1, 5.0)], 1):
                box = QDoubleSpinBox()
                box.setRange(lo, hi)
                box.setSingleStep(0.1)
                box.setValue(val)

                def changed(v, i=i, col=col):
                    self.data["phases"][i][col - 1] = v
                    self.save()

                box.valueChanged.connect(changed)
                g.addWidget(box, i + 1, col)
        return w

    def refresh_lights(self) -> None:
        self.lights.blockSignals(True)
        self.lights.clear()
        chosen = set(self.data.get("lights") or [])
        names = {l.entity_id: l.name for l in self.c.known_lights}
        ids = list(dict.fromkeys(self.c.cfg["home_assistant"]["lights"] + sorted(chosen)))
        for entity in ids:
            item = QListWidgetItem(f"{names.get(entity, entity)}")
            item.setData(Qt.ItemDataRole.UserRole, entity)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if entity in chosen else Qt.CheckState.Unchecked)
            self.lights.addItem(item)
        self.lights.blockSignals(False)

    def _lights_changed(self, _item) -> None:
        self.data["lights"] = [
            self.lights.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.lights.count())
            if self.lights.item(i).checkState() == Qt.CheckState.Checked
        ]
        self.save()


class EffectsTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 14, 14, 14)
        row.setSpacing(12)

        self.list = QListWidget()
        self.list.setObjectName("effectList")
        self.list.setFixedWidth(180)
        row.addWidget(self.list)

        right = QFrame()
        right.setObjectName("panel")
        rv = QVBoxLayout(right)
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 15px; font-weight: 600;")
        head.addWidget(self.title)
        head.addStretch()
        reset = QPushButton("Reset")
        reset.clicked.connect(self.reset)
        test = QPushButton("▶  Test on lights")
        test.setObjectName("primary")
        test.clicked.connect(lambda: self.c.test_effect(self.current))
        head.addWidget(reset)
        head.addWidget(test)
        rv.addLayout(head)
        self.stack = QStackedWidget()
        rv.addWidget(self.stack, 1)
        row.addWidget(right, 1)

        self.editors = {}
        for name in FIELDS:
            item = QListWidgetItem(TITLES[name])
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
            ed = EffectEditor(controller, name, lambda n=name: self._update_icon(n))
            self.editors[name] = ed
            self.stack.addWidget(ed)
            self._update_icon(name)
        self.list.currentRowChanged.connect(self._select)
        self.list.setCurrentRow(0)
        controller.lights_loaded.connect(lambda _l: [e.refresh_lights() for e in self.editors.values()])

    @property
    def current(self) -> str:
        return self.list.currentItem().data(Qt.ItemDataRole.UserRole)

    def _select(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        self.title.setText(TITLES[self.current])

    def _update_icon(self, name: str) -> None:
        from PyQt6.QtGui import QIcon, QPixmap
        rgb = self.c.cfg["effects"][name][SWATCH_KEY[name]]
        pix = QPixmap(14, 14)
        pix.fill(QColor(*rgb))
        item = self.list.item(list(FIELDS).index(name))
        if item:
            item.setIcon(QIcon(pix))

    def reset(self) -> None:
        name = self.current
        self.c.cfg["effects"][name] = copy.deepcopy(DEFAULTS["effects"][name])
        self.c.save()
        self.editors[name].build()
        self._update_icon(name)
