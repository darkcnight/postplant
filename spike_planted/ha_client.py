"""Async Home Assistant REST client: one shared connection, never raises into callers."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote

import httpx

log = logging.getLogger("spike.ha")

ENTITY_RE = re.compile(r"^light\.[a-z0-9_]+$")

# HA color_mode -> state attribute that holds the colour in that mode
COLOR_ATTR = {
    "hs": "hs_color",
    "xy": "xy_color",
    "rgb": "rgb_color",
    "rgbw": "rgbw_color",
    "rgbww": "rgbww_color",
    "color_temp": "color_temp_kelvin",
}


@dataclass(frozen=True)
class Light:
    entity_id: str
    name: str
    state: str


def valid_lights(entities) -> list[str]:
    good = [e for e in entities if isinstance(e, str) and ENTITY_RE.match(e)]
    bad = [e for e in entities if e not in good]
    if bad:
        log.warning("Ignoring invalid light entity ids: %s", bad)
    return good


def validate_url(url: str) -> str:
    """Accept only plain http(s)://host[:port] URLs; the token is sent to this host."""
    parsed = httpx.URL(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.host:
        raise ValueError("URL must look like http://homeassistant.local:8123")
    if parsed.userinfo:
        raise ValueError("URL must not contain a username or password")
    return str(parsed).rstrip("/")


def restore_call(state: dict) -> tuple[str, dict] | None:
    """The light service call that puts a light back the way `state` describes."""
    entity = state.get("entity_id")
    if not entity or not ENTITY_RE.match(entity):
        return None
    if state.get("state") == "off":
        return "turn_off", {"entity_id": entity}
    if state.get("state") != "on":
        return None  # unavailable / unknown: leave it alone
    attrs = state.get("attributes") or {}
    payload: dict = {"entity_id": entity}
    if attrs.get("brightness") is not None:
        payload["brightness"] = attrs["brightness"]
    effect = attrs.get("effect")
    if effect and str(effect).lower() != "none":
        payload["effect"] = effect
        return "turn_on", payload
    key = COLOR_ATTR.get(attrs.get("color_mode"))
    if key and attrs.get(key) is not None:
        payload[key] = attrs[key]
    return "turn_on", payload


class HAClient:
    def __init__(self, url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None,
                 on_health: Callable[[bool], None] | None = None, timeout: float = 3.0):
        self.base_url = validate_url(url)
        self._client = httpx.AsyncClient(
            base_url=self.base_url + "/api",
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            transport=transport,
            follow_redirects=False,   # never carry the token to another host
        )
        self._on_health = on_health
        self._healthy: bool | None = None

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ plumbing
    def _health(self, ok: bool) -> None:
        if ok != self._healthy:
            self._healthy = ok
            if self._on_health:
                self._on_health(ok)

    async def _get(self, path: str):
        try:
            r = await self._client.get(path)
            r.raise_for_status()
            self._health(True)
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            log.warning("HA GET %s failed: %s", path, e)
            self._health(False)
            return None

    async def _service(self, service: str, payload: dict) -> bool:
        try:
            r = await self._client.post(f"/services/light/{service}", json=payload)
            r.raise_for_status()
            self._health(True)
            return True
        except httpx.HTTPError as e:
            log.warning("HA light.%s failed: %s", service, e)
            self._health(False)
            return False

    # ------------------------------------------------------------------ API
    async def check(self) -> tuple[bool, str]:
        try:
            r = await self._client.get("/")
        except httpx.HTTPError as e:
            self._health(False)
            return False, f"Can't reach Home Assistant ({e.__class__.__name__})"
        if r.status_code == 401:
            self._health(False)
            return False, "Token rejected (401)"
        if r.status_code != 200:
            self._health(False)
            return False, f"Unexpected response {r.status_code}"
        self._health(True)
        return True, "Connected"

    async def list_lights(self) -> list[Light]:
        states = await self._get("/states") or []
        lights = [
            Light(s["entity_id"], (s.get("attributes") or {}).get("friendly_name") or s["entity_id"],
                  s.get("state", "unknown"))
            for s in states
            if isinstance(s, dict) and ENTITY_RE.match(s.get("entity_id", ""))
        ]
        return sorted(lights, key=lambda l: l.name.lower())

    async def snapshot(self, entities) -> dict[str, dict]:
        snap = {}
        for entity in valid_lights(entities):
            state = await self._get(f"/states/{quote(entity, safe='')}")
            if isinstance(state, dict):
                snap[entity] = state
        return snap

    async def restore(self, snap: dict[str, dict]) -> None:
        for state in snap.values():
            call = restore_call(state)
            if call:
                await self._service(*call)

    async def turn_on(self, entities, *, rgb=None, brightness: int | None = None,
                      transition: float | None = None) -> bool:
        ids = valid_lights(entities)
        if not ids:
            return False
        payload: dict = {"entity_id": ids}
        if rgb is not None:
            payload["rgb_color"] = [int(c) for c in rgb]
        if brightness is not None:
            payload["brightness"] = max(0, min(255, int(brightness)))
        if transition:
            payload["transition"] = round(float(transition), 2)
        return await self._service("turn_on", payload)

    async def turn_off(self, entities, *, transition: float | None = None) -> bool:
        ids = valid_lights(entities)
        if not ids:
            return False
        payload: dict = {"entity_id": ids}
        if transition:
            payload["transition"] = round(float(transition), 2)
        return await self._service("turn_off", payload)
