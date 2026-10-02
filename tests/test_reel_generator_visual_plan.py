"""Tests for jarvis.reel_generator.visual_plan: the shared per-scene
visual plan dataclasses (ScenePlan/TextCue/VisualPlan) used by both AI
Reel Generator's own Smart Visual Director and jarvis.story_generator.
Promoted here from jarvis.story_generator.visual_plan - see that
module's own docstring for the full history.

Confirms: ScenePlan's backward-compatible defaults (visual_source,
transition) let jarvis.story_generator's own construction (which never
passes them) keep working, sticker_glyph resolves correctly, and
VisualPlan.for_scene() looks up by scene number."""

from __future__ import annotations

from typing import Any

from jarvis.reel_generator.visual_plan import (
    DEFAULT_TRANSITION,
    DEFAULT_VISUAL_SOURCE,
    STICKER_CHOICES,
    VISUAL_SOURCE_CHOICES,
    VISUAL_STYLE_CHOICES,
    ScenePlan,
    TextCue,
    VisualPlan,
    set_scene_generated_video,
)


def _plan(**overrides: Any) -> ScenePlan:
    defaults: dict[str, Any] = dict(
        scene_number=1, visual_type="photo_style", main_visual_prompt="a scene",
        supporting_visuals=(), text_cues=(), sticker="none", sticker_start_seconds=0.0,
        motion="static", emotion="calm", pacing="medium", lighting="neutral",
    )
    defaults.update(overrides)
    return ScenePlan(**defaults)


def test_scene_plan_constructible_without_visual_source_or_transition():
    # jarvis.story_generator.story_scenes never passes visual_source or
    # transition - both must default sensibly (backward compatibility
    # for the promotion - see ScenePlan's own docstring).
    plan = _plan()
    assert plan.visual_source == DEFAULT_VISUAL_SOURCE
    assert plan.transition == DEFAULT_TRANSITION
    assert plan.is_hook is False


def test_scene_plan_accepts_explicit_visual_source_and_transition():
    plan = _plan(visual_source="video_clip", transition="fade", is_hook=True)
    assert plan.visual_source == "video_clip"
    assert plan.transition == "fade"
    assert plan.is_hook is True


def test_sticker_glyph_resolves_known_sticker():
    plan = _plan(sticker="thinking")
    assert plan.sticker_glyph == STICKER_CHOICES["thinking"]
    assert plan.sticker_glyph != ""


def test_sticker_glyph_empty_for_none():
    plan = _plan(sticker="none")
    assert plan.sticker_glyph == ""


def test_sticker_glyph_empty_for_unknown_name():
    plan = _plan(sticker="not-a-real-sticker")
    assert plan.sticker_glyph == ""


def test_visual_plan_for_scene_finds_matching_plan():
    vp = VisualPlan(scenes=(_plan(scene_number=1), _plan(scene_number=2)))
    found = vp.for_scene(2)
    assert found is not None
    assert found.scene_number == 2


def test_visual_plan_for_scene_returns_none_when_missing():
    vp = VisualPlan(scenes=(_plan(scene_number=1),))
    assert vp.for_scene(99) is None


def test_visual_plan_visual_style_defaults_to_empty_string():
    # jarvis.story_generator has no whole-story style selector of its
    # own - VisualPlan.visual_style must default to "" so that
    # package's own construction (which never sets it) is unaffected.
    vp = VisualPlan(scenes=(_plan(),))
    assert vp.visual_style == ""


def test_visual_plan_accepts_explicit_visual_style():
    vp = VisualPlan(scenes=(_plan(),), visual_style="cinematic")
    assert vp.visual_style == "cinematic"


def test_text_cue_duration_seconds():
    cue = TextCue(text="hi", position="top", start_seconds=1.0, end_seconds=3.5, animation="pop")
    assert cue.duration_seconds == 2.5


def test_visual_source_choices_are_distinct_from_visual_type_choices():
    # The two vocabularies are deliberately different (see
    # ScenePlan's own docstring) - visual_source is the newer, broader
    # AI Reel Generator requirement-1 set.
    assert "video_clip" in VISUAL_SOURCE_CHOICES
    assert "uploaded_photo" in VISUAL_SOURCE_CHOICES
    assert "typography" in VISUAL_SOURCE_CHOICES


def test_visual_style_choices_cover_the_brief_own_list():
    expected = {
        "cinematic", "lifestyle", "minimal", "luxury", "soft_feminine",
        "ugc", "emotional", "modern", "bold", "educational",
    }
    assert expected.issubset(set(VISUAL_STYLE_CHOICES))


# --- set_scene_generated_video() (NATURAL MOTION / HYBRID mode's one write path) -----------


def test_set_scene_generated_video_sets_the_field_on_the_matching_scene_only():
    plan = VisualPlan(scenes=(_plan(scene_number=1), _plan(scene_number=2), _plan(scene_number=3)))
    updated = set_scene_generated_video(plan, 2, "/tmp/clip_02.mp4")
    assert updated.for_scene(1).generated_video_path is None
    assert updated.for_scene(2).generated_video_path == "/tmp/clip_02.mp4"
    assert updated.for_scene(3).generated_video_path is None


def test_set_scene_generated_video_leaves_every_other_field_of_the_matching_scene_untouched():
    plan = VisualPlan(scenes=(_plan(scene_number=1, main_visual_prompt="a cozy morning", emotion="hopeful"),))
    updated = set_scene_generated_video(plan, 1, "/tmp/clip_01.mp4")
    scene = updated.for_scene(1)
    assert scene.main_visual_prompt == "a cozy morning"
    assert scene.emotion == "hopeful"
    assert scene.generated_video_path == "/tmp/clip_01.mp4"


def test_set_scene_generated_video_can_clear_a_path_back_to_none():
    plan = VisualPlan(scenes=(_plan(scene_number=1, generated_video_path="/tmp/old_clip.mp4"),))
    updated = set_scene_generated_video(plan, 1, None)
    assert updated.for_scene(1).generated_video_path is None


def test_set_scene_generated_video_is_a_silent_no_op_for_an_unknown_scene_number():
    plan = VisualPlan(scenes=(_plan(scene_number=1),))
    updated = set_scene_generated_video(plan, 99, "/tmp/clip.mp4")
    assert updated.for_scene(1).generated_video_path is None
    assert len(updated.scenes) == 1


def test_set_scene_generated_video_does_not_mutate_the_original_plan():
    plan = VisualPlan(scenes=(_plan(scene_number=1),))
    set_scene_generated_video(plan, 1, "/tmp/clip.mp4")
    assert plan.for_scene(1).generated_video_path is None
