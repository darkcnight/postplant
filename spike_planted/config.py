"""Settings: config.yaml (everything but the token) + .env (HA_TOKEN)."""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml

DEFAULTS: dict = {
    "home_assistant": {
        "url": "http://homeassistant.local:8123",
        "lights": [],
    },
    "capture": {
        "analysis_fps": 5,
        "start_on_launch": True,
        "open_on_login": False,
    },
    "detection": {
        "threshold": 0.60,
        "plant_confirm_s": 0.5,
        "end_gap_s": 2.5,
        "detonation_cutoff_s": 43.0,
        "whiteout_window_s": [44.0, 46.5],
        "end_evidence_frames": 3,
        "defuse_min_frames": 3,
        "hidden_timeout_s": 60.0,
        "cooldown_s": 5.0,
    },
    "debug": {
        "save_round_frames": False,
    },
    "effects": {
        "planted": {
            "color": [255, 42, 58],
            "brightness_low": 60,
            "brightness_high": 255,
            "transition": 0.15,
            "phases": [[15.0, 1.2], [15.0, 0.7], [15.0, 0.3]],
            "lights": [],
        },
        "defused": {
            "color": [46, 229, 157],
            "brightness": 255,
            "flash_s": 0.4,
            "hold_s": 1.2,
            "lights": [],
        },
        "detonated": {
            "strobe_color": [255, 255, 255],
            "strobe_count": 5,
            "strobe_interval": 0.08,
            "glow_color": [255, 40, 0],
            "glow_brightness": 180,
            "glow_s": 3.0,
            "lights": [],
        },
        "wiped": {
            "color": [179, 0, 27],
            "brightness": 200,
            "hold_s": 2.0,
            "fade_s": 1.5,
            "lights": [],
        },
    },
}

_PLACEHOLDER_TOKENS = {"your_long_lived_token_here"}


def deep_merge(base: dict, over: dict | None) -> dict:
    out = copy.deepcopy(base)
    for key, value in (over or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def read_env(path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def load_config(root: Path) -> dict:
    path = root / "config.yaml"
    user = yaml.safe_load(path.read_text()) if path.exists() else {}
    cfg = deep_merge(DEFAULTS, user if isinstance(user, dict) else {})

    ha = cfg["home_assistant"]
    if isinstance(ha.get("lights"), str):
        ha["lights"] = [ha["lights"]]
    ha["lights"] = list(ha.get("lights") or [])

    token = read_env(root / ".env").get("HA_TOKEN") or os.environ.get("HA_TOKEN", "")
    if token.startswith("${") or token in _PLACEHOLDER_TOKENS:
        token = ""
    ha["token"] = token
    return cfg


def save_config(root: Path, cfg: dict) -> None:
    out = copy.deepcopy(cfg)
    out.get("home_assistant", {}).pop("token", None)
    tmp = root / "config.yaml.tmp"
    tmp.write_text(yaml.safe_dump(out, sort_keys=False, allow_unicode=True))
    tmp.replace(root / "config.yaml")


def save_token(root: Path, token: str) -> None:
    path = root / ".env"
    lines = path.read_text().splitlines() if path.exists() else []
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith("HA_TOKEN="):
            out.append(f"HA_TOKEN={token}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"HA_TOKEN={token}")
    path.write_text("\n".join(out) + "\n")
    os.chmod(path, 0o600)
