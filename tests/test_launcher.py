from pathlib import Path

from postplant import launcher


def test_desktop_entry_runs_module_from_project(tmp_path):
    root = Path("/some/dir with space")
    text = launcher.desktop_entry(["/venv/bin/python", "-m", "postplant"], root, "/i.svg")
    assert "Name=Postplant" in text
    assert "Path=/some/dir with space" in text
    exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
    assert exec_line == 'Exec="/venv/bin/python" "-m" "postplant"'
    assert "'" not in exec_line                       # spec allows double quotes only
    assert launcher._exec_quote('a "b" $c') == '"a \\"b\\" \\$c"'


def test_autostart_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    path = launcher.set_autostart(True)
    assert path.exists() and path.parent.name == "autostart"
    launcher.set_autostart(False)
    assert not path.exists()


def test_install_replaces_old_spike_planted_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    old = tmp_path / "applications" / "spike-planted.desktop"
    old.parent.mkdir(parents=True)
    old.write_text("[Desktop Entry]\n")
    path = launcher.install_launcher()
    assert path.name == "postplant.desktop" and path.exists() and not old.exists()


def test_appimage_entry_execs_the_appimage_and_copies_icon(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("APPIMAGE", "/home/me/Apps/Postplant x86_64.AppImage")
    text = launcher.install_launcher().read_text()
    assert 'Exec="/home/me/Apps/Postplant x86_64.AppImage"' in text and "Path=" not in text
    icon = next(l for l in text.splitlines() if l.startswith("Icon="))[5:]
    assert Path(icon).exists() and Path(icon).is_relative_to(tmp_path)
