"""Stage 3 model tests: filter looks and transitions as the editor
applies them (jarvis.video_editor.track_layout), the preview's own
transition blending (jarvis.video_editor.playback.transition_frame and
PlaybackEngine during an overlap) and the Filters library thumbnails."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from PIL import Image

from jarvis.video_editor import playback, track_layout
from jarvis.video_editor.editor_state import EditorState
from jarvis.video_editor.effects import LOOK_CHOICES, EffectSpec
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec

MEDIA = {
    "v": MediaItem(media_item_id="v", original_filename="klipas.mp4", stored_path=Path("a.mp4"), kind="video",
                   duration_seconds=10, width=1080, height=1920, fps=30),
    "p": MediaItem(media_item_id="p", original_filename="foto.jpg", stored_path=Path("b.jpg"), kind="photo",
                   duration_seconds=None, width=800, height=600, fps=None),
}


def _state(*, still_seconds: float = 3.0, transition: TransitionSpec = TransitionSpec()) -> EditorState:
    return EditorState(timeline=Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="v", source_in_seconds=0, source_out_seconds=4,
                     transition_out=transition, effect=EffectSpec(brightness=0.1)),
        TimelineStill(clip_id="s1", media_item_id="p", display_duration_seconds=still_seconds),
        TimelineStill(clip_id="s2", media_item_id="p", display_duration_seconds=2),
    )))


# --- transitions ------------------------------------------------------------------------------------


def test_transition_length_is_limited_by_both_clips():
    state = _state(still_seconds=1.0)
    assert track_layout.max_transition_seconds(state, 0) == pytest.approx(0.9)
    assert track_layout.max_transition_seconds(state, 2) == 0.0  # the last clip
    assert track_layout.max_transition_seconds(_state(still_seconds=8), 0) == track_layout.MAX_TRANSITION_SECONDS


def test_set_transition_clamps_and_cut_clears_it():
    state = track_layout.set_transition(_state(still_seconds=1.0), 0, "fade", 1.5)
    assert state.timeline.items[0].transition_out == TransitionSpec("fade", 0.9)
    assert state.timeline.validate() == []

    cleared = track_layout.set_transition(state, 0, "cut", 1.0)
    assert cleared.timeline.items[0].transition_out == TransitionSpec()

    unchanged = track_layout.set_transition(state, 2, "dissolve", 0.5)  # nothing after the last clip
    assert unchanged == state


def test_set_transition_everywhere_skips_the_last_clip():
    state = track_layout.set_transition_everywhere(_state(), "slide_left", 0.6)
    kinds = [item.transition_out for item in state.timeline.items]
    assert kinds == [TransitionSpec("slide_left", 0.6), TransitionSpec("slide_left", 0.6), TransitionSpec()]
    assert state.timeline.validate() == []


def test_transition_markers_sit_where_the_clips_overlap():
    state = track_layout.set_transition(_state(), 0, "dissolve", 1.0)
    assert track_layout.transition_markers(state, MEDIA) == [(0, 3.0, 4.0, "dissolve")]
    assert track_layout.transition_markers(_state(), MEDIA) == []


# --- filter looks -------------------------------------------------------------------------------------


def test_set_look_on_one_clip_keeps_its_other_effects():
    state = track_layout.set_look(_state(), 0, "moody", 0.55)
    effect = state.timeline.items[0].effect
    assert (effect.look, effect.look_intensity, effect.brightness) == ("moody", 0.55, 0.1)
    assert state.timeline.items[1].effect == EffectSpec()
    assert track_layout.effect_label(effect).startswith("Moody 55%")


def test_set_look_on_every_clip_and_intensity_bounds():
    state = track_layout.set_look(_state(), None, "golden_hour", 1.7)
    assert {item.effect.look for item in state.timeline.items} == {"golden_hour"}
    assert {item.effect.look_intensity for item in state.timeline.items} == {1.0}
    bars = track_layout.build_track_bars(state, MEDIA)
    assert [bar.label.split(" + ")[0] for bar in bars["effects"]] == ["Golden Hour 100%"] * 3


# --- preview transitions --------------------------------------------------------------------------------


RED, BLUE = Image.new("RGB", (40, 20), (255, 0, 0)), Image.new("RGB", (40, 20), (0, 0, 255))


def test_transition_frame_fade_and_dissolve():
    assert playback.transition_frame(RED, BLUE, "fade", 0.0).getpixel((5, 5)) == (255, 0, 0)
    half = playback.transition_frame(RED, BLUE, "fade", 0.5).getpixel((5, 5))
    assert half[0] == pytest.approx(128, abs=2) and half[2] == pytest.approx(128, abs=2)
    assert playback.transition_frame(RED, BLUE, "fade", 1.0).getpixel((5, 5)) == (0, 0, 255)

    dissolve = playback.transition_frame(RED, BLUE, "dissolve", 0.5)
    counts = {color: count for count, color in dissolve.getcolors()}
    assert set(counts) == {(255, 0, 0), (0, 0, 255)}  # whole pixels from both, not a blend
    assert 0.3 < counts[(0, 0, 255)] / (40 * 20) < 0.7


def test_transition_frame_slides():
    left = playback.transition_frame(RED, BLUE, "slide_left", 0.25)
    assert left.getpixel((0, 5)) == (255, 0, 0) and left.getpixel((39, 5)) == (0, 0, 255)
    assert left.getpixel((29, 5)) == (255, 0, 0) and left.getpixel((30, 5)) == (0, 0, 255)
    right = playback.transition_frame(RED, BLUE, "slide_right", 0.25)
    assert right.getpixel((9, 5)) == (0, 0, 255) and right.getpixel((10, 5)) == (255, 0, 0)


class _SolidStream:
    """A decode stream whose frames are one flat color per segment."""

    def __init__(self, command, *, width, height, first_timestamp):
        self.size = (width, height)
        self.color = (255, 0, 0) if first_timestamp < 3.0 else (0, 0, 255)

    def frame_for(self, t):
        return Image.new("RGB", self.size, self.color)

    def stop(self):
        pass


class _Clock:
    now = 50.0

    def __call__(self):
        return self.now


class _SilentAudio:
    def play(self, path, *, from_seconds):
        pass

    def stop(self):
        pass


def test_engine_blends_both_clips_during_a_fade():
    state = track_layout.set_transition(_state(), 0, "fade", 1.0)  # overlap 3.0 - 4.0 s
    clock = _Clock()
    engine = playback.PlaybackEngine(
        state.timeline, MEDIA, frame_size=(36, 64), ffmpeg="ffmpeg", audio=_SilentAudio(), clock=clock,
        stream_factory=_SolidStream,
    )
    engine.seek(2.0)
    engine.play()
    assert engine.tick().getpixel((5, 5)) == (255, 0, 0)
    clock.now += 1.5  # 3.5 s: halfway through the fade
    mid = engine.tick().getpixel((5, 5))
    assert mid[0] == pytest.approx(128, abs=3) and mid[2] == pytest.approx(128, abs=3)
    clock.now += 1.0  # 4.5 s: only the photo
    assert engine.tick().getpixel((5, 5)) == (0, 0, 255)


# --- Filters library thumbnails ------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_look_previews_are_graded_versions_of_the_image():
    image = Image.new("RGB", (48, 48), (130, 120, 110))
    previews = playback.render_look_previews(image, LOOK_CHOICES)
    assert set(previews) == set(LOOK_CHOICES)
    assert previews["none"].getpixel((24, 24)) == (130, 120, 110)
    for look in LOOK_CHOICES[1:]:
        assert previews[look].size == (48, 48)
        assert previews[look].getpixel((24, 24)) != (130, 120, 110), look
