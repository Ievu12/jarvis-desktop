"""Real-time playback for the Video Editor's live preview - plays the
assembled timeline at its real speed, at a reduced preview resolution,
without any new compiled dependency (same ffmpeg-subprocess + Pillow
rule as the rest of this package - see jarvis.video_editor's own
docstring for the Windows Smart App Control history behind it).

How: one long-running `ffmpeg` per timeline segment decodes that
segment straight into raw RGB frames on stdout, through the SAME
per-segment chain the export builds (multisource_export's
scale/pad/fps normalization + effects.build_segment_effect_filter()),
just at preview size. A reader thread turns the byte stream into PIL
images; PlaybackEngine.tick(), called by the GUI ~60 times a second,
hands back whichever frame belongs to the current wall-clock position.
Overlays (text, stickers, captions) are NOT decoded here - they are
drawn on top of these frames by jarvis.video_editor.preview_compositor,
which is what makes overlay edits instant.

Measured on a 1080x1920 H.264 source: ffmpeg decodes + scales to
360x640 at ~120 frames/s, four times faster than playback needs, and
starting a segment mid-way is near-instant because clips are seeked on
the INPUT side (jumping to the nearest keyframe), unlike the single-
frame export render (live_preview.render_preview_frame()), which must
run the whole filtergraph from t=0 and gets slower the further into the
timeline it renders (0.5 s at 1 s, 2.5 s at 28 s).

Known approximations, all of which the paused "exact frame"
(live_preview) shows correctly:
  - crossfade/slide transitions are shown as hard cuts while playing;
  - audio (played by `ffplay`, if installed) can drift by ~0.1 s.
"""

from __future__ import annotations

import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image

from jarvis.video_editor.effects import build_segment_effect_filter
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.multisource_export import ExportFormat, _scale_pad_filter
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineItem

PREVIEW_FPS = 30
PREVIEW_MAX_SIDE = 640
# The longer side of the decoded preview frame - 360x640 for a 9:16
# Reel. Big enough to judge an edit on screen, small enough that
# decoding stays several times faster than real time.

_FRAME_QUEUE_SIZE = 12
_SINGLE_FRAME_TIMEOUT_SECONDS = 20

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
# Without this, every ffmpeg/ffplay started from the windowed (no
# console) app flashes a console window on Windows.


class PlaybackError(Exception):
    """Raised when a preview frame can't be decoded - always with a
    human-readable message."""


def preview_size(export_format: ExportFormat, *, max_side: int = PREVIEW_MAX_SIDE) -> tuple[int, int]:
    """The decoded preview frame size for `export_format`: same aspect
    ratio, longer side `max_side`, both sides even (yuv420p needs it)."""
    ratio = max_side / max(export_format.width, export_format.height)
    width = max(2, round(export_format.width * ratio / 2) * 2)
    height = max(2, round(export_format.height * ratio / 2) * 2)
    return width, height


@dataclass(frozen=True)
class TimelineSegment:
    """One timeline item placed on the assembled timeline."""

    index: int
    item: TimelineItem
    media: MediaItem
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def timeline_segments(timeline: Timeline, media_items: dict[str, MediaItem]) -> list[TimelineSegment]:
    """Where each item sits on the ASSEMBLED timeline. A crossfade/
    slide transition overlaps two items by its duration, so the next
    item starts that much earlier - the same offset arithmetic
    multisource_export._apply_crossfades() uses for xfade's `offset=`.
    Items whose media is missing are skipped."""
    segments: list[TimelineSegment] = []
    cursor = 0.0
    last = len(timeline.items) - 1
    for n, item in enumerate(timeline.items):
        duration = item.on_screen_duration_seconds
        media = media_items.get(item.media_item_id)
        if media is not None and duration > 0:
            segments.append(TimelineSegment(n, item, media, cursor, cursor + duration))
        cursor += duration
        if n < last and item.transition_out.kind != "cut":
            cursor -= item.transition_out.duration_seconds
    return segments


