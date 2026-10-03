"""Tests for jarvis.video_editor.playback - the real-time preview's
segment arithmetic, play/pause/seek state machine (driven by a fake
clock and fake decode streams) and, when ffmpeg is installed, a real
single-frame decode."""

from __future__ import annotations

import shutil
import subprocess

import pytest
from PIL import Image

from jarvis.video_editor import playback
from jarvis.video_editor.effects import EffectSpec
from jarvis.video_editor.multisource_export import ExportFormat
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec


def _media(tmp_path) -> dict[str, MediaItem]:
    return {
        "v": MediaItem(media_item_id="v", original_filename="a.mp4", stored_path=tmp_path / "a.mp4", kind="video",
                       duration_seconds=10, width=1080, height=1920, fps=30),
        "p": MediaItem(media_item_id="p", original_filename="b.png", stored_path=tmp_path / "b.png", kind="photo",
                       duration_seconds=None, width=800, height=600, fps=None),
    }


def _timeline(transition: str = "cut") -> Timeline:
    return Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="v", source_in_seconds=0, source_out_seconds=4,
                     transition_out=TransitionSpec(kind=transition, duration_seconds=1.0)),
        TimelineStill(clip_id="s1", media_item_id="p", display_duration_seconds=3),
    ))


def test_preview_size_keeps_aspect_and_even_sides():
    assert playback.preview_size(ExportFormat("9:16", 1080, 1920, "Reel")) == (360, 640)
    w, h = playback.preview_size(ExportFormat("16:9", 1920, 1080, "YouTube"))
    assert (w, h) == (640, 360)


def test_segments_cut_and_crossfade(tmp_path):
    cut = playback.timeline_segments(_timeline("cut"), _media(tmp_path))
    assert [(s.start_seconds, s.end_seconds) for s in cut] == [(0, 4), (4, 7)]
    assert playback.assembled_duration(cut) == 7

    fade = playback.timeline_segments(_timeline("fade"), _media(tmp_path))
    assert [(s.start_seconds, s.end_seconds) for s in fade] == [(0, 4), (3, 6)]
    assert playback.segment_at(fade, 3.5).index == 1  # incoming item wins in the overlap
    assert playback.segment_at(fade, 0).index == 0


def test_decode_command_seeks_input_unless_effect_is_time_dependent(tmp_path):
    segments = playback.timeline_segments(_timeline(), _media(tmp_path))
    plain = playback.build_decode_command("ffmpeg", segments[0], local_seconds=2, width=360, height=640)
    assert plain[plain.index("-ss") + 1] == "2.000"
    assert plain.count("-ss") == 1

    still = TimelineStill(clip_id="s", media_item_id="p", display_duration_seconds=3,
                          effect=EffectSpec(motion="zoom_in"))
    segment = playback.TimelineSegment(0, still, _media(tmp_path)["p"], 0, 3)
    moving = playback.build_decode_command("ffmpeg", segment, local_seconds=1.5, width=360, height=640)
    # decoded from the start, output seeked - the zoom depends on time since the segment start
    assert moving[moving.index("-t") + 1] == "3.000"
    assert moving[moving.index("-map") + 2: moving.index("-map") + 4] == ["-ss", "1.500"]


class _FakeStream:
    instances: list["_FakeStream"] = []

    def __init__(self, command, *, width, height, first_timestamp):
        self.command = command
        self.first_timestamp = first_timestamp
        self.size = (width, height)
        self.stopped = False
        _FakeStream.instances.append(self)

    def frame_for(self, t):
        return Image.new("RGB", self.size, (int(t * 10) % 256, 0, 0))

    def stop(self):
        self.stopped = True


class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class _FakeAudio:
    def __init__(self):
        self.calls = []

    def play(self, path, *, from_seconds):
        self.calls.append(("play", from_seconds))

    def stop(self):
        self.calls.append(("stop",))


@pytest.fixture
def engine(tmp_path):
    _FakeStream.instances = []
    clock = _Clock()
    audio = _FakeAudio()
    eng = playback.PlaybackEngine(_timeline(), _media(tmp_path), frame_size=(360, 640), ffmpeg="ffmpeg",
                                  audio=audio, clock=clock, stream_factory=_FakeStream)
    return eng, clock, audio


def test_play_follows_the_clock_and_stops_at_the_end(engine, tmp_path):
    eng, clock, _ = engine
    assert eng.duration == 7 and not eng.playing
    eng.play()
    assert eng.playing and len(_FakeStream.instances) == 1
    clock.now += 1.5
    frame = eng.tick()
    assert frame is not None and frame.size == (360, 640)
    assert eng.position == pytest.approx(1.5)

    clock.now += 2.0  # 3.5 s: the next segment starts within 0.75 s, so it is pre-opened
    eng.tick()
    assert len(_FakeStream.instances) == 2
    assert _FakeStream.instances[1].first_timestamp == 4
    clock.now += 1.0  # 4.5 s: switches to the pre-opened stream without starting another
    eng.tick()
    assert len(_FakeStream.instances) == 2 and _FakeStream.instances[0].stopped

    clock.now += 10
    assert eng.tick() is None
    assert not eng.playing and eng.position == 7


def test_pause_seek_and_replay_from_start(engine):
    eng, clock, audio = engine
    eng.set_audio_file(None)
    eng.play()
    clock.now += 2
    eng.pause()
    assert not eng.playing and eng.position == pytest.approx(2)
    clock.now += 5
    assert eng.position == pytest.approx(2)  # paused time doesn't move

    eng.seek(5.25)
    assert eng.position == 5.25
    eng.play()
    assert _FakeStream.instances[-1].first_timestamp == pytest.approx(5.25)
    eng.seek(1)  # seeking while playing keeps playing from there
    assert eng.playing and _FakeStream.instances[-1].first_timestamp == pytest.approx(1)

    eng.seek(7)
    eng.pause()
    eng.play()  # at the end: starts over
    assert eng.position == 0


def test_audio_follows_play_and_pause(engine, tmp_path):
    eng, clock, audio = engine
    wav = tmp_path / "a.wav"
    wav.write_bytes(b"x")
    eng.set_audio_file(wav)
    eng.seek(1)
    eng.play()
    assert audio.calls[-1] == ("play", 1)
    eng.pause()
    assert audio.calls[-1] == ("stop",)


def test_editing_the_timeline_keeps_the_position(engine, tmp_path):
    eng, clock, _ = engine
    eng.seek(6)
    shorter = Timeline(items=(_timeline().items[0],))
    eng.set_timeline(shorter, _media(tmp_path))
    assert eng.duration == 4 and eng.position == 4


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_single_frame_decode(tmp_path):
    video = tmp_path / "a.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=1080x1920:r=30:d=2",
         "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    media = {"v": MediaItem(media_item_id="v", original_filename="a.mp4", stored_path=video, kind="video",
                            duration_seconds=2, width=1080, height=1920, fps=30)}
    timeline = Timeline(items=(TimelineClip(clip_id="c", media_item_id="v", source_in_seconds=0,
                                            source_out_seconds=2),))
    frame = playback.decode_single_frame(timeline, media, t=1.0, width=360, height=640)
    assert frame.size == (360, 640)
    r, g, b = frame.getpixel((180, 320))
    assert r > 200 and g < 60 and b < 60

    with pytest.raises(playback.PlaybackError):
        playback.decode_single_frame(Timeline(items=()), {}, t=0, width=360, height=640)
