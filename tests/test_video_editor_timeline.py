"""Tests for jarvis.video_editor.timeline: the pure, I/O-free timeline
data model (Timeline/TimelineClip/TimelineStill/TransitionSpec).
Deliberately distinct from jarvis.video_studio.reel's own
ReelEditPlan/PlannedClip - these tests never touch that module at all,
confirming total isolation between the two data models.

Confirms: on-screen duration accounts for speed_factor correctly,
total_duration_seconds() sums every item, and validate() catches every
documented problem (empty timeline, bad trim range, out-of-range speed,
non-positive still duration, malformed transition) while returning []
for a well-formed timeline - never raising in any case."""

from __future__ import annotations

from jarvis.video_editor.timeline import (
    ASPECT_RATIO_CHOICES,
    TRANSITION_KIND_CHOICES,
    Timeline,
    TimelineClip,
    TimelineStill,
    TransitionSpec,
)


def test_aspect_ratio_and_transition_kind_choices():
    assert ASPECT_RATIO_CHOICES == ("9:16", "1:1", "16:9")
    assert TRANSITION_KIND_CHOICES == ("cut", "fade", "dissolve", "slide_left", "slide_right")


def test_transition_spec_defaults_to_a_plain_cut():
    transition = TransitionSpec()
    assert transition.kind == "cut"
    assert transition.duration_seconds == 0.0


def test_timeline_clip_on_screen_duration_at_normal_speed():
    clip = TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=2.0, source_out_seconds=7.0)
    assert clip.source_duration_seconds == 5.0
    assert clip.on_screen_duration_seconds == 5.0


def test_timeline_clip_on_screen_duration_at_double_speed():
    clip = TimelineClip(
        clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=10.0, speed_factor=2.0,
    )
    assert clip.on_screen_duration_seconds == 5.0  # plays twice as fast -> half the on-screen time


def test_timeline_clip_on_screen_duration_at_half_speed():
    clip = TimelineClip(
        clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=4.0, speed_factor=0.5,
    )
    assert clip.on_screen_duration_seconds == 8.0


def test_timeline_still_on_screen_duration_is_its_display_duration():
    still = TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=3.0)
    assert still.on_screen_duration_seconds == 3.0


def test_timeline_total_duration_sums_every_item():
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=2.0),
        TimelineClip(clip_id="c2", media_item_id="m3", source_in_seconds=10.0, source_out_seconds=13.0),
    ))
    assert timeline.total_duration_seconds() == 10.0


def test_empty_timeline_has_zero_duration():
    assert Timeline().total_duration_seconds() == 0.0


# --- validate() ------------------------------------------------------------------------------


def test_validate_empty_timeline_reports_a_problem():
    problems = Timeline().validate()
    assert len(problems) >= 1
    assert any("no clips" in p for p in problems)


def test_validate_well_formed_timeline_reports_nothing():
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=2.0),
    ))
    assert timeline.validate() == []


def test_validate_catches_backwards_trim_range():
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=5.0, source_out_seconds=2.0),
    ))
    problems = timeline.validate()
    assert any("trim end must be after trim start" in p for p in problems)


def test_validate_catches_out_of_range_speed_factor():
    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0, speed_factor=10.0,
        ),
    ))
    problems = timeline.validate()
    assert any("outside the supported" in p for p in problems)


def test_validate_catches_non_positive_still_duration():
    timeline = Timeline(items=(
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=0.0),
    ))
    problems = timeline.validate()
    assert any("display duration must be greater than zero" in p for p in problems)


def test_validate_catches_cut_transition_with_nonzero_duration():
    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0,
            transition_out=TransitionSpec(kind="cut", duration_seconds=1.0),
        ),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=2.0),
    ))
    problems = timeline.validate()
    assert any("cannot have a nonzero duration" in p for p in problems)


def test_validate_catches_fade_transition_with_zero_duration():
    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0,
            transition_out=TransitionSpec(kind="fade", duration_seconds=0.0),
        ),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=2.0),
    ))
    problems = timeline.validate()
    assert any("needs a duration greater than zero" in p for p in problems)


def test_validate_accepts_a_real_fade_transition():
    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0,
            transition_out=TransitionSpec(kind="fade", duration_seconds=0.5),
        ),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=2.0),
    ))
    assert timeline.validate() == []


def test_validate_catches_last_item_with_a_transition_into_nothing():
    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0,
            transition_out=TransitionSpec(kind="fade", duration_seconds=0.5),
        ),
    ))
    problems = timeline.validate()
    assert any("transition into a next clip that doesn't exist" in p for p in problems)


def test_timeline_item_effect_defaults_to_no_effect_for_backward_compatibility():
    # A project saved before jarvis.video_editor.effects existed never
    # had an `effect` field in its serialized TimelineClip/TimelineStill
    # - this construction (no `effect=` argument at all) is exactly what
    # json-deserializing such an older project produces, and must still
    # work with a real, validate()-passing default.
    from jarvis.video_editor.effects import EffectSpec

    clip = TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0)
    still = TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=2.0)
    assert clip.effect == EffectSpec()
    assert still.effect == EffectSpec()
    assert Timeline(items=(clip, still)).validate() == []


def test_validate_surfaces_an_invalid_effect_spec_on_an_item():
    from jarvis.video_editor.effects import EffectSpec

    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0,
            effect=EffectSpec(motion_intensity=99.0),
        ),
    ))
    problems = timeline.validate()
    assert any("intensity" in p for p in problems)


def test_timeline_never_raises_for_any_malformed_input():
    # Defensive - even a maximally-broken timeline must return a problem
    # list, never raise, matching this module's own documented contract.
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=5.0, source_out_seconds=5.0, speed_factor=0.0),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=-1.0),
    ))
    problems = timeline.validate()
    assert isinstance(problems, list)
    assert len(problems) > 0