def assembled_duration(segments: list[TimelineSegment]) -> float:
    return max((s.end_seconds for s in segments), default=0.0)


def segment_at(segments: list[TimelineSegment], t: float) -> TimelineSegment | None:
    """The segment showing at timeline time `t` - during a transition
    overlap, the incoming one (playback shows transitions as cuts)."""
    found = None
    for segment in segments:
        if segment.start_seconds <= t:
            found = segment
        else:
            break
    if found is None and segments:
        return segments[0]
    return found


def _effect_is_time_dependent(item: TimelineItem) -> bool:
    return item.effect.motion != "none" or item.effect.fade != "none"


def build_decode_command(
    ffmpeg: str, segment: TimelineSegment, *, local_seconds: float, width: int, height: int,
    fps: int = PREVIEW_FPS, frame_count: int | None = None,
) -> list[str]:
    """The ffmpeg command that decodes `segment` from `local_seconds`
    (seconds into the segment) as raw RGB24 frames on stdout.

    A motion (zoom/pan) or fade effect depends on time since the
    segment's start, so those segments are decoded from their start
    and the output is seeked (`-ss` after the filters) - correct, and
    still fast at preview size. Everything else seeks the input
    directly, which costs nothing."""
    item = segment.item
    local_seconds = max(0.0, min(local_seconds, segment.duration_seconds))
    time_dependent = _effect_is_time_dependent(item)
    input_offset = 0.0 if time_dependent else local_seconds
    path = str(segment.media.stored_path)

    if isinstance(item, TimelineClip):
        source_start = item.source_in_seconds + input_offset * item.speed_factor
        source_length = max(0.01, item.source_out_seconds - source_start)
        input_args = ["-ss", f"{source_start:.3f}", "-t", f"{source_length:.3f}", "-i", path]
        speed = f",setpts=PTS/{item.speed_factor}" if item.speed_factor != 1.0 else ""
        chain = f"[0:v]setpts=PTS-STARTPTS{speed},{_scale_pad_filter(width, height, fps=fps)}[raw]"
    else:  # TimelineStill
        still_length = max(0.01, item.display_duration_seconds - input_offset)
        input_args = ["-loop", "1", "-t", f"{still_length:.3f}", "-i", path]
        chain = f"[0:v]{_scale_pad_filter(width, height, fps=fps)}[raw]"

    has_effect = not item.effect.is_identity
    if has_effect:
        chain += ";" + build_segment_effect_filter(
            item.effect, width=width, height=height, duration_seconds=item.on_screen_duration_seconds,
            fps=fps, video_label="[raw]", output_label="out",
        )
    else:
        chain = chain[: -len("[raw]")] + "[out]"

    output_args = ["-ss", f"{local_seconds:.3f}"] if time_dependent and local_seconds > 0 else []
    if frame_count is not None:
        output_args += ["-frames:v", str(frame_count)]
    return [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", *input_args,
        "-filter_complex", chain, "-map", "[out]", *output_args,
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]


