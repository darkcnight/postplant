import asyncio
import http.client
import json
import threading

from postplant.cs2 import (CFG_NAME, CS2_CFG_SUBDIR, GSIServer, GSITracker, cfg_text,
                           check_payload, find_cs2_cfg_dir)
from postplant.tracker import Kind

TOKEN = "tok123"


def state(phase="live", bomb=None, win=None, in_match=True):
    p = {"provider": {"name": "Counter-Strike 2"}, "auth": {"token": TOKEN}}
    if in_match:
        p["map"] = {"phase": "live"}
        p["round"] = {"phase": phase}
        if bomb:
            p["round"]["bomb"] = bomb
        if win:
            p["round"]["win_team"] = win
    return p


def run(tracker, seq):
    events = []
    for t, payload in seq:
        events += tracker.update(payload, t)
    return [e.kind for e in events], events


def test_plant_then_each_outcome():
    for bomb, win, kind in [("defused", "CT", Kind.DEFUSED), ("exploded", "T", Kind.DETONATED)]:
        kinds, events = run(GSITracker(), [
            (0, state("freezetime")), (10, state()), (20, state(bomb="planted")),
            (30, state(bomb="planted")), (55, state("over", bomb=bomb, win=win))])
        assert kinds == [Kind.PLANTED, kind]
        assert "35.0s" in events[1].reason and events[1].plant_t == 20


def test_ct_wiped_after_plant_is_wiped():
    kinds, _ = run(GSITracker(), [(0, state(bomb="planted")),
                                  (12, state("over", bomb="planted", win="T"))])
    assert kinds == [Kind.PLANTED, Kind.WIPED]


def test_no_second_plant_event_until_next_round():
    tr = GSITracker()
    kinds, _ = run(tr, [(0, state(bomb="planted")), (5, state("over", bomb="defused", win="CT")),
                        (6, state("over", bomb="defused", win="CT")),
                        (7, state("over", bomb="planted"))])   # stale payload during "over"
    assert kinds == [Kind.PLANTED, Kind.DEFUSED]
    assert tr.state == "cooldown"
    kinds, _ = run(tr, [(15, state("freezetime")), (40, state(bomb="planted"))])
    assert kinds == [Kind.PLANTED]


def test_leaving_match_mid_plant_aborts():
    kinds, _ = run(GSITracker(), [(0, state(bomb="planted")), (3, state(in_match=False))])
    assert kinds == [Kind.PLANTED, Kind.ABORTED]


def test_reset_while_planted_aborts_once():
    tr = GSITracker()
    tr.update(state(bomb="planted"), 0)
    assert [e.kind for e in tr.reset(1)] == [Kind.ABORTED] and tr.reset(2) == []


def test_check_payload_requires_token_and_json():
    good = json.dumps(state()).encode()
    assert check_payload(good, TOKEN) is not None
    assert check_payload(good, "other") is None
    assert check_payload(b"not json", TOKEN) is None
    assert check_payload(b"[1, 2]", TOKEN) is None
    assert check_payload(json.dumps({"auth": {"token": 5}}).encode(), TOKEN) is None


def test_server_accepts_good_posts_and_rejects_bad():
    got = []
    server = GSIServer("127.0.0.1", 0, TOKEN, lambda p, t: got.append(p))
    port = server.server_address[1]
    stop = threading.Event()

    def loop():
        while not stop.is_set():
            server.handle_request()

    th = threading.Thread(target=loop, daemon=True)
    th.start()

    def post(body):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        c.request("POST", "/", body=body, headers={"Content-Type": "application/json"})
        status = c.getresponse().status
        c.close()
        return status

    try:
        assert post(json.dumps(state(bomb="planted"))) == 200
        bad = state()
        bad["auth"]["token"] = "nope"
        assert post(json.dumps(bad)) == 403
        assert post(b"x" * (300 * 1024)) == 413
    finally:
        stop.set()
        th.join(3)
        server.server_close()
    assert len(got) == 1 and got[0]["round"]["bomb"] == "planted"
    assert server.rejected == 1


def test_cfg_text_has_uri_token_and_round_data():
    text = cfg_text("http://127.0.0.1:3450/", TOKEN)
    assert '"uri"       "http://127.0.0.1:3450/"' in text
    assert f'"token" "{TOKEN}"' in text and '"round"    "1"' in text
    assert text.count("{") == text.count("}")


def test_find_cfg_dir_in_extra_steam_library(tmp_path):
    home = tmp_path / "home"
    steam = home / ".local/share/Steam/steamapps"
    steam.mkdir(parents=True)
    lib = tmp_path / "games"
    (lib / CS2_CFG_SUBDIR).mkdir(parents=True)
    (steam / "libraryfolders.vdf").write_text(
        '"libraryfolders"\n{\n "0" { "path" "%s" }\n "1" { "path" "%s" }\n}\n'
        % (home / ".local/share/Steam", lib))
    assert find_cs2_cfg_dir(home) == (lib / CS2_CFG_SUBDIR).resolve()
    assert CFG_NAME.startswith("gamestate_integration_")


def test_planted_pulse_fits_cs2_bomb_timer():
    from test_effects import make

    async def pulse_end(game):
        fx, ha, cfg, _ = make(lights=("light.room",))
        cfg["game"] = game
        await fx.handle(Kind.PLANTED)
        await fx.wait()
        return ha.sent[-1][0]

    val, cs2 = asyncio.run(pulse_end("valorant")), asyncio.run(pulse_end("cs2"))
    assert 44 <= val <= 49 and abs(cs2 - val * 40 / 45) < 1.5
