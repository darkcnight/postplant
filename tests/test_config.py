import os
import stat

import yaml

from spike_planted.config import DEFAULTS, deep_merge, load_config, save_config, save_token


def test_deep_merge_keeps_defaults_and_overrides():
    out = deep_merge({"a": {"b": 1, "c": 2}, "d": 3}, {"a": {"b": 9}, "e": 4})
    assert out == {"a": {"b": 9, "c": 2}, "d": 3, "e": 4}


def test_load_missing_files_gives_defaults(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    cfg = load_config(tmp_path)
    assert cfg["detection"]["threshold"] == DEFAULTS["detection"]["threshold"]
    assert cfg["home_assistant"]["token"] == ""


def test_load_reads_token_from_env_file_and_normalises_lights(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    (tmp_path / ".env").write_text("# comment\nHA_TOKEN=abc123\n")
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump({"home_assistant": {"url": "http://ha:8123", "lights": "light.one",
                                           "token": "${HA_TOKEN}"}})
    )
    cfg = load_config(tmp_path)
    assert cfg["home_assistant"]["token"] == "abc123"
    assert cfg["home_assistant"]["lights"] == ["light.one"]
    assert cfg["home_assistant"]["url"] == "http://ha:8123"


def test_placeholder_token_counts_as_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    (tmp_path / ".env").write_text("HA_TOKEN=your_long_lived_token_here\n")
    assert load_config(tmp_path)["home_assistant"]["token"] == ""


def test_save_config_never_writes_token(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    cfg = load_config(tmp_path)
    cfg["home_assistant"]["token"] = "secret"
    cfg["detection"]["threshold"] = 0.7
    save_config(tmp_path, cfg)
    text = (tmp_path / "config.yaml").read_text()
    assert "secret" not in text
    assert load_config(tmp_path)["detection"]["threshold"] == 0.7
    assert cfg["home_assistant"]["token"] == "secret"  # caller's dict untouched


def test_save_token_replaces_line_and_keeps_comments(tmp_path, monkeypatch):
    monkeypatch.delenv("HA_TOKEN", raising=False)
    (tmp_path / ".env").write_text("# keep me\nHA_TOKEN=old\nOTHER=1\n")
    save_token(tmp_path, "new")
    lines = (tmp_path / ".env").read_text().splitlines()
    assert lines == ["# keep me", "HA_TOKEN=new", "OTHER=1"]
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    assert load_config(tmp_path)["home_assistant"]["token"] == "new"
