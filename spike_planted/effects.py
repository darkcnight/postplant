"""Light sequences for spike events. One sequence at a time; config read live."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

from .tracker import Kind

log = logging.getLogger("spike.effects")

EFFECT_NAMES = ("planted", "defused", "detonated", "wiped")


class Effects:
    def __init__(self, ha, get_cfg: Callable[[], dict],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        self.ha = ha
        self._cfg = get_cfg
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        self._snapshot: dict | None = None

    # ------------------------------------------------------------------ public
    async def handle(self, kind: Kind) -> None:
        await self._cancel()
        if self._snapshot is None:
            if kind is Kind.ABORTED:
                return
            self._snapshot = await self.ha.snapshot(self._all_lights())
        if kind is Kind.PLANTED:
            self._task = asyncio.create_task(self._planted(), name="planted")
            return
        script = {
            Kind.DEFUSED: self._defused,
            Kind.DETONATED: self._detonated,
            Kind.WIPED: self._wiped,
            Kind.ABORTED: None,
        }[kind]
        self._task = asyncio.create_task(self._finish(script), name=kind.value)

    async def test(self, name: str, planted_s: float = 8.0) -> None:
        """Run one effect on the real lights, then restore."""
        if name == "planted":
            await self.handle(Kind.PLANTED)
            await self._sleep(planted_s)
            await self.handle(Kind.ABORTED)
        else:
            await self.handle(Kind(name))
        await self.wait()

    async def wait(self) -> None:
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ helpers
    def _effect(self, name: str) -> dict:
        return self._cfg()["effects"][name]

    def _lights(self, name: str) -> list[str]:
        own = self._effect(name).get("lights") or []
        return list(own) if own else list(self._cfg()["home_assistant"]["lights"])

    def _all_lights(self) -> list[str]:
        cfg = self._cfg()
        lights = list(cfg["home_assistant"]["lights"])
        for name in EFFECT_NAMES:
            for entity in cfg["effects"][name].get("lights") or []:
                if entity not in lights:
                    lights.append(entity)
        return lights

    async def _cancel(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _finish(self, script) -> None:
        try:
            if script:
                await script()
        finally:
            snap, self._snapshot = self._snapshot, None
            if snap:
                await self.ha.restore(snap)

    # ------------------------------------------------------------------ sequences
    async def _planted(self) -> None:
        c = self._effect("planted")
        lights, rgb = self._lights("planted"), c["color"]
        hi, lo = c["brightness_high"], c["brightness_low"]
        await self.ha.turn_on(lights, rgb=rgb, brightness=hi)
        high = False
        for length, interval in c["phases"]:
            for _ in range(max(1, round(length / interval))):
                await self._sleep(interval)
                await self.ha.turn_on(lights, rgb=rgb, brightness=hi if high else lo,
                                      transition=min(c.get("transition", 0.15), interval / 2))
                high = not high
        await self.ha.turn_on(lights, rgb=rgb, brightness=hi)  # hold until an outcome

    async def _defused(self) -> None:
        c = self._effect("defused")
        lights = self._lights("defused")
        await self.ha.turn_on(lights, rgb=c["color"], brightness=c["brightness"])
        await self._sleep(c["flash_s"])
        await self.ha.turn_on(lights, rgb=c["color"], brightness=max(1, c["brightness"] // 2),
                              transition=0.3)
        await self._sleep(c["hold_s"])

    async def _detonated(self) -> None:
        c = self._effect("detonated")
        lights = self._lights("detonated")
        for _ in range(c["strobe_count"]):
            await self.ha.turn_on(lights, rgb=c["strobe_color"], brightness=255)
            await self._sleep(c["strobe_interval"])
            await self.ha.turn_off(lights)
            await self._sleep(c["strobe_interval"])
        await self.ha.turn_on(lights, rgb=c["glow_color"], brightness=c["glow_brightness"])
        await self._sleep(c["glow_s"])

    async def _wiped(self) -> None:
        c = self._effect("wiped")
        lights = self._lights("wiped")
        await self.ha.turn_on(lights, rgb=c["color"], brightness=c["brightness"])
        await self._sleep(c["hold_s"])
        await self.ha.turn_on(lights, rgb=c["color"], brightness=1, transition=c["fade_s"])
        await self._sleep(c["fade_s"])