class FrameStream:
    """One running ffmpeg decode of one segment, read on a background
    thread into a small bounded queue (bounded so a fast decoder can
    never run ahead and fill memory - it simply blocks on the pipe)."""

    def __init__(
        self, command: list[str], *, width: int, height: int, first_timestamp: float, fps: int = PREVIEW_FPS,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
    ) -> None:
        self._width, self._height = width, height
        self._first_timestamp = first_timestamp
        self._fps = fps
        self._frames: "queue.Queue[tuple[float, Image.Image] | None]" = queue.Queue(maxsize=_FRAME_QUEUE_SIZE)
        self._stopped = threading.Event()
        self._peeked: tuple[float, Image.Image] | None = None
        self.finished = False
        self._process = popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
        )
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def _read_loop(self) -> None:
        frame_bytes = self._width * self._height * 3
        stdout = self._process.stdout
        n = 0
        try:
            while not self._stopped.is_set():
                data = _read_exactly(stdout, frame_bytes)
                if data is None:
                    break
                image = Image.frombytes("RGB", (self._width, self._height), data)
                timestamp = self._first_timestamp + n / self._fps
                n += 1
                while not self._stopped.is_set():
                    try:
                        self._frames.put((timestamp, image), timeout=0.1)
                        break
                    except queue.Full:
                        continue
        finally:
            while not self._stopped.is_set():
                try:
                    self._frames.put(None, timeout=0.1)
                    break
                except queue.Full:
                    continue

    def frame_for(self, t: float) -> Image.Image | None:
        """The newest decoded frame whose timestamp is <= `t` (older
        ones are dropped - when the decoder falls behind, playback skips
        frames rather than slowing down). None if no frame for `t` has
        been decoded yet."""
        latest = None
        while not self.finished:
            if self._peeked is None:
                try:
                    item = self._frames.get_nowait()
                except queue.Empty:
                    break
                if item is None:  # end-of-stream marker
                    self.finished = True
                    break
                self._peeked = item
            timestamp, image = self._peeked
            if timestamp > t + 0.5 / self._fps:
                break  # not due yet - keep it for a later tick
            latest = image
            self._peeked = None
        return latest

    def stop(self) -> None:
        self._stopped.set()
        try:
            self._process.kill()
        except OSError:
            pass
        try:
            self._process.wait(timeout=2)
        except (subprocess.TimeoutExpired, OSError):
            pass


def _read_exactly(stream, size: int) -> bytes | None:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def decode_single_frame(
    timeline: Timeline, media_items: dict[str, MediaItem], *, t: float, width: int, height: int,
    ffmpeg: str | None = None,
) -> Image.Image:
    """One base frame (no overlays) at timeline time `t` - what a
    paused preview shows while scrubbing. Blocking (~0.1-0.3 s): call
    it from a background thread."""
    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if ffmpeg is None:
        raise PlaybackError("FFmpeg was not found on PATH.")
    segments = timeline_segments(timeline, media_items)
    showing = segments_showing(segments, t)
    if not showing:
        raise PlaybackError("The timeline has nothing to show yet.")
    frames = [_decode_one(ffmpeg, segment, t=t, width=width, height=height) for segment in showing]
    if len(frames) == 1:
        return frames[0]
    outgoing, incoming = showing
    return transition_frame(
        frames[0], frames[1], outgoing.item.transition_out.kind, transition_progress(outgoing, incoming, t),
    )


def _decode_one(ffmpeg: str, segment: TimelineSegment, *, t: float, width: int, height: int) -> Image.Image:
    local = min(max(0.0, t - segment.start_seconds), max(0.0, segment.duration_seconds - 1 / PREVIEW_FPS))
    command = build_decode_command(ffmpeg, segment, local_seconds=local, width=width, height=height, frame_count=1)
    try:
        result = subprocess.run(
            command, capture_output=True, timeout=_SINGLE_FRAME_TIMEOUT_SECONDS, check=False,
            stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise PlaybackError("Decoding the preview frame timed out.") from None
    expected = width * height * 3
    if result.returncode != 0 or len(result.stdout) < expected:
        message = result.stderr.decode("utf-8", "replace").strip()[-300:]
        raise PlaybackError(f"Couldn't decode the preview frame. {message}".strip())
    return Image.frombytes("RGB", (width, height), result.stdout[:expected])


def render_look_previews(
    image: Image.Image, looks: tuple[str, ...], *, intensity: float = 1.0, ffmpeg: str | None = None,
) -> dict[str, Image.Image]:
    """`image` graded by each filter look in `looks` (the Filters
    library's thumbnails), through the same `build_segment_effect_filter`
    the export uses. Blocking: call it from a background thread. A look
    that fails to render is left out of the result."""
    from jarvis.video_editor.effects import EffectSpec

    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if ffmpeg is None:
        raise PlaybackError("FFmpeg was not found on PATH.")
    image = image.convert("RGB")
    width, height = image.size
    raw = image.tobytes()
    previews: dict[str, Image.Image] = {}
    for look in looks:
        if look == "none":
            previews[look] = image.copy()
            continue
        graph = build_segment_effect_filter(
            EffectSpec(look=look, look_intensity=intensity), width=width, height=height,
            duration_seconds=1.0, fps=PREVIEW_FPS, video_label="[0:v]", output_label="out",
        )
        command = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{width}x{height}", "-i", "-", "-filter_complex", graph, "-map", "[out]",
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ]
        try:
            result = subprocess.run(
                command, input=raw, capture_output=True, timeout=_SINGLE_FRAME_TIMEOUT_SECONDS, check=False,
                creationflags=_NO_WINDOW,
            )
        except subprocess.TimeoutExpired:
            continue
        if result.returncode == 0 and len(result.stdout) >= len(raw):
            previews[look] = Image.frombytes("RGB", (width, height), result.stdout[:len(raw)])
    return previews


