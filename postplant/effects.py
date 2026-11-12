"""Light sequences for spike events.

Built for slow lights (Zigbee groups, Tuya): commands are paced to at most one per
`min_command_interval_s`, pulses use HA transitions instead of rapid steps, and every
restore is read back and retried until the lights match the pre-plant state.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from .ha_client import state_matches
from .tracker import Kind

log = logging.getLogger("spike.effects")

EFFECT_NAMES = ("planted", "defused", "detonated", "wiped")
BOMB_TIMER_S = {"valorant": 45.0, "cs2": 40.0}
VERIFY_DELAY_S = 1.5
RESTORE_ATTEMPTS = 3


class Effects:
    def __init__(self, ha, get_cfg: Callable[[], dict],
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic,
                 on_log: Callable[[str], None] | None = None):
        self.ha = ha
        self._cfg = get_cfg
        self._sleep = sleep
        self._clock = clock
        self._log = on_log or (lambda msg: log.info(msg))
        self._task: asyncio.Task | None = None
        self._snapshot: dict | None = None      # pre-plant state for the current round
        self._unrestored: dict = {}             # entity -> target we failed to put back
        self._last_target: dict = {}            # last state we restored to (for Restore now)
        self._last_send = -1e9
        self._restoring = False

    # ------------------------------------------------------------------ public
    async def handle(self, kind: Kind) -> None:
        await self._cancel()
        if self._snapshot is None:
            if kind is Kind.ABORTED:
                return
            self._snapshot = await self._take_snapshot()
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

    async def test(self, name: str, planted_s: float = 10.0) -> None:
        """Run one effect on the real lights, then restore."""
        if name == "planted":
            await self.handle(Kind.PLANTED)
            await self._sleep(planted_s)
            await self.handle(Kind.ABORTED)
        else:
            await self.handle(Kind(name))
        await self.wait()

    async def stop_and_restore(self) -> None:
        await self.handle(Kind.ABORTED)
        await self.wait()

    async def restore_now(self) -> bool:
        """Put the lights back to the last known pre-plant state."""
        await self._cancel()
        target = self._snapshot or {**self._last_target, **self._unrestored}
        self._snapshot = None
        if not target and not self._restore_plan()[1]:
            self._log("Nothing to restore yet (no round has changed the lights)")
            return False
        return await self._restore(target)

    async def wait(self) -> None:
        if self._task:
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------ helpers
    def _effect(self, name: str) -> dict:
        return self._cfg()["effects"][name]

    @property
    def _min_gap(self) -> float:
        return float(self._cfg()["home_assistant"].get("min_command_interval_s", 1.0))

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

    def _restore_plan(self) -> tuple[list[str], list[str]]:
        """(entities whose state we save and put back, scripts to run after a round).

        Per-light rule in home_assistant.restore: {"mode": "state"} (default),
        {"mode": "members", "members": [...]} for groups whose own state is only an
        average, or {"mode": "script", "script": "script.x"} for looks a light state
        can't describe (e.g. a device-side scene)."""
        rules = self._cfg()["home_assistant"].get("restore") or {}
        states, scripts = [], []
        for light in self._all_lights():
            rule = rules.get(light) or {}
            mode = rule.get("mode", "state")
            if mode == "script" and rule.get("script"):
                if rule["script"] not in scripts:
                    scripts.append(rule["script"])
                continue
            targets = (rule.get("members") or [light]) if mode == "members" else [light]
            states += [t for t in targets if t not in states]
        return states, scripts

    async def _take_snapshot(self) -> dict:
        snap = await self.ha.snapshot(self._restore_plan()[0])
        # A light we failed to restore last round is still showing our colours; keep the
        # state we were trying to put it back to instead of saving the stuck colour.
        for entity, target in self._unrestored.items():
            if entity in snap:
                snap[entity] = target
        return snap

    async def _pace(self) -> None:
        wait = self._min_gap - (self._clock() - self._last_send)
        if wait > 0:
            await self._sleep(wait)
        self._last_send = self._clock()

    async def _on(self, lights, **kw) -> None:
        await self._pace()
        # One call per light, in parallel: HA answers a call only when every light in it
        # is done, so a slow cloud light (Tuya) would otherwise hold up the Zigbee ones.
        await asyncio.gather(*(self.ha.turn_on([light], **kw) for light in lights))

    async def _cancel(self) -> None:
        if self._task and not self._task.done():
            if self._restoring:     # never interrupt a restore; let it finish first
                await self.wait()
                self._task = None
                return
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
                await self._restore(snap)

    async def _restore(self, target: dict) -> bool:
        self._restoring = True
        try:
            return await self._restore_inner(target)
        finally:
            self._restoring = False

    async def _restore_inner(self, target: dict) -> bool:
        self._last_target = dict(target)
        scripts = self._restore_plan()[1]
        for script in scripts:
            await self._pace()
            await self.ha.run_script(script)
        if not target:
            return True
        transition = float(self._cfg()["home_assistant"].get("restore_transition_s", 1.0))
        pending = dict(target)
        for attempt in range(1, RESTORE_ATTEMPTS + 1):
            await self._pace()
            await self.ha.restore(pending, transition=transition)
            self._last_send = self._clock()
            await self._sleep(transition + VERIFY_DELAY_S)
            current = await self.ha.snapshot(list(pending))
            pending = {e: s for e, s in pending.items() if not state_matches(s, current.get(e))}
            if not pending:
                self._unrestored = {}
                if attempt > 1:
                    self._log(f"Lights restored (took {attempt} tries)")
                return True
        self._unrestored = pending
        self._log("Couldn't confirm restore for: " + ", ".join(pending)
                  + ". Will keep their earlier state for the next round")
        return False

    # ------------------------------------------------------------------ sequences
    async def _planted(self) -> None:
        c = self._effect("planted")
        lights, rgb = self._lights("planted"), c["color"]
        hi, lo = c["brightness_high"], c["brightness_low"]
        await self._on(lights, rgb=rgb, brightness=hi)
        high = False
        # Phases are written for Valorant's 45 s spike; stretch them to this game's timer.
        scale = BOMB_TIMER_S.get(self._cfg().get("game"), 45.0) / BOMB_TIMER_S["valorant"]
        for length, interval in c["phases"]:
            length = float(length) * scale
            step = max(float(interval), self._min_gap)
            for _ in range(max(1, round(length / step))):
                # Fade over the whole step so slow lights breathe instead of flicker. Brightness
                # only: the colour was set above, and resending it doubles the Zigbee commands
                # (measured: pulses confirmed about twice as fast without it).
                await self._on(lights, brightness=hi if high else lo,
                               transition=step * 0.9)
                await self._sleep(step)
                high = not high
        await self._on(lights, rgb=rgb, brightness=hi)  # hold until an outcome

    async def _defused(self) -> None:
        c = self._effect("defused")
        lights = self._lights("defused")
        await self._on(lights, rgb=c["color"], brightness=c["brightness"], transition=0)
        await self._sleep(c["flash_s"])
        await self._on(lights, rgb=c["color"], brightness=max(1, c["brightness"] // 2),
                       transition=self._min_gap)
        await self._sleep(c["hold_s"])

    async def _detonated(self) -> None:
        c = self._effect("detonated")
        lights = self._lights("detonated")
        await self._on(lights, rgb=c["flash_color"], brightness=255, transition=0)
        await self._sleep(c["flash_s"])
        await self._on(lights, rgb=c["glow_color"], brightness=c["glow_brightness"],
                       transition=c["fade_s"])
        await self._sleep(c["fade_s"] + c["glow_s"])

    async def _wiped(self) -> None:
        c = self._effect("wiped")
        lights = self._lights("wiped")
        await self._on(lights, rgb=c["color"], brightness=c["brightness"])
        await self._sleep(c["hold_s"])
        await self._on(lights, rgb=c["color"], brightness=1, transition=c["fade_s"])
        await self._sleep(c["fade_s"])
