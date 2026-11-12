from pathlib import Path

from postplant import launcher


def test_desktop_entry_runs_module_from_project(tmp_path):
    root = Path("/some/dir with space")
    text = launcher.desktop_entry(root)
    assert "Name=Postplant" in text
    assert "Path=/some/dir with space" in text
    exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
    assert exec_line.endswith(" -m postplant") and exec_line.startswith('Exec="')
    assert "'" not in exec_line                       # spec allows double quotes only
    assert launcher._exec_quote('a "b" $c') == '"a \\"b\\" \\$c"'


def test_autostart_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = launcher.set_autostart(Path("/p"), True)
    assert path.exists() and path.parent.name == "autostart"
    launcher.set_autostart(Path("/p"), False)
    assert not path.exists()


def test_install_replaces_old_spike_planted_entry(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    old = tmp_path / "applications" / "spike-planted.desktop"
    old.parent.mkdir(parents=True)
    old.write_text("[Desktop Entry]\n")
    path = launcher.install_launcher(Path("/p"))
    assert path.name == "postplant.desktop" and path.exists() and not old.exists()
