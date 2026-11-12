"""Capture one window (Moonlight) on Wayland via xdg-desktop-portal ScreenCast + PipeWire.

Uses jeepney (blocking D-Bus, no GLib main loop) so it can run in a worker thread
without fighting Qt's event loop. KDE remembers the picked window through a restore
token, so the picker dialog only appears the first time or on request.

Frames come from the system's `gst-launch-1.0` (with the PipeWire plugin) running as a
child process, not from Python GStreamer bindings, so a bundled app (AppImage) only
needs GStreamer on the host, never matching Python bindings.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from jeepney import DBusAddress, MatchRule, MessageType, message_bus, new_method_call
from jeepney.io.blocking import Proxy, open_dbus_connection

log = logging.getLogger("spike.capture")

PORTAL = DBusAddress("/org/freedesktop/portal/desktop", "org.freedesktop.portal.Desktop",
                     "org.freedesktop.portal.ScreenCast")
SOURCE_WINDOW = 2
CURSOR_HIDDEN = 1
PERSIST_UNTIL_REVOKED = 2


class CaptureError(Exception):
    pass


class CaptureCancelled(CaptureError):
    """User closed the picker, or KDE refused the saved window."""


def moonlight_running() -> bool:
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                if Path(f"/proc/{pid}/comm").read_text().strip() == "moonlight":
                    return True
            except OSError:
                pass
    return False


# Variables an AppImage sets for its own bundled libraries; the host's GStreamer must
# not see them.
_BUNDLE_ENV = ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONHOME", "PYTHONPATH", "GST_PLUGIN_PATH",
               "GST_PLUGIN_SYSTEM_PATH", "GST_PLUGIN_SCANNER", "GI_TYPELIB_PATH", "QT_PLUGIN_PATH")
_CAPS = re.compile(r"GstFdSink.*caps = video/x-raw.*?width=\(int\)(\d+).*?height=\(int\)(\d+)")


def host_env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in _BUNDLE_ENV}
    for key in _BUNDLE_ENV:     # AppImage tools keep the user's originals here
        if f"APPIMAGE_ORIGINAL_{key}" in os.environ:
            env[key] = os.environ[f"APPIMAGE_ORIGINAL_{key}"]
    return env


def check_gstreamer() -> str | None:
    """None if capture can work, else a message saying what to install."""
    if not shutil.which("gst-launch-1.0"):
        return "GStreamer isn't installed (needs gst-launch-1.0 and gst-plugin-pipewire)"
    ok = subprocess.run(["gst-inspect-1.0", "pipewiresrc"], env=host_env(),
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    return None if ok else "GStreamer's PipeWire plugin is missing (install gst-plugin-pipewire)"


def frame_stride(width: int) -> int:
    """GStreamer pads each BGR row to a multiple of 4 bytes."""
    return (width * 3 + 3) & ~3


class FramePipe:
    """gst-launch-1.0 child process writing raw BGR frames into a pipe."""

    def __init__(self, source: list[str], max_fps: int, keep_fds: tuple[int, ...] = ()):
        self._r, w = os.pipe()
        cmd = ["gst-launch-1.0", "-v", *source, "!", "videorate", f"max-rate={max(1, max_fps)}", "!",
               "videoconvert", "!", "video/x-raw,format=BGR", "!", "fdsink", f"fd={w}",
               "sync=false"]
        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                         stdin=subprocess.DEVNULL, pass_fds=(*keep_fds, w),
                                         env=host_env(), text=True)
        finally:
            os.close(w)
        self.size: tuple[int, int] | None = None     # (width, height)
        self.broken = False                           # size changed or process ended
        self.output: list[str] = []
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        threading.Thread(target=self._read_log, daemon=True, name="gst-log").start()
        threading.Thread(target=self._read_frames, daemon=True, name="gst-frames").start()

    def _read_log(self) -> None:
        for line in self.proc.stdout:
            self.output = (self.output + [line.rstrip()])[-20:]
            m = _CAPS.search(line)
            if m:
                size = (int(m.group(1)), int(m.group(2)))
                with self._cond:
                    if self.size is None:
                        self.size = size
                    elif size != self.size:
                        self.broken = True    # window resized: caller reopens
                    self._cond.notify_all()
        with self._cond:
            self.broken = True
            self._cond.notify_all()

    def _read_frames(self) -> None:
        with open(self._r, "rb", buffering=0) as f:
            with self._cond:
                self._cond.wait_for(lambda: self.size is not None or self.broken)
            if self.size is None:
                return
            w, h = self.size
            stride = frame_stride(w)
            buf = bytearray(stride * h)
            view = memoryview(buf)
            while not self.broken:
                got = 0
                while got < len(buf):
                    n = f.readinto(view[got:])
                    if not n:
                        return
                    got += n
                frame = np.frombuffer(buf, np.uint8).reshape(h, stride)[:, : w * 3]
                with self._cond:
                    self._frame = frame.reshape(h, w, 3).copy()
                    self._seq += 1
                    self._cond.notify_all()

    def wait_started(self, timeout_s: float) -> None:
        with self._cond:
            self._cond.wait_for(lambda: self.size is not None or self.broken, timeout_s)
        if self.size is None:
            tail = "; ".join(l for l in self.output[-3:] if l)
            raise CaptureError("GStreamer didn't start" + (f": {tail}" if tail else ""))

    def latest(self, after: int, timeout_s: float) -> tuple[int, np.ndarray | None]:
        with self._cond:
            self._cond.wait_for(lambda: self._seq > after or self.broken, timeout_s)
            if self.broken or self._seq <= after:
                return after, None
            return self._seq, self._frame

    def close(self) -> None:
        self.broken = True
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(2)
            except subprocess.TimeoutExpired:
                self.proc.kill()


class PortalCapture:
    def __init__(self, token_path: Path, max_fps: int = 10):
        self.token_path = token_path
        self.max_fps = max_fps
        self._conn = None
        self._session: str | None = None
        self._pipe: FramePipe | None = None
        self._seq = 0
        self._fd: int | None = None

    # ------------------------------------------------------------------ portal
    def _request(self, method: str, signature: str, *args, timeout: float = 300):
        token = "sp" + secrets.token_hex(6)
        sender = self._conn.unique_name[1:].replace(".", "_")
        path = f"/org/freedesktop/portal/desktop/request/{sender}/{token}"
        rule = MatchRule(type="signal", interface="org.freedesktop.portal.Request",
                         member="Response", path=path)
        Proxy(message_bus, self._conn).AddMatch(rule)
        with self._conn.filter(rule) as queue:
            opts = dict(args[-1])
            opts["handle_token"] = ("s", token)
            reply = self._conn.send_and_get_reply(
                new_method_call(PORTAL, method, signature, (*args[:-1], opts)))
            if reply.header.message_type is MessageType.error:
                raise CaptureError(f"{method}: {reply.body}")
            try:
                signal = self._conn.recv_until_filtered(queue, timeout=timeout)
            except TimeoutError as e:
                raise CaptureError(f"{method}: no answer from the portal") from e
        code, results = signal.body
        if code == 1:
            raise CaptureCancelled(f"{method} cancelled")
        if code != 0:
            raise CaptureError(f"{method} failed (code {code})")
        return results

    def has_token(self) -> bool:
        return self.token_path.exists() and bool(self.token_path.read_text().strip())

    def open(self, *, force_picker: bool = False) -> None:
        problem = check_gstreamer()
        if problem:
            raise CaptureError(problem)
        self._conn = open_dbus_connection(bus="SESSION", enable_fds=True)
        try:
            res = self._request("CreateSession", "a{sv}",
                                {"session_handle_token": ("s", "sp" + secrets.token_hex(6))},
                                timeout=10)
            self._session = res["session_handle"][1]
            select = {
                "types": ("u", SOURCE_WINDOW),
                "multiple": ("b", False),
                "cursor_mode": ("u", CURSOR_HIDDEN),
                "persist_mode": ("u", PERSIST_UNTIL_REVOKED),
            }
            if self.has_token() and not force_picker:
                select["restore_token"] = ("s", self.token_path.read_text().strip())
            self._request("SelectSources", "oa{sv}", self._session, select, timeout=10)
            res = self._request("Start", "osa{sv}", self._session, "", {})
            if "restore_token" in res:
                self.token_path.parent.mkdir(parents=True, exist_ok=True)
                self.token_path.write_text(res["restore_token"][1])
            streams = res.get("streams", ("", []))[1]
            if not streams:
                raise CaptureError("portal returned no stream")
            node = int(streams[0][0])
            reply = self._conn.send_and_get_reply(
                new_method_call(PORTAL, "OpenPipeWireRemote", "oa{sv}", (self._session, {})))
            if reply.header.message_type is MessageType.error:
                raise CaptureError(f"OpenPipeWireRemote: {reply.body}")
            self._fd = reply.body[0].to_raw_fd()
            source = ["pipewiresrc", f"fd={self._fd}", f"path={node}", "always-copy=true"]
            self._pipe = FramePipe(source, self.max_fps, keep_fds=(self._fd,))
            self._seq = 0
            self._pipe.wait_started(10)
            log.info("Capturing PipeWire node %s", node)
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------------ frames
    def read(self, timeout_s: float = 0.5) -> np.ndarray | None:
        if self._pipe is None or self._pipe.broken:
            time.sleep(timeout_s)       # caller reopens after a few seconds without frames
            return None
        self._seq, frame = self._pipe.latest(self._seq, timeout_s)
        return frame

    def close(self) -> None:
        if self._pipe is not None:
            self._pipe.close()
            self._pipe = None
        if self._conn is not None:
            if self._session:
                try:
                    self._conn.send(new_method_call(
                        DBusAddress(self._session, "org.freedesktop.portal.Desktop",
                                    "org.freedesktop.portal.Session"), "Close"))
                except Exception:  # noqa: BLE001 - best effort on shutdown
                    pass
            self._conn.close()
            self._conn = None
        self._session = None
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
