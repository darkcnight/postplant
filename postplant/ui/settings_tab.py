"""Settings tab: Home Assistant, lights, capture and detection."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QListWidget, QListWidgetItem, QPushButton, QScrollArea,
                             QSizePolicy, QSlider, QSpinBox, QVBoxLayout, QWidget)

from . import theme


class SettingsTab(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.c = controller
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        row = QHBoxLayout(body)
        row.setContentsMargins(14, 4, 14, 14)
        row.setSpacing(12)
        row.addWidget(self._ha_box(), 1)
        col = QVBoxLayout()
        col.addWidget(self._game_box())
        self.cs2_box = self._cs2_box()
        col.addWidget(self.cs2_box)
        col.addWidget(self._capture_box())
        self.det_box = self._detection_box()
        col.addWidget(self.det_box)
        col.addStretch()
        self._show_game(self.c.game)
        row.addLayout(col, 1)

        controller.connection_checked.connect(self._on_checked)
        controller.lights_loaded.connect(self._fill_lights)

    # ------------------------------------------------------------------ Home Assistant
    def _ha_box(self):
        box = QGroupBox("Home Assistant")
        v = QVBoxLayout(box)
        ha = self.c.cfg["home_assistant"]
        v.addWidget(QLabel("URL"))
        self.url = QLineEdit(ha["url"])
        v.addWidget(self.url)
        v.addWidget(QLabel("Long-lived access token"))
        tok_row = QHBoxLayout()
        self.token = QLineEdit(ha["token"])
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        show = QPushButton("Show")
        show.setCheckable(True)
        show.toggled.connect(lambda on: (self.token.setEchoMode(
            QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
            show.setText("Hide" if on else "Show")))
        tok_row.addWidget(self.token, 1)
        tok_row.addWidget(show)
        v.addLayout(tok_row)
        hint = QLabel("HA → your profile → Security → Long-lived access tokens")
        hint.setStyleSheet(f"color: {theme.GREY}; font-size: 11px;")
        v.addWidget(hint)
        test_row = QHBoxLayout()
        test = QPushButton("Save && test connection")
        test.clicked.connect(self._test)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        test_row.addWidget(test)
        test_row.addWidget(self.status, 1)
        v.addLayout(test_row)

        rate_row = QHBoxLayout()
        rate_row.addWidget(QLabel("Light command rate: at most one every"))
        rate = QDoubleSpinBox()
        rate.setRange(0.3, 3.0)
        rate.setSingleStep(0.1)
        rate.setSuffix(" s")
        rate.setValue(ha.get("min_command_interval_s", 1.0))
        rate.valueChanged.connect(lambda v: self._set("home_assistant", "min_command_interval_s", v))
        rate_row.addWidget(rate)
        rate_row.addStretch()
        v.addLayout(rate_row)
        rate_hint = QLabel("Zigbee groups and Tuya lights drop commands sent faster than "
                           "about 1 per second.")
        rate_hint.setWordWrap(True)
        rate_hint.setStyleSheet(f"color: {theme.GREY}; font-size: 11px;")
        v.addWidget(rate_hint)

        v.addWidget(QLabel("Lights to control"))
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search lights…")
        self.search.textChanged.connect(self._filter)
        v.addWidget(self.search)
        self.lights = QListWidget()
        self.lights.setMinimumHeight(180)
        self.lights.itemChanged.connect(self._lights_changed)
        v.addWidget(self.lights, 1)
        self._fill_lights(self.c.known_lights)
        return box

    def _test(self):
        self.status.setText("Checking…")
        self.status.setStyleSheet(f"color: {theme.MUTED};")
        self.c.set_ha(self.url.text(), self.token.text())
        self.c.check_connection()

    def _on_checked(self, ok: bool, msg: str):
        if ok and self.url.text().strip().lower().startswith("http://"):
            msg += " · plain HTTP: token is unencrypted on your network"
        self.status.setText(("✓ " if ok else "✗ ") + msg)
        self.status.setStyleSheet(f"color: {theme.GREEN if ok else theme.RED};")

    def _fill_lights(self, lights):
        chosen = set(self.c.cfg["home_assistant"]["lights"])
        self.lights.blockSignals(True)
        self.lights.clear()
        listed = {l.entity_id for l in lights}
        for entity in sorted(chosen - listed):  # configured but not (yet) listed by HA
            self._add_light(entity, f"{entity}  (not loaded)", True)
        # ticked lights first, then the rest alphabetically (lights arrive sorted by name)
        for l in sorted(lights, key=lambda l: l.entity_id not in chosen):
            self._add_light(l.entity_id, f"{l.name}  ({l.entity_id})", l.entity_id in chosen)
        self.lights.blockSignals(False)
        self._filter(self.search.text())

    def _add_light(self, entity, label, checked):
        item = QListWidgetItem(label)
        item.setData(Qt.ItemDataRole.UserRole, entity)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.lights.addItem(item)

    def _filter(self, text):
        text = text.lower()
        for i in range(self.lights.count()):
            item = self.lights.item(i)
            item.setHidden(bool(text) and text not in item.text().lower())

    def _lights_changed(self, _item):
        self.c.cfg["home_assistant"]["lights"] = [
            self.lights.item(i).data(Qt.ItemDataRole.UserRole)
            for i in range(self.lights.count())
            if self.lights.item(i).checkState() == Qt.CheckState.Checked
        ]
        self.c.save()
        self.c.lights_loaded.emit(self.c.known_lights)  # refresh effect editors

    # ------------------------------------------------------------------ game
    def _game_box(self):
        box = QGroupBox("Game")
        v = QVBoxLayout(box)
        self.game = QComboBox()
        self.game.addItem("Valorant (reads the Moonlight stream)", "valorant")
        self.game.addItem("CS2 (Game State Integration)", "cs2")
        self.game.setCurrentIndex(1 if self.c.game == "cs2" else 0)
        self.game.currentIndexChanged.connect(self._game_changed)
        v.addWidget(self.game)
        return box

    def _game_changed(self, _i):
        game = self.game.currentData()
        self._show_game(game)
        self.c.set_game(game)

    def _show_game(self, game: str):
        cs2 = game == "cs2"
        self.cs2_box.setVisible(cs2)
        self.det_box.setVisible(not cs2)
        self.pick.setVisible(not cs2)
        self.token_status.setVisible(not cs2)

    def _cs2_box(self):
        from ..cs2 import CFG_NAME, find_cs2_cfg_dir

        box = QGroupBox("CS2")
        f = QFormLayout(box)
        c = self.c.cfg["cs2"]
        port = QSpinBox()
        port.setRange(1024, 65535)
        port.setValue(int(c["port"]))
        port.valueChanged.connect(lambda v: self._set("cs2", "port", v, restart=True))
        f.addRow("Port", port)

        self.cs2_status = QLabel("")
        self.cs2_status.setWordWrap(True)
        self.cs2_status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        install = QPushButton("Install config into CS2 on this PC")
        install.clicked.connect(self._install_cs2_cfg)
        f.addRow(install)
        f.addRow(self.cs2_status)
        found = find_cs2_cfg_dir()
        note = QLabel(f"Writes {CFG_NAME} to " + (str(found) if found else
                      "CS2's cfg folder (not found yet; is CS2 installed in Steam?)")
                      + ". Restart CS2 after installing.")
        note.setWordWrap(True)
        note.setStyleSheet(f"color: {theme.GREY}; font-size: 11px;")
        f.addRow(note)
        return box

    def _cs2_cfg(self, host: str) -> str:
        from ..cs2 import cfg_text
        c = self.c.cfg["cs2"]
        return cfg_text(f"http://{host}:{c['port']}/", c["token"])

    def _install_cs2_cfg(self):
        from ..cs2 import CFG_NAME, find_cs2_cfg_dir
        folder = find_cs2_cfg_dir()
        if folder is None:
            self._cs2_msg(False, "CS2's cfg folder wasn't found in any Steam library")
            return
        (folder / CFG_NAME).write_text(self._cs2_cfg("127.0.0.1"))
        self._cs2_msg(True, "Installed. Restart CS2, then press Start")

    def _cs2_msg(self, ok: bool, msg: str):
        self.cs2_status.setText(("✓ " if ok else "✗ ") + msg)
        self.cs2_status.setStyleSheet(f"color: {theme.GREEN if ok else theme.RED};")

    # ------------------------------------------------------------------ capture
    def _capture_box(self):
        box = QGroupBox("Start-up")
        v = QVBoxLayout(box)
        cap = self.c.cfg["capture"]
        token_ok = (self.c.root / ".state" / "portal_token").exists()
        self.token_status = QLabel("Moonlight window: " + ("✓ remembered" if token_ok
                                                             else "not picked yet"))
        v.addWidget(self.token_status)
        self.pick = QPushButton("Pick window again…")
        self.pick.clicked.connect(lambda: self.c.restart(force_picker=True))
        v.addWidget(self.pick, alignment=Qt.AlignmentFlag.AlignLeft)
        auto = QCheckBox("Start detecting when the app opens")
        auto.setChecked(cap["start_on_launch"])
        auto.toggled.connect(lambda on: self._set("capture", "start_on_launch", on))
        v.addWidget(auto)
        login = QCheckBox("Open Postplant when I log in")
        login.setChecked(cap["open_on_login"])
        login.toggled.connect(self.c.set_open_on_login)
        v.addWidget(login)
        menu = QPushButton("Add Postplant to the app menu")
        menu.clicked.connect(self._add_to_menu)
        v.addWidget(menu, alignment=Qt.AlignmentFlag.AlignLeft)
        return box

    def _add_to_menu(self):
        from .. import launcher
        launcher.install_launcher()
        if self.c.cfg["capture"]["open_on_login"]:
            launcher.set_autostart(True)       # point autostart at this copy too
        self.c.log_line.emit("Added Postplant to the app menu")

    # ------------------------------------------------------------------ detection
    def _detection_box(self):
        box = QGroupBox("Detection")
        f = QFormLayout(box)
        det = self.c.cfg["detection"]
        slider_row = QHBoxLayout()
        self.thr = QSlider(Qt.Orientation.Horizontal)
        self.thr.setRange(30, 95)
        self.thr.setValue(round(det["threshold"] * 100))
        self.thr_label = QLabel(f"{det['threshold']:.2f}")
        self.thr.valueChanged.connect(self._thr_changed)
        slider_row.addWidget(self.thr, 1)
        slider_row.addWidget(self.thr_label)
        f.addRow("Icon match threshold", slider_row)

        cutoff = QDoubleSpinBox()
        cutoff.setRange(30.0, 45.0)
        cutoff.setSingleStep(0.5)
        cutoff.setValue(det["detonation_cutoff_s"])
        cutoff.valueChanged.connect(lambda v: self._set("detection", "detonation_cutoff_s", v,
                                                        restart=True))
        f.addRow("Detonation if icon lasts (s)", cutoff)

        fps = QSpinBox()
        fps.setRange(2, 15)
        fps.setValue(self.c.cfg["capture"]["analysis_fps"])
        fps.valueChanged.connect(lambda v: self._set("capture", "analysis_fps", v, restart=True))
        f.addRow("Frames analysed per second", fps)

        dbg = QCheckBox("Save frames around each round end (debug/)")
        dbg.setChecked(self.c.cfg["debug"]["save_round_frames"])
        dbg.toggled.connect(lambda on: self._set("debug", "save_round_frames", on, restart=True))
        f.addRow(dbg)
        note = QLabel("Detection changes restart capture if it's running.")
        note.setStyleSheet(f"color: {theme.GREY}; font-size: 11px;")
        f.addRow(note)
        return box

    def _thr_changed(self, v):
        self.thr_label.setText(f"{v / 100:.2f}")
        self._set("detection", "threshold", v / 100, restart=True)

    def _set(self, section, key, value, restart=False):
        self.c.cfg[section][key] = value
        self.c.save()
        if restart and self.c.running:
            self.c.restart()
