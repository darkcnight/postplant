"""Colours and the app stylesheet."""
RED = "#ff4655"
GREEN = "#3ddc97"
AMBER = "#f5a623"
GREY = "#6b7383"
BG = "#0f1218"
PANEL = "#161a22"
LINE = "#2a2f3a"
TEXT = "#e8e6e3"
MUTED = "#9aa3b2"

STATE_COLORS = {
    "idle": GREY,
    "planted": RED,
    "cooldown": MUTED,
    "defused": "#2ee59d",
    "detonated": "#ff8c00",
    "wiped": "#b3001b",
    "aborted": MUTED,
}

STYLESHEET = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-size: 13px; }}
QTabWidget::pane {{ border: none; border-top: 1px solid {LINE}; }}
QTabBar::tab {{ background: {PANEL}; color: {MUTED}; padding: 9px 18px; border: none; }}
QTabBar::tab:selected {{ color: white; border-bottom: 2px solid {RED}; }}
QFrame#panel, QGroupBox {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 8px; }}
QGroupBox {{ margin-top: 16px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {GREY};
                    font-size: 10px; letter-spacing: 1px; text-transform: uppercase; }}
QLabel#caption {{ color: {GREY}; font-size: 10px; letter-spacing: 1px; background: transparent; }}
QLabel {{ background: transparent; }}
QFrame#strip {{ background: {PANEL}; border-top: 1px solid {LINE}; }}
QPushButton {{ background: #222833; color: #c9ced8; border: 1px solid {LINE}; border-radius: 6px;
               padding: 6px 12px; }}
QPushButton:hover {{ border-color: {MUTED}; }}
QPushButton#primary {{ background: {RED}; color: white; border: none; font-weight: 600; }}
QPushButton#primary:hover {{ background: #ff6470; }}
QLineEdit, QSpinBox, QDoubleSpinBox {{ background: {BG}; border: 1px solid {LINE}; border-radius: 5px;
                                       padding: 5px 7px; selection-background-color: {RED}; }}
QListWidget, QPlainTextEdit {{ background: {BG}; border: 1px solid {LINE}; border-radius: 6px; }}
QListWidget#effectList {{ background: {PANEL}; padding: 4px; }}
QListWidget#effectList::item {{ padding: 9px 10px; border-radius: 6px; }}
QListWidget#effectList::item:selected {{ background: #222833; color: white; border: 1px solid {RED}; }}
QPlainTextEdit {{ font-family: monospace; font-size: 11px; color: {MUTED}; }}
QCheckBox {{ background: transparent; spacing: 8px; }}
QCheckBox::indicator, QListWidget::indicator {{ width: 14px; height: 14px; border-radius: 3px;
                                               border: 1px solid {GREY}; background: {BG}; }}
QCheckBox::indicator:checked, QListWidget::indicator:checked {{ background: {RED};
                                                               border-color: {RED}; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 16px; background: #222833;
                                                             border: none; }}
QAbstractSpinBox::up-arrow {{ width: 0; height: 0; border-left: 4px solid transparent;
                              border-right: 4px solid transparent; border-bottom: 5px solid {MUTED}; }}
QAbstractSpinBox::down-arrow {{ width: 0; height: 0; border-left: 4px solid transparent;
                                border-right: 4px solid transparent; border-top: 5px solid {MUTED}; }}
QSlider::groove:horizontal {{ height: 5px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {RED}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; width: 14px; margin: -5px 0; border-radius: 7px; }}
"""