class AudioPlayer:
    """Plays a prepared audio file with `ffplay` (shipped with ffmpeg,
    no window, no new dependency) from a given position. Silently does
    nothing when ffplay isn't installed - `available` tells the GUI so
    it can say why there's no sound."""

    def __init__(self, ffplay: str | None = None, *, popen: Callable[..., subprocess.Popen] = subprocess.Popen) -> None:
        self._ffplay = ffplay if ffplay is not None else shutil.which("ffplay")
        self._popen = popen
        self._process: subprocess.Popen | None = None

    @property
    def available(self) -> bool:
        return self._ffplay is not None

    def play(self, audio_path: Path, *, from_seconds: float) -> None:
        self.stop()
        if self._ffplay is None or not audio_path.is_file():
            return
        self._process = self._popen(
            [self._ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", "-ss", f"{max(0.0, from_seconds):.3f}",
             str(audio_path)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
        )

    def stop(self) -> None:
        if self._process is not None:
            try:
                self._process.kill()
            except OSError:
                pass
            self._process = None


def render_preview_audio(
    timeline: Timeline, media_items: dict[str, MediaItem], *, output_path: Path, cwd: Path,
    audio_mix_builder: Callable[[int, float], tuple[list[str], str, str] | None] | None = None,
    ffmpeg: str | None = None,
) -> Path:
    """Renders the assembled timeline's audio (every clip, transitions'
    acrossfades, optional music mix) to a WAV for AudioPlayer, by
    running the export's own filtergraph with a tiny 16x16 video canvas
    so the video side costs almost nothing. Blocking - run it in the
    background whenever the timeline or music changes.

    `audio_mix_builder(distinct_input_count, duration)` returns the
    music `audio_mix_filter` tuple export_timeline() takes, or None."""
    from jarvis.video_editor.multisource_export import ExportFormat as _ExportFormat
    from jarvis.video_editor.multisource_export import build_filtergraph

    ffmpeg = ffmpeg or shutil.which("ffmpeg")
    if ffmpeg is None:
        raise PlaybackError("FFmpeg was not found on PATH.")
    tiny = _ExportFormat(aspect_ratio=timeline.aspect_ratio, width=16, height=16, label="audio-only")
    input_args, full_filter, video_out, audio_out = build_filtergraph(timeline, media_items, tiny, cwd=cwd)
    stages = [full_filter, f"[{video_out}]nullsink"]
    if audio_mix_builder is not None:
        distinct_inputs = len({item.media_item_id for item in timeline.items})
        duration = assembled_duration(timeline_segments(timeline, media_items))
        mix = audio_mix_builder(distinct_inputs, duration)
        if mix is not None:
            extra_args, clause, mixed_label = mix
            input_args = input_args + extra_args
            stages.append(clause)
            audio_out = mixed_label
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-nostdin", *input_args,
        "-filter_complex", ";".join(stages), "-map", f"[{audio_out}]", "-ac", "2", "-ar", "44100", str(output_path),
    ]
    result = subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True, timeout=600, check=False,
        stdin=subprocess.DEVNULL, creationflags=_NO_WINDOW,
    )
    if result.returncode != 0 or not output_path.is_file():
        raise PlaybackError(f"Couldn't prepare preview audio. {result.stderr.strip()[-300:]}")
    return output_path


