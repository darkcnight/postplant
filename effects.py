"""
effects.py — Home Assistant light effect sequences for spike events.

Three sequences:
  • spike_planted()   — solid red immediately, then pulsing (slow → mid → fast)
  • spike_defused()   — cancels sequence, restores original state
  • spike_exploded()  — rapid white flash burst, then settle to orange-red glow
"""

import asyncio
import logging
import time
from typing import Union

from ha_client import HAClient

log = logging.getLogger("spike.effects")

# Valorant spike timer
SPIKE_DURATION = 45.0


class EffectsController:
    def __init__(self, ha: HAClient, lights: Union[str, list], config: dict):
        self.ha = ha
        self.lights = lights
        self.cfg = config
        self._sequence_task: asyncio.Task | None = None
        self._plant_time: float | None = None

    # ------------------------------------------------------------------
    # Public event handlers
    # ------------------------------------------------------------------

    async def on_planted(self) -> None:
        """Call when spike is planted — starts the countdown effect."""
        await self._cancel_sequence()
        self._plant_time = time.monotonic()

        # Save current light state so we can restore on defuse
        await self.ha.save_state(self.lights)

        self._sequence_task = asyncio.create_task(
            self._planted_sequence(), name="spike_sequence"
        )
        log.info("Spike planted effect started")

    async def on_defused(self) -> None:
        """Call when spike is defused — cancel and restore lights."""
        await self._cancel_sequence()
        log.info("Defuse! Restoring lights...")

        # Quick green flash to celebrate the defuse, then restore
        await self.ha.turn_on(self.lights, rgb=(0, 255, 80), brightness=255)
        await asyncio.sleep(0.3)
        await self.ha.turn_on(self.lights, rgb=(0, 255, 80), brightness=180)
        await asyncio.sleep(0.5)

        await self.ha.restore_state(self.lights)

    async def on_exploded(self) -> None:
        """Call when spike detonates — flash burst then settle."""
        await self._cancel_sequence()
        log.info("BOOM! Running explosion effect...")
        await self._explosion_sequence()

    # ------------------------------------------------------------------
    # Planted sequence
    # ------------------------------------------------------------------

    async def _planted_sequence(self) -> None:
        """
        Phase 1 (0–15 s):  Solid red, slow pulse every 1.2 s
        Phase 2 (15–30 s): Faster pulse every 0.7 s
        Phase 3 (30–45 s): Rapid pulse every 0.3 s (panic!)
        End:               If 45 s elapsed with no cancel → exploded
        """
        p = self.cfg["pulse"]
        hi = p["brightness_high"]
        lo = p["brightness_low"]
        RED = (255, 0, 0)

        # Set red immediately
        await self.ha.turn_on(self.lights, rgb=RED, brightness=hi)

        phases = [
            (15.0, p["pulse_slow"]),    # first 15 s
            (15.0, p["pulse_mid"]),     # next 15 s
            (15.0, p["pulse_fast"]),    # final 15 s
        ]

        try:
            for phase_duration, interval in phases:
                phase_end = time.monotonic() + phase_duration
                high = True
                while time.monotonic() < phase_end:
                    brightness = hi if high else lo
                    await self.ha.turn_on(self.lights, rgb=RED, brightness=brightness)
                    high = not high
                    await asyncio.sleep(interval)

        except asyncio.CancelledError:
            log.debug("Planted sequence cancelled")
            raise

        # Sequence ran to completion — trigger explosion ourselves
        log.info("Timer expired — triggering explosion effect")
        await self.on_exploded()

    # ------------------------------------------------------------------
    # Explosion sequence
    # ------------------------------------------------------------------

    async def _explosion_sequence(self) -> None:
        ec = self.cfg["explosion"]
        WHITE = (255, 255, 255)

        for i in range(ec["flash_count"]):
            brightness = 255 - (i * 30)  # slightly dimmer each flash
            await self.ha.turn_on(self.lights, rgb=WHITE, brightness=max(brightness, 100))
            await asyncio.sleep(ec["flash_interval"])
            await self.ha.turn_off(self.lights)
            await asyncio.sleep(ec["flash_interval"])

        # Settle to orange-red glow
        await asyncio.sleep(0.1)
        final_rgb = tuple(ec["final_color"])
        await self.ha.turn_on(
            self.lights,
            rgb=final_rgb,
            brightness=ec["final_brightness"],
        )
        await asyncio.sleep(3.0)

        # Fade back to saved state (or off)
        await self.ha.restore_state(self.lights)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _cancel_sequence(self) -> None:
        if self._sequence_task and not self._sequence_task.done():
            self._sequence_task.cancel()
            try:
                await self._sequence_task
            except asyncio.CancelledError:
                pass
        self._sequence_task = None

