"""python -m spike_planted : launch the Spike Planted window."""
import logging
import sys
from pathlib import Path

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .worker import Controller

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    # PyQt6 aborts the process on an exception escaping a slot; log it instead so a
    # stray bug can't kill the window mid-game.
    sys.excepthook = lambda *exc: logging.getLogger("spike").error("Unhandled error",
                                                                   exc_info=exc)
    app = QApplication(sys.argv)
    app.setApplicationName("Spike Planted")
    app.setDesktopFileName("spike-planted")
    icon = QIcon(str(ROOT / "spike_planted" / "assets" / "icon.svg"))
    app.setWindowIcon(icon)

    controller = Controller(ROOT)
    win = MainWindow(controller, icon)
    win.show()
    if controller.cfg["home_assistant"]["token"]:
        controller.check_connection()
    else:
        win.tabs.setCurrentIndex(2)
        controller.log_line.emit("No Home Assistant token yet: add it on the Settings tab")
    if controller.cfg["capture"]["start_on_launch"]:
        controller.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
