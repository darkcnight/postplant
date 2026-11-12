"""Capture one window (Moonlight) on Wayland via xdg-desktop-portal ScreenCast + PipeWire.

Uses jeepney (blocking D-Bus, no GLib main loop) so it can run in a worker thread
without fighting Qt's event loop. KDE remembers the picked window through a restore
token, so the picker dialog only appears the first time or on request.
"""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

import numpy as np
from jeepney import DBusAddress, MatchRule, MessageType, message_bus, new_method_call
from jeepney.io.blocking import Proxy, open_dbus_connection

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

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


class PortalCapture:
    def __init__(self, token_path: Path):
        self.token_path = token_path
        self._conn = None
        self._session: str | None = None
        self._pipeline = None
        self._sink = None
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
        Gst.init(None)
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
            self._pipeline = Gst.parse_launch(
                f"pipewiresrc fd={self._fd} path={node} always-copy=true ! videoconvert ! "
                "video/x-raw,format=BGR ! appsink name=sink max-buffers=1 drop=true sync=false")
            self._sink = self._pipeline.get_by_name("sink")
            if self._pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise CaptureError("GStreamer pipeline failed to start")
            log.info("Capturing PipeWire node %s", node)
        except Exception:
            self.close()
            raise

    # ------------------------------------------------------------------ frames
    def read(self, timeout_s: float = 0.5) -> np.ndarray | None:
        if self._sink is None:
            return None
        sample = self._sink.emit("try-pull-sample", int(timeout_s * Gst.SECOND))
        if sample is None:
            return None
        caps = sample.get_caps().get_structure(0)
        w, h = caps.get_value("width"), caps.get_value("height")
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            return None
        try:
            stride = len(info.data) // h
            return (np.frombuffer(info.data, np.uint8).reshape(h, stride)[:, : w * 3]
                    .reshape(h, w, 3).copy())
        finally:
            buf.unmap(info)

    def close(self) -> None:
        if self._pipeline is not None:
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = self._sink = None
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
