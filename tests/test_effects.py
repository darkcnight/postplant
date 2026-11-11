import asyncio
import copy

from spike_planted.config import DEFAULTS
from spike_planted.effects import Effects
from spike_planted.tracker import Kind


class RecordingHA:
    def __init__(self):
        self.log = []

    async def snapshot(self, entities):
        self.log.append(("snapshot", tuple(entities)))
        return {e: {"entity_id": e, "state": "off"} for e in entities}

    async def restore(self, snap):
        self.log.append(("restore", tuple(snap)))

    async def turn_on(self, entities, *, rgb=None, brightness=None, transition=None):
        self.log.append(("on", tuple(entities), tuple(rgb) if rgb else None, brightness))
        return True

    async def turn_off(self, entities, *, transition=None):
        self.log.append(("off", tuple(entities)))
        return True


async def no_sleep(_s):
    await asyncio.sleep(0)


def make(cfg_patch=None):
    cfg = copy.deepcopy(DEFAULTS)
    cfg["home_assistant"]["lights"] = ["light.room"]
    for name, patch in (cfg_patch or {}).items():
        cfg["effects"][name].update(patch)
    ha = RecordingHA()
    return Effects(ha, lambda: cfg, sleep=no_sleep), ha, cfg


def test_planted_then_defused_snapshots_once_and_restores():
    async def go():
        fx, ha, _ = make({"planted": {"phases": [[1.0, 0.5]]}})
        await fx.handle(Kind.PLANTED)
        await fx.wait()                      # planted sequence runs to its hold
        await fx.handle(Kind.DEFUSED)
        await fx.wait()
        return ha.log

    log = asyncio.run(go())
    assert log[0] == ("snapshot", ("light.room",))
    assert [x for x in log if x[0] == "snapshot"] == [log[0]]
    ons = [x for x in log if x[0] == "on"]
    assert ons[0][2] == (255, 42, 58) and ons[0][3] == 255
    assert [o[3] for o in ons[1:3]] == [60, 255]          # alternating low/high
    assert any(o[2] == (46, 229, 157) for o in ons)        # green defuse
    assert log[-1] == ("restore", ("light.room",))


def test_outcome_cancels_running_planted_pulse():
    async def go():
        fx, ha, _ = make()
        real_sleep = asyncio.sleep
        fx._sleep = lambda s: real_sleep(0.01)
        await fx.handle(Kind.PLANTED)
        await real_sleep(0.05)
        await fx.handle(Kind.DETONATED)
        await fx.wait()
        return ha.log

    log = asyncio.run(go())
    assert sum(1 for x in log if x[0] == "off") == 5       # strobe count
    assert log[-1][0] == "restore"


def test_aborted_restores_without_outcome_effect():
    async def go():
        fx, ha, _ = make({"planted": {"phases": [[0.2, 0.1]]}})
        await fx.handle(Kind.PLANTED)
        await fx.wait()
        n = len(ha.log)
        await fx.handle(Kind.ABORTED)
        await fx.wait()
        return ha.log[n:]

    assert asyncio.run(go()) == [("restore", ("light.room",))]


def test_aborted_with_nothing_running_is_a_noop():
    async def go():
        fx, ha, _ = make()
        await fx.handle(Kind.ABORTED)
        await fx.wait()
        return ha.log

    assert asyncio.run(go()) == []


def test_per_effect_light_override_and_union_snapshot():
    async def go():
        fx, ha, _ = make({"wiped": {"lights": ["light.logo"]}})
        await fx.test("wiped")
        return ha.log

    log = asyncio.run(go())
    assert log[0] == ("snapshot", ("light.room", "light.logo"))
    assert all(x[1] == ("light.logo",) for x in log if x[0] == "on")


def test_test_planted_runs_then_restores():
    async def go():
        fx, ha, _ = make({"planted": {"phases": [[0.2, 0.1]]}})
        await fx.test("planted", planted_s=0)
        return ha.log

    log = asyncio.run(go())
    assert log[0][0] == "snapshot" and log[-1][0] == "restore"
