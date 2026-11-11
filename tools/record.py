"""Footage recorder: capture the Moonlight window via the portal (restore token,
no dialog) and save reference footage for building the spike detector.

- hud/<t>.jpg   : top-centre HUD strip of the 16:9 game area, full res, 4 fps
- full/<t>.jpg  : whole game area downscaled to 1280 wide, 1 fps
Runs until killed or MAX_SECONDS.
"""
import sys, time, random, string
from pathlib import Path

import cv2, dbus, numpy as np
from dbus.mainloop.glib import DBusGMainLoop
import gi
gi.require_version("Gst", "1.0")
from gi.repository import GLib, Gst

HERE = Path(__file__).resolve().parents[1] / ".state"
OUT = Path(sys.argv[1])
MAX_SECONDS = 3 * 3600
(OUT / "hud").mkdir(parents=True, exist_ok=True)
(OUT / "full").mkdir(parents=True, exist_ok=True)

DBusGMainLoop(set_as_default=True)
Gst.init(None)
bus = dbus.SessionBus()
sc = dbus.Interface(bus.get_object("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop"),
                    "org.freedesktop.portal.ScreenCast")
sender = bus.get_unique_name()[1:].replace(".", "_")
loop = GLib.MainLoop()
tok = lambda: "sp_" + "".join(random.choices(string.ascii_lowercase, k=8))


def request(method, *args, opts):
    t = tok(); opts["handle_token"] = t; r = {}
    path = f"/org/freedesktop/portal/desktop/request/{sender}/{t}"
    m = bus.add_signal_receiver(lambda c, res: (r.update(code=int(c), res=res), loop.quit()),
                                "Response", "org.freedesktop.portal.Request", path=path)
    getattr(sc, method)(*args, opts); loop.run(); m.remove()
    if r["code"]: sys.exit(f"{method} failed (code {r['code']})")
    return r["res"]


session = request("CreateSession", opts={"session_handle_token": tok()})["session_handle"]
sel = {"types": dbus.UInt32(2), "multiple": False, "cursor_mode": dbus.UInt32(1), "persist_mode": dbus.UInt32(2)}
tf = HERE / "portal_token"
if tf.exists(): sel["restore_token"] = tf.read_text().strip()
request("SelectSources", session, opts=sel)
res = request("Start", session, "", opts={})
if "restore_token" in res: tf.write_text(str(res["restore_token"]))
node = int(res["streams"][0][0])
fd = sc.OpenPipeWireRemote(session, dbus.Dictionary({}, signature="sv")).take()
pipe = Gst.parse_launch(f"pipewiresrc fd={fd} path={node} always-copy=true ! videoconvert ! "
                        "video/x-raw,format=BGR ! appsink name=sink max-buffers=1 drop=true sync=false")
sink = pipe.get_by_name("sink"); pipe.set_state(Gst.State.PLAYING)
print("recording to", OUT, flush=True)


def game_box(f):
    """16:9 game area inside the window (strip black bars)."""
    h, w = f.shape[:2]
    cols = np.where(f.max(axis=(0, 2)) > 12)[0]
    rows = np.where(f.max(axis=(1, 2)) > 12)[0]
    if len(cols) == 0 or len(rows) == 0: return 0, 0, w, h
    return cols[0], rows[0], cols[-1] + 1, rows[-1] + 1


t0 = time.monotonic(); next_hud = next_full = next_box = 0.0; box = None; n = 0
while time.monotonic() - t0 < MAX_SECONDS:
    s = sink.emit("try-pull-sample", 500 * Gst.MSECOND)
    if s is None: continue
    now = time.monotonic() - t0
    if now < next_hud: continue
    st = s.get_caps().get_structure(0); w, h = st.get_value("width"), st.get_value("height")
    buf = s.get_buffer(); ok, info = buf.map(Gst.MapFlags.READ)
    f = np.frombuffer(info.data, np.uint8).reshape(h, len(info.data) // h)[:, :w * 3].reshape(h, w, 3).copy()
    buf.unmap(info)
    if box is None or now >= next_box:
        box = game_box(f); next_box = now + 5
    x0, y0, x1, y1 = box; g = f[y0:y1, x0:x1]; gh, gw = g.shape[:2]
    ts = f"{time.time():.2f}"
    hud = g[: int(gh * 0.14), int(gw * 0.30): int(gw * 0.70)]
    cv2.imwrite(str(OUT / "hud" / f"{ts}.jpg"), hud, [cv2.IMWRITE_JPEG_QUALITY, 90])
    next_hud = now + 0.25
    if now >= next_full:
        cv2.imwrite(str(OUT / "full" / f"{ts}.jpg"), cv2.resize(g, (1280, int(1280 * gh / gw))),
                    [cv2.IMWRITE_JPEG_QUALITY, 85])
        next_full = now + 1.0
    n += 1
    if n % 2400 == 0: print(f"{now/60:.0f} min, {n} hud frames", flush=True)
pipe.set_state(Gst.State.NULL)
