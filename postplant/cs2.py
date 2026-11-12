"""CS2 mode: Valve's Game State Integration (GSI).

CS2 POSTs JSON game state to a URL listed in a `gamestate_integration_*.cfg` file in its
cfg folder. No screen capture: the bomb state (`planted` / `defused` / `exploded`) and the
round winner come straight from the game. This module is the listener and the round logic.
"""
from __future__ import annotations

import hmac
import json
import logging
import re
import secrets
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable

from .tracker import Event, Kind

log = logging.getLogger("spike.cs2")

BOMB_TIMER_S = 40.0
MAX_BODY = 256 * 1024
CFG_NAME = "gamestate_integration_postplant.cfg"
CS2_CFG_SUBDIR = Path("steamapps/common/Counter-Strike Global Offensive/game/csgo/cfg")


def new_token() -> str:
    return secrets.token_urlsafe(18)


def cfg_text(uri: str, token: str) -> str:
    """The GSI config file CS2 reads. Only the sections Postplant needs."""
    return "\n".join([
        '"Postplant"',
        "{",
        f'    "uri"       "{uri}"',
        '    "timeout"   "1.0"',
        '    "buffer"    "0.0"',
        '    "throttle"  "0.1"',
        '    "heartbeat" "10.0"',
        '    "auth"',
        "    {",
        f'        "token" "{token}"',
        "    }",
        '    "data"',
        "    {",
        '        "provider" "1"',
        '        "map"      "1"',
        '        "round"    "1"',
        "    }",
        "}",
        "",
    ])


def steam_libraries(home: Path | None = None) -> list[Path]:
    """Steam library folders on this Linux PC (native and Flatpak Steam)."""
    home = home or Path.home()
    roots = [home / ".local/share/Steam", home / ".steam/steam",
             home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]
    libs: list[Path] = []
    for root in roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        found = [root]
        if vdf.exists():
            found += [Path(p) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(errors="ignore"))]
        for lib in found:
            try:
                lib = lib.resolve()
            except OSError:
                continue
            if lib.exists() and lib not in libs:
                libs.append(lib)
    return libs


def find_cs2_cfg_dir(home: Path | None = None) -> Path | None:
    for lib in steam_libraries(home):
        if (lib / CS2_CFG_SUBDIR).is_dir():
            return lib / CS2_CFG_SUBDIR
    return None


class GSITracker:
    """Game state payloads in, the same events the Valorant tracker emits out."""

    def __init__(self):
        self.plant_t: float | None = None
        self._finished = False      # outcome sent; wait for the next round before re-arming

    @property
    def state(self) -> str:
        return "planted" if self.plant_t is not None else ("cooldown" if self._finished else "idle")

    def update(self, payload: dict, t: float) -> list[Event]:
        rnd = payload.get("round") if isinstance(payload.get("round"), dict) else None
        if not isinstance(payload.get("map"), dict) or rnd is None:
            # Left the match, or in the menus.
            self._finished = False
            return self._end(Kind.ABORTED, t, "Left the match") if self.plant_t is not None else []
        bomb, phase = rnd.get("bomb"), rnd.get("phase")

        if self.plant_t is None:
            if phase == "freezetime" or bomb is None:
                self._finished = False          # new round
            if bomb == "planted" and not self._finished:
                self.plant_t = t
                return [Event(Kind.PLANTED, t, "Bomb planted (CS2)", t)]
            return []

        if bomb == "defused":
            return self._end(Kind.DEFUSED, t, "Bomb defused (CS2)")
        if bomb == "exploded":
            return self._end(Kind.DETONATED, t, "Bomb exploded (CS2)")
        if phase == "over":
            if rnd.get("win_team") == "T":
                return self._end(Kind.WIPED, t, "Terrorists won with the bomb down (CS2)")
            return self._end(Kind.ABORTED, t, "Round over without a bomb outcome (CS2)")
        if bomb is None or phase == "freezetime":
            return self._end(Kind.ABORTED, t, "Round reset (CS2)")
        return []

    def reset(self, t: float) -> list[Event]:
        self._finished = False
        return self._end(Kind.ABORTED, t, "Stopped") if self.plant_t is not None else []

    def _end(self, kind: Kind, t: float, reason: str) -> list[Event]:
        plant_t, self.plant_t = self.plant_t, None
        self._finished = True
        if plant_t is not None:
            reason = f"{reason} after {t - plant_t:.1f}s"
        return [Event(kind, t, reason, plant_t)]


def check_payload(body: bytes, token: str) -> dict | None:
    """Parse a GSI POST body; None unless it is JSON carrying our token."""
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    auth = payload.get("auth")
    got = auth.get("token") if isinstance(auth, dict) else None
    if not isinstance(got, str) or not hmac.compare_digest(got.encode(), token.encode()):
        return None
    return payload


class GSIServer(HTTPServer):
    """Tiny HTTP listener. Each accepted payload goes to `on_payload(payload, monotonic_t)`."""

    def __init__(self, host: str, port: int, token: str,
                 on_payload: Callable[[dict, float], None]):
        self.token, self.on_payload = token, on_payload
        self.rejected = 0
        super().__init__((host, port), _Handler)
        self.timeout = 0.5


class _Handler(BaseHTTPRequestHandler):
    server: GSIServer

    def do_POST(self):  # noqa: N802 - http.server API
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY:
            self._reply(413 if length > MAX_BODY else 400)
            return
        payload = check_payload(self.rfile.read(length), self.server.token)
        if payload is None:
            self.server.rejected += 1
            self._reply(403)
            return
        self._reply(200)
        self.server.on_payload(payload, time.monotonic())

    def do_GET(self):  # noqa: N802
        self._reply(405)

    def _reply(self, code: int) -> None:
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt, *args):  # keep the console quiet
        log.debug("GSI %s", fmt % args)
