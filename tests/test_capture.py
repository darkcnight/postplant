import shutil
import time

import numpy as np
import pytest

from postplant.capture import FramePipe, frame_stride, host_env

needs_gst = pytest.mark.skipif(not shutil.which("gst-launch-1.0"), reason="no GStreamer")


def test_stride_pads_rows_to_four_bytes():
    assert frame_stride(2560) == 7680 and frame_stride(322) == 968


def test_host_env_drops_bundle_paths(monkeypatch):
    monkeypatch.setenv("LD_LIBRARY_PATH", "/tmp/.mount_x/usr/lib")
    monkeypatch.setenv("PYTHONHOME", "/tmp/.mount_x/usr")
    monkeypatch.setenv("APPIMAGE_ORIGINAL_LD_LIBRARY_PATH", "/opt/mine")
    env = host_env()
    assert env["LD_LIBRARY_PATH"] == "/opt/mine" and "PYTHONHOME" not in env


@needs_gst
def test_frames_arrive_with_odd_width_and_rate_limit():
    pipe = FramePipe(["videotestsrc", "is-live=true", "pattern=solid-color", "foreground-color=0xff0000ff",
                      "!", "video/x-raw,width=322,height=100,framerate=30/1"], max_fps=5)
    try:
        pipe.wait_started(10)
        assert pipe.size == (322, 100)
        seq, frames, t0 = 0, 0, time.monotonic()
        while time.monotonic() - t0 < 1.5:
            new, frame = pipe.latest(seq, 0.5)
            if frame is not None:
                seq, frames = new, frames + 1
                assert frame.shape == (100, 322, 3)
                assert np.all(frame[50, 10] == [255, 0, 0])      # BGR blue
        assert 4 <= frames <= 10                                   # ~5 fps, not 30
    finally:
        pipe.close()