class PlaybackEngine:
    """Play/pause/seek state for one timeline. GUI-agnostic: the owner
    calls tick() on a timer and paints whatever frame it returns.

    One decode stream per segment that is on screen (two during a
    transition, blended like the export's xfade) plus the next segment,
    opened shortly before it's needed.

    `clock` and `stream_factory` are injectable so tests can drive it
    deterministically without real time or real ffmpeg."""

    def __init__(
        self, timeline: Timeline, media_items: dict[str, MediaItem], *, frame_size: tuple[int, int],
        ffmpeg: str | None = None, audio: AudioPlayer | None = None,
        clock: Callable[[], float] = time.monotonic,
        stream_factory: Callable[..., FrameStream] = FrameStream,
    ) -> None:
        self._ffmpeg = ffmpeg or shutil.which("ffmpeg") or "ffmpeg"
        self._width, self._height = frame_size
        self._audio = audio
        self._audio_path: Path | None = None
        self._clock = clock
        self._stream_factory = stream_factory
        self._segments: list[TimelineSegment] = []
        self.duration = 0.0
        self._position = 0.0
        self.playing = False
        self._play_started_at = 0.0
        self._play_started_position = 0.0
        self._streams: dict[int, FrameStream] = {}
        self._last_frames: dict[int, Image.Image] = {}
        self.set_timeline(timeline, media_items)

    @property
    def frame_size(self) -> tuple[int, int]:
        return self._width, self._height

    @property
    def position(self) -> float:
        if self.playing:
            return min(self.duration, self._play_started_position + (self._clock() - self._play_started_at))
        return self._position

    def set_timeline(self, timeline: Timeline, media_items: dict[str, MediaItem]) -> None:
        """Called after every timeline edit. Keeps the position (clamped
        into the new length) and, if playing, restarts decoding there."""
        was_playing = self.playing
        position = self.position
        self._stop_streams()
        self._segments = timeline_segments(timeline, media_items)
        self.duration = assembled_duration(self._segments)
        self._position = max(0.0, min(position, self.duration))
        self.playing = False
        if was_playing and self.duration > 0:
            self.play()

    def set_audio_file(self, audio_path: Path | None) -> None:
        """The prepared WAV (render_preview_audio()) to play along, or
        None while it's being (re)built."""
        self._audio_path = audio_path
        if self.playing and self._audio is not None:
            if audio_path is None:
                self._audio.stop()
            else:
                self._audio.play(audio_path, from_seconds=self.position)

    def play(self) -> None:
        if self.duration <= 0:
            return
        if self._position >= self.duration - 1 / PREVIEW_FPS:
            self._position = 0.0  # play again from the start after reaching the end
        self.playing = True
        self._play_started_position = self._position
        self._sync_streams(self._position)
        if self._audio is not None and self._audio_path is not None:
            self._audio.play(self._audio_path, from_seconds=self._position)
        self._play_started_at = self._clock()

    def pause(self) -> None:
        if not self.playing:
            return
        self._position = self.position
        self.playing = False
        self._stop_streams()
        if self._audio is not None:
            self._audio.stop()

    def toggle(self) -> None:
        self.pause() if self.playing else self.play()

    def seek(self, t: float) -> None:
        was_playing = self.playing
        if was_playing:
            self.pause()
        self._position = max(0.0, min(t, self.duration))
        if was_playing:
            self.play()

    def tick(self) -> Image.Image | None:
        """Advances playback to the wall clock. Returns the frame to show
        now, or None when there's nothing new (keep the current one)."""
        if not self.playing:
            return None
        t = self.position
        if t >= self.duration:
            self._position = self.duration
            self.playing = False
            self._stop_streams()
            if self._audio is not None:
                self._audio.stop()
            return None

        showing = self._sync_streams(t)
        updated = False
        for segment in showing:
            stream = self._streams.get(segment.index)
            frame = stream.frame_for(t) if stream is not None else None
            if frame is not None:
                self._last_frames[segment.index] = frame
                updated = True
        if not updated:
            return None
        if len(showing) == 1:
            return self._last_frames.get(showing[0].index)
        outgoing, incoming = showing[0], showing[1]
        first, second = self._last_frames.get(outgoing.index), self._last_frames.get(incoming.index)
        if first is None or second is None:
            return second or first
        return transition_frame(first, second, outgoing.item.transition_out.kind, transition_progress(outgoing, incoming, t))

    def close(self) -> None:
        self.pause()
        self._stop_streams()

    # --- internals ------------------------------------------------------------------------

    def _sync_streams(self, t: float) -> list[TimelineSegment]:
        """Opens decode streams for the segments on screen at `t` (and
        the next one when it's close), closes the rest. Returns the
        segments on screen, outgoing first."""
        showing = segments_showing(self._segments, t)
        wanted = {segment.index: segment for segment in showing}
        for segment in self._segments:
            if t < segment.start_seconds <= t + 0.75:
                wanted.setdefault(segment.index, segment)
        for index in [i for i in self._streams if i not in wanted]:
            self._streams.pop(index).stop()
            self._last_frames.pop(index, None)
        for index, segment in wanted.items():
            if index not in self._streams:
                local = max(0.0, t - segment.start_seconds)
                self._streams[index] = self._open_stream(segment, local_seconds=local)
        return showing

    def _open_stream(self, segment: TimelineSegment, *, local_seconds: float) -> FrameStream:
        command = build_decode_command(
            self._ffmpeg, segment, local_seconds=local_seconds, width=self._width, height=self._height,
        )
        return self._stream_factory(
            command, width=self._width, height=self._height,
            first_timestamp=segment.start_seconds + local_seconds,
        )

    def _stop_streams(self) -> None:
        for stream in self._streams.values():
            stream.stop()
        self._streams.clear()
        self._last_frames.clear()


