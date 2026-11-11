import asyncio

from fakes import FakeHA, light
from spike_planted.ha_client import HAClient, restore_call, valid_lights


def run(coro):
    return asyncio.run(coro)


def client(fake, token="tok", health=None):
    return HAClient("http://ha:8123/", token, transport=fake.transport, on_health=health)


def test_restore_call_per_color_mode():
    assert restore_call(light("light.a", "off")) == ("turn_off", {"entity_id": "light.a"})
    assert restore_call(light("light.a", brightness=120, color_mode="hs", hs_color=[30, 80])) == (
        "turn_on", {"entity_id": "light.a", "brightness": 120, "hs_color": [30, 80]})
    assert restore_call(light("light.a", brightness=50, color_mode="color_temp",
                              color_temp_kelvin=2700, color_temp=370)) == (
        "turn_on", {"entity_id": "light.a", "brightness": 50, "color_temp_kelvin": 2700})
    assert restore_call(light("light.a", brightness=9, effect="rainbow", color_mode="hs",
                              hs_color=[1, 2])) == (
        "turn_on", {"entity_id": "light.a", "brightness": 9, "effect": "rainbow"})
    assert restore_call(light("light.a", "unavailable")) is None


def test_url_validation():
    import pytest
    from spike_planted.ha_client import validate_url
    assert validate_url("http://192.168.1.50:8123/") == "http://192.168.1.50:8123"
    for bad in ("ftp://ha", "http://user:pw@ha:8123", "homeassistant.local", ""):
        with pytest.raises(ValueError):
            validate_url(bad)


def test_invalid_entity_ids_are_dropped():
    assert valid_lights(["light.ok_1", "light.../../x", "switch.fan", 3]) == ["light.ok_1"]


def test_check_connection_and_bad_token():
    fake = FakeHA()
    assert run(_check(client(fake))) == (True, "Connected")
    assert run(_check(client(fake, token="wrong"))) == (False, "Token rejected (401)")


async def _check(c):
    try:
        return await c.check()
    finally:
        await c.aclose()


def test_list_lights_sorted_by_name_and_only_lights():
    fake = FakeHA([light("light.b", friendly_name="Bedroom"), light("light.a", friendly_name="Alpha"),
                   {"entity_id": "switch.fan", "state": "on", "attributes": {}}])

    async def go():
        c = client(fake)
        try:
            return await c.list_lights()
        finally:
            await c.aclose()

    names = [l.name for l in run(go())]
    assert names == ["Alpha", "Bedroom"]


def test_snapshot_then_restore_round_trip():
    fake = FakeHA([light("light.a", brightness=100, color_mode="rgb", rgb_color=[1, 2, 3]),
                   light("light.b", "off")])

    async def go():
        c = client(fake)
        try:
            snap = await c.snapshot(["light.a", "light.b", "light.missing"])
            await c.turn_on(["light.a", "light.b"], rgb=(255, 0, 0), brightness=300, transition=0.2)
            await c.restore(snap)
            return snap
        finally:
            await c.aclose()

    snap = run(go())
    assert set(snap) == {"light.a", "light.b"}
    assert fake.calls[0] == ("turn_on", {"entity_id": ["light.a", "light.b"],
                                         "rgb_color": [255, 0, 0], "brightness": 255,
                                         "transition": 0.2})
    assert ("turn_on", {"entity_id": "light.a", "brightness": 100, "rgb_color": [1, 2, 3]}) in fake.calls
    assert ("turn_off", {"entity_id": "light.b"}) in fake.calls


def test_failures_report_health_and_do_not_raise():
    fake = FakeHA([light("light.a")])
    health = []

    async def go():
        c = client(fake, health=health.append)
        try:
            assert await c.turn_on(["light.a"], brightness=10)
            fake.fail = True
            assert not await c.turn_on(["light.a"], brightness=10)
            assert await c.snapshot(["light.a"]) == {}
        finally:
            await c.aclose()

    run(go())
    assert health == [True, False]
