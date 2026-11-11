"""App-menu launcher and login autostart (.desktop files)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

DESKTOP_NAME = "spike-planted.desktop"


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def python_for(root: Path) -> str:
    venv = root / ".venv" / "bin" / "python"
    return str(venv) if venv.exists() else sys.executable


def _exec_quote(arg: str) -> str:
    """Quote one Exec= argument per the Desktop Entry spec (double quotes only)."""
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def desktop_entry(root: Path) -> str:
    icon = root / "spike_planted" / "assets" / "icon.svg"
    return "\n".join([
        "[Desktop Entry]",
        "Type=Application",
        "Name=Spike Planted",
        "Comment=Valorant spike events to Home Assistant lights",
        f"Exec={_exec_quote(python_for(root))} -m spike_planted",
        f"Path={root}",
        f"Icon={icon}",
        "Terminal=false",
        "Categories=Game;",
        "",
    ])


def install_launcher(root: Path) -> Path:
    path = _data_home() / "applications" / DESKTOP_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(desktop_entry(root))
    return path


def set_autostart(root: Path, on: bool) -> Path:
    path = _config_home() / "autostart" / DESKTOP_NAME
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(desktop_entry(root))
    elif path.exists():
        path.unlink()
    return path


if __name__ == "__main__":
    print("Installed", install_launcher(Path(__file__).resolve().parents[1]))
