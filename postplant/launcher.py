"""App-menu launcher and login autostart (.desktop files)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

DESKTOP_NAME = "postplant.desktop"
LEGACY_NAMES = ("spike-planted.desktop",)   # the app's name before Postplant


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


SOURCE_ROOT = Path(__file__).resolve().parents[1]
ICON_SRC = Path(__file__).resolve().parent / "assets" / "icon.svg"


def python_for(root: Path) -> str:
    venv = root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def app_command() -> tuple[list[str], Path | None]:
    """How to start this copy of Postplant: (argv, working dir)."""
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        return [appimage], None
    return [python_for(SOURCE_ROOT), "-m", "postplant"], SOURCE_ROOT


def install_icon() -> str:
    """Copy the icon somewhere stable (an AppImage's own files vanish when it exits)."""
    dest = _data_home() / "icons" / "hicolor" / "scalable" / "apps" / "postplant.svg"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(ICON_SRC.read_bytes())
    return str(dest)


def _exec_quote(arg: str) -> str:
    """Quote one Exec= argument per the Desktop Entry spec (double quotes only)."""
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def desktop_entry(argv: list[str], cwd: Path | None, icon: str) -> str:
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Postplant",
        "Comment=Valorant and CS2 bomb events to Home Assistant lights",
        "Exec=" + " ".join(_exec_quote(a) for a in argv),
    ]
    if cwd is not None:
        lines.append(f"Path={cwd}")
    lines += [f"Icon={icon}", "Terminal=false", "Categories=Game;", ""]
    return "\n".join(lines)


def _entry() -> str:
    argv, cwd = app_command()
    return desktop_entry(argv, cwd, install_icon())


def _remove_legacy(folder: Path) -> None:
    for name in LEGACY_NAMES:
        (folder / name).unlink(missing_ok=True)


def install_launcher() -> Path:
    path = _data_home() / "applications" / DESKTOP_NAME
    _remove_legacy(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_entry())
    return path


def launcher_installed() -> bool:
    return (_data_home() / "applications" / DESKTOP_NAME).exists()


def set_autostart(on: bool) -> Path:
    path = _config_home() / "autostart" / DESKTOP_NAME
    _remove_legacy(path.parent)
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_entry())
    elif path.exists():
        path.unlink()
    return path


if __name__ == "__main__":
    print("Installed", install_launcher())
