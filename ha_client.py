"""
ha_client.py — Home Assistant REST API client
"""

import asyncio
import logging
from typing import Union

import httpx

log = logging.getLogger("spike.ha")


class HAClient:
    """Thin async wrapper around the Home Assistant REST API."""

    def __init__(self, url: str, token: str):
        self.base_url = url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._saved_states: dict = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post(self, path: str, payload: dict) -> bool:
        url = f"{self.base_url}/api/{path}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.post(url, headers=self.headers, json=payload)
                r.raise_for_status()
                return True
        except Exception as e:
            log.error("HA request failed [%s]: %s", path, e)
            return False

    async def _get(self, path: str) -> dict | None:
        url = f"{self.base_url}/api/{path}"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url, headers=self.headers)
                r.raise_for_status()
                return r.json()
        except Exception as e:
            log.error("HA GET failed [%s]: %s", path, e)
            return None

    # ------------------------------------------------------------------
    # Light control
    # ------------------------------------------------------------------

    def _entity_list(self, lights: Union[str, list]) -> list[str]:
        return [lights] if isinstance(lights, str) else list(lights)

    async def save_state(self, lights: Union[str, list]) -> None:
        """Snapshot current state of all controlled lights so we can restore later."""
        for entity_id in self._entity_list(lights):
            state = await self._get(f"states/{entity_id}")
            if state:
                self._saved_states[entity_id] = state
                log.debug("Saved state for %s", entity_id)

    async def restore_state(self, lights: Union[str, list]) -> None:
        """Restore lights to their pre-round state."""
        for entity_id in self._entity_list(lights):
            saved = self._saved_states.get(entity_id)
            if not saved:
                await self.turn_off([entity_id])
                continue

            attrs = saved.get("attributes", {})
            payload: dict = {"entity_id": entity_id}

            if saved["state"] == "off":
                await self._post("services/light/turn_off", {"entity_id": entity_id})
                continue

            if "brightness" in attrs:
                payload["brightness"] = attrs["brightness"]
            if "rgb_color" in attrs:
                payload["rgb_color"] = attrs["rgb_color"]
            elif "color_temp" in attrs:
                payload["color_temp"] = attrs["color_temp"]

            await self._post("services/light/turn_on", payload)
            log.debug("Restored state for %s", entity_id)

        self._saved_states.clear()

    async def turn_on(
        self,
        lights: Union[str, list],
        rgb: tuple[int, int, int] | None = None,
        brightness: int | None = None,
        transition: float = 0.0,
    ) -> bool:
        payload: dict = {"entity_id": self._entity_list(lights)}
        if rgb:
            payload["rgb_color"] = list(rgb)
        if brightness is not None:
            payload["brightness"] = brightness
        if transition > 0:
            payload["transition"] = transition
        return await self._post("services/light/turn_on", payload)

    async def turn_off(self, lights: Union[str, list]) -> bool:
        payload = {"entity_id": self._entity_list(lights)}
        return await self._post("services/light/turn_off", payload)

    async def set_brightness(
        self, lights: Union[str, list], brightness: int
    ) -> bool:
        payload = {
            "entity_id": self._entity_list(lights),
            "brightness": brightness,
        }
        return await self._post("services/light/turn_on", payload)

