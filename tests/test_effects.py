import asyncio
import copy

from spike_planted.config import DEFAULTS
from spike_planted.effects import Effects
from spike_planted.ha_client import restore_call
from spike_planted.tracker import Kind


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    async def sleep(self, s):
        self.t += max(0.0, s)
        await asyncio.sleep(0)


class StatefulHA:
    """Remembers light states; can drop restore commands like a flooded Zigbee mesh."""

    def __init__(self, clock, states):
        self.clock = clock
        self.states = copy.deepcopy(states)
        self.sent = []                 # (time, kind, entities)
        self.drop_restores = {}        # entity -> how many restore commands to drop

    async def snapshot(self, entities):
        return {e: copy.deepcopy(self.states[e]) for e in entities if e in self.states}

    async def restore(self, snap, *, transition=None):
        for entity, target in snap.items():
            self.sent.append((self.clock.t, "restore", (entity,)))
            if self.drop_restores.get(entity, 0) > 0:
                self.drop_restores[entity] -= 1
                continue
            service, payload = restore_call(target)
            self.states[entity] = copy.deepcopy(target)

    async def turn_on(self, entities, *, rgb=None, brightness=None, transition=None):
        self.sent.append((self.clock.t, "on", tuple(entities), tuple(rgb or ()), brightness,
                          transition))
        for e in entities:
            self.states[e] = {"entity_id": e, "state": "on",
                              "attributes": {"color_mode": "rgb", "rgb_color": list(rgb),
                                             "brightness": brightness}}
        return True

    async def turn_off(self, entities, *, transition=None):
        self.sent.append((self.clock.t, "off", tuple(entities)))
        return True


WARM = {"entity_id": "light.room", "state": "on",
        "attributes": {"color_mode": "color_temp", "color_temp_kelvin": 2700, "brightness": 120}}
LOGO = {"entity_id": "light.logo", "state": "on",
        "attributes": {"color_mode": "hs", "hs_color": [200, 50], "brightness": 80}}


def make(patch=None, lights=("light.room", "light.logo")):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["home_assistant"]["lights"] = list(lights)
    for name, p in (patch or {}).items():
        cfg["effects"][name].update(p)
    clock = FakeClock()
    ha = StatefulHA(clock, {"light.room": WARM, "light.logo": LOGO})
    logs = []
    fx = Effects(ha, lambda: cfg, sleep=clock.sleep, clock=clock, on_log=logs.append)
    return fx, ha, cfg, logs


def command_times(ha):
    return [x[0] for x in ha.sent]


def test_commands_are_paced_to_min_interval():
    async def go():
        fx, ha, cfg, _ = make()
        await fx.handle(Kind.PLANTED)
        await fx.wait()
        await fx.handle(Kind.DETONATED)
        await fx.wait()
        return ha

    ha = asyncio.run(go())
    times = command_times(ha)
    gaps = [b - a for a, b in zip(times, times[1:]) if b != a]   # restore sends per light at once
    assert min(gaps) >= 1.0 - 1e-9
    planted = [x for x in ha.sent if x[1] == "on" and x[3] == (255, 42, 58)]
    assert len(planted) <= 50                 # was ~100 with the old 0.3 s pulse
    assert all(x[5] is None or x[5] >= 0.9 for x in planted)   # every pulse step fades


def test_detonation_is_flash_then_glow_not_strobe():
    async def go():
        fx, ha, _, _ = make()
        await fx.test("detonated")
        return ha

    ha = asyncio.run(go())
    ons = [x for x in ha.sent if x[1] == "on"]
    assert [o[3] for o in ons] == [(255, 255, 255), (255, 40, 0)]
    assert not [x for x in ha.sent if x[1] == "off"]


def test_restore_puts_lights_back_exactly():
    async def go():
        fx, ha, _, _ = make()
        await fx.handle(Kind.PLANTED)
        await fx.wait()
        await fx.handle(Kind.DEFUSED)
        await fx.wait()
        return ha

    ha = asyncio.run(go())
    assert ha.states["light.room"] == WARM and ha.states["light.logo"] == LOGO


def test_dropped_restore_is_retried():
    async def go():
        fx, ha, _, logs = make()
        ha.drop_restores = {"light.logo": 1}
        await fx.test("wiped")
        return ha, logs

    ha, logs = asyncio.run(go())
    assert ha.states["light.logo"] == LOGO
    assert any("2 tries" in l for l in logs)


def test_unconfirmed_restore_does_not_poison_next_snapshot():
    async def go():
        fx, ha, _, logs = make()
        ha.drop_restores = {"light.logo": 99}          # logo never comes back this round
        await fx.test("detonated")
        stuck = copy.deepcopy(ha.states["light.logo"])
        ha.drop_restores = {}
        await fx.test("defused")                        # next round
        return ha, stuck, logs

    ha, stuck, logs = asyncio.run(go())
    assert stuck["attributes"]["rgb_color"] == [255, 40, 0]   # was stuck on the glow
    assert ha.states["light.logo"] == LOGO                    # restored to the real original
    assert any("Couldn't confirm" in l for l in logs)


def test_stop_and_restore_waits_for_lights():
    async def go():
        fx, ha, _, _ = make()
        await fx.handle(Kind.PLANTED)
        await asyncio.sleep(0)
        await fx.stop_and_restore()
        return ha

    ha = asyncio.run(go())
    assert ha.states["light.room"] == WARM and ha.states["light.logo"] == LOGO


def test_restore_now_after_round_uses_last_target():
    async def go():
        fx, ha, _, _ = make()
        await fx.test("wiped")
        ha.states["light.room"] = {"entity_id": "light.room", "state": "on",
                                   "attributes": {"color_mode": "rgb", "rgb_color": [255, 0, 0],
                                                  "brightness": 255}}
        ok = await fx.restore_now()
        return ok, ha

    ok, ha = asyncio.run(go())
    assert ok and ha.states["light.room"] == WARM


def test_restore_now_with_no_history_is_a_noop():
    async def go():
        fx, ha, _, logs = make()
        return await fx.restore_now(), ha.sent, logs

    ok, sent, logs = asyncio.run(go())
    assert not ok and sent == [] and "Nothing to restore" in logs[0]


def test_aborted_with_nothing_running_is_a_noop():
    async def go():
        fx, ha, _, _ = make()
        await fx.handle(Kind.ABORTED)
        await fx.wait()
        return ha.sent

    assert asyncio.run(go()) == []


def test_per_effect_light_override_and_union_snapshot():
    async def go():
        fx, ha, _, _ = make({"wiped": {"lights": ["light.logo"]}}, lights=("light.room",))
        await fx.test("wiped")
        return ha

    ha = asyncio.run(go())
    assert all(x[2] == ("light.logo",) for x in ha.sent if x[1] == "on")
    assert {x[2][0] for x in ha.sent if x[1] == "restore"} == {"light.room", "light.logo"}
