"""Pictures for the Reels picture/video inserts: photos are read once
with Pillow, videos are decoded by a long-running ffmpeg process per
(file, size) that streams frames in order - moving forward costs one
frame, jumping back or far ahead restarts it at the new place. Only
ffmpeg and Pillow, like the rest of the editor.

The live preview uses the shared DEFAULT_FRAMES; an export makes its own
VideoFrames (its own decoders at export size) and closes it at the end."""

from __future__ import annotations

import shutil
import subprocess
import threading
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path

from PIL import Image

FPS = 30
_MAX_DECODERS = 6
_FORWARD_READ_LIMIT = 90
"""Jumping further ahead than this many frames restarts the decoder."""


@lru_cache(maxsize=32)
def _load_image(path: str, mtime: float) -> Image.Image | None:
    try:
        with Image.open(path) as opened:
            opened.seek(0)
            return opened.convert("RGBA")
    except (OSError, ValueError):
        return None


def load_image(path: str) -> Image.Image | None:
    """The photo at `path` (first frame of a GIF), or None if unreadable."""
    try:
        mtime = Path(path).stat().st_mtime
    except OSError:
        return None
    return _load_image(path, mtime)


def media_aspect_ratio(path: str, kind: str) -> float:
    """Width / height of a photo or video (1.0 if it can't be read)."""
    if kind == "image":
        image = load_image(path)
        return image.width / image.height if image is not None and image.height else 1.0
    try:
        from jarvis.video_studio.ffmpeg_utils import probe_video

        probe = probe_video(Path(path))
        if probe.width and probe.height:
            return probe.width / probe.height
    except Exception:  # noqa: BLE001 - an unreadable file just gets a square frame
        pass
    return 1.0


def media_duration(path: str) -> float | None:
    try:
        from jarvis.video_studio.ffmpeg_utils import probe_video

        return probe_video(Path(path)).duration_seconds
    except Exception:  # noqa: BLE001
        return None


class _Decoder:
    def __init__(self, path: str, width: int, height: int) -> None:
        self.path, self.width, self.height = path, width, height
        self._proc: subprocess.Popen | None = None
        self._next_index = 0
        self._last: Image.Image | None = None
        self._last_index = -1
        self._ended = False

    def _start(self, index: int) -> None:
        self.close()
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            self._ended = True
            return
        self._proc = subprocess.Popen(
            [ffmpeg, "-loglevel", "error", "-ss", f"{index / FPS:.3f}", "-i", self.path, "-an",
             "-vf", f"fps={FPS},scale={self.width}:{self.height}:flags=bicubic", "-f", "rawvideo",
             "-pix_fmt", "rgb24", "pipe:1"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
        )
        self._next_index = index
        self._ended = False

    def frame(self, t: float) -> Image.Image | None:
        index = max(0, round(t * FPS))
        if index == self._last_index and self._last is not None:
            return self._last
        if self._proc is None or index < self._next_index or index > self._next_index + _FORWARD_READ_LIMIT:
            if not (self._ended and index >= self._next_index and self._last is not None):
                self._start(index)
        size = self.width * self.height * 3
        while not self._ended and self._next_index <= index and self._proc is not None:
            data = _read_exactly(self._proc.stdout, size)
            if data is None:
                self._ended = True  # past the end of the video: keep showing its last frame
                break
            self._last = Image.frombytes("RGB", (self.width, self.height), data)
            self._next_index += 1
        self._last_index = index
        return self._last

    def close(self) -> None:
        if self._proc is not None:
            try:
                self._proc.kill()
                self._proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            if self._proc.stdout is not None:
                self._proc.stdout.close()
            self._proc = None


def _read_exactly(stream, size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class VideoFrames:
    """Video frames at a given size, by source time."""

    def __init__(self) -> None:
        self._decoders: OrderedDict[tuple, _Decoder] = OrderedDict()
        self._lock = threading.Lock()

    def frame(self, path: str, t: float, width: int, height: int) -> Image.Image | None:
        key = (path, width, height)
        with self._lock:
            decoder = self._decoders.pop(key, None) or _Decoder(path, width, height)
            self._decoders[key] = decoder
            while len(self._decoders) > _MAX_DECODERS:
                _key, oldest = self._decoders.popitem(last=False)
                oldest.close()
            return decoder.frame(t)

    def close(self) -> None:
        with self._lock:
            for decoder in self._decoders.values():
                decoder.close()
            self._decoders.clear()


DEFAULT_FRAMES = VideoFrames()
