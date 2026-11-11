from pathlib import Path

from spike_planted import launcher


def test_desktop_entry_runs_module_from_project(tmp_path):
    root = Path("/some/dir with space")
    text = launcher.desktop_entry(root)
    assert "Name=Spike Planted" in text
    assert "Path=/some/dir with space" in text
    exec_line = next(l for l in text.splitlines() if l.startswith("Exec="))
    assert exec_line.endswith(" -m spike_planted") and exec_line.startswith('Exec="')
    assert "'" not in exec_line                       # spec allows double quotes only
    assert launcher._exec_quote('a "b" $c') == '"a \\"b\\" \\$c"'


def test_autostart_toggle(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    path = launcher.set_autostart(Path("/p"), True)
    assert path.exists() and path.parent.name == "autostart"
    launcher.set_autostart(Path("/p"), False)
    assert not path.exists()