# --- transitions -------------------------------------------------------------------------------


def segments_showing(segments: list[TimelineSegment], t: float) -> list[TimelineSegment]:
    """The one segment on screen at `t`, or the outgoing and incoming
    pair during a transition overlap."""
    showing = [s for s in segments if s.start_seconds <= t < s.end_seconds]
    if not showing:
        last = segment_at(segments, t)
        return [last] if last is not None else []
    return showing[-2:]


def transition_progress(outgoing: TimelineSegment, incoming: TimelineSegment, t: float) -> float:
    overlap = outgoing.end_seconds - incoming.start_seconds
    if overlap <= 0:
        return 1.0
    return max(0.0, min(1.0, (t - incoming.start_seconds) / overlap))


_DISSOLVE_NOISE: dict[tuple[int, int], Image.Image] = {}


def _dissolve_noise(size: tuple[int, int]) -> Image.Image:
    noise = _DISSOLVE_NOISE.get(size)
    if noise is None:
        import random

        rng = random.Random(1234)
        noise = Image.frombytes("L", size, bytes(rng.randrange(256) for _ in range(size[0] * size[1])))
        _DISSOLVE_NOISE[size] = noise
    return noise


def transition_frame(first: Image.Image, second: Image.Image, kind: str, progress: float) -> Image.Image:
    """`first` turning into `second`, `progress` of the way through,
    mirroring ffmpeg xfade's fade/dissolve/slideleft/slideright."""
    if second.size != first.size:
        second = second.resize(first.size)
    width = first.width
    if kind == "slide_left":
        offset = round(progress * width)
        frame = Image.new("RGB", first.size)
        frame.paste(first, (-offset, 0))
        frame.paste(second, (width - offset, 0))
        return frame
    if kind == "slide_right":
        offset = round(progress * width)
        frame = Image.new("RGB", first.size)
        frame.paste(first, (offset, 0))
        frame.paste(second, (offset - width, 0))
        return frame
    if kind == "dissolve":
        mask = _dissolve_noise(first.size).point(lambda v: 255 if v < progress * 256 else 0)
        return Image.composite(second, first, mask)
    return Image.blend(first, second, progress)
