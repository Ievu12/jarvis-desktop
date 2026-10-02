"""Tests for jarvis.reel_generator.visual_quality: the local,
deterministic (no LLM call) visual_quality_score
("would this scene still make sense with text removed") and
consecutive-scene variety checks (camera_shot/camera_movement/motion/
environment) - the Visual Reel Generator stage's own quality gate."""

from __future__ import annotations

from jarvis.reel_generator.visual_plan import ScenePlan, VisualPlan
from jarvis.reel_generator.visual_quality import (
    MIN_RENDERABLE_QUALITY_SCORE,
    VarietyIssue,
    check_scene_variety,
    score_scene_plan,
)


def _plan(**overrides) -> ScenePlan:
    defaults = dict(
        scene_number=1, visual_type="photo_style",
        main_visual_prompt="A woman standing in a bright bathroom looking into the mirror, warm morning light",
        supporting_visuals=(), text_cues=(), sticker="none", sticker_start_seconds=0.0,
        motion="static", emotion="neutral", pacing="medium", lighting="neutral",
        environment="bright bathroom with a large mirror", subject_action="looking into the mirror",
        camera_shot="medium_shot", camera_movement="static",
    )
    defaults.update(overrides)
    return ScenePlan(**defaults)


# --- score_scene_plan() ------------------------------------------------------------------


def test_detailed_scene_scores_high():
    plan = _plan()
    breakdown = score_scene_plan(plan)
    assert breakdown.score == 10
    assert breakdown.passes is True
    assert breakdown.reasons == ()


def test_typography_source_is_heavily_penalized():
    plan = _plan(visual_source="typography", environment="", subject_action="")
    breakdown = score_scene_plan(plan)
    assert breakdown.score <= 4
    assert breakdown.passes is False
    assert any("typography" in reason for reason in breakdown.reasons)


def test_empty_main_visual_prompt_is_penalized():
    plan = _plan(main_visual_prompt="")
    breakdown = score_scene_plan(plan)
    assert breakdown.score <= 7
    assert any("too short" in reason for reason in breakdown.reasons)


def test_short_main_visual_prompt_is_penalized():
    plan = _plan(main_visual_prompt="a room")
    breakdown = score_scene_plan(plan)
    assert breakdown.score <= 7
    assert any("too short" in reason for reason in breakdown.reasons)


def test_vague_wording_is_penalized_even_if_long_enough():
    plan = _plan(main_visual_prompt="a generic background scene with some visual image and stuff happening")
    breakdown = score_scene_plan(plan)
    assert breakdown.score < 10
    assert any("generic" in reason or "vague" in reason for reason in breakdown.reasons)


def test_missing_environment_is_penalized():
    plan = _plan(environment="")
    breakdown = score_scene_plan(plan)
    assert breakdown.score == 9
    assert any("environment" in reason for reason in breakdown.reasons)


def test_missing_subject_action_is_penalized():
    plan = _plan(subject_action="")
    breakdown = score_scene_plan(plan)
    assert breakdown.score == 9
    assert any("subject_action" in reason for reason in breakdown.reasons)


def test_typography_does_not_double_penalize_missing_subject_action():
    # subject_action is expected to be blank for a typography card - it
    # must not ALSO get the generic "subject_action is not specified"
    # penalty on top of the typography penalty itself.
    plan = _plan(visual_source="typography", subject_action="", environment="")
    breakdown = score_scene_plan(plan)
    assert not any("subject_action is not specified" in reason for reason in breakdown.reasons)


def test_score_never_goes_below_one():
    plan = _plan(
        visual_source="typography", main_visual_prompt="", environment="", subject_action="",
    )
    breakdown = score_scene_plan(plan)
    assert breakdown.score >= 1


def test_score_never_exceeds_ten():
    plan = _plan()
    breakdown = score_scene_plan(plan)
    assert breakdown.score <= 10


def test_min_renderable_quality_score_is_seven():
    assert MIN_RENDERABLE_QUALITY_SCORE == 7


def test_passes_property_matches_the_threshold_exactly():
    # main_visual_prompt too short (-3) -> score 7 -> passes (>= 7);
    # environment/subject_action left populated so only ONE penalty applies.
    plan_at_threshold = _plan(main_visual_prompt="a room")
    breakdown = score_scene_plan(plan_at_threshold)
    assert breakdown.score == 7
    assert breakdown.passes is True


def test_passes_property_is_false_just_below_the_threshold():
    # Same short prompt (-3), PLUS a missing environment (-1) -> score 6
    # -> does not pass (< 7).
    plan_below_threshold = _plan(main_visual_prompt="a room", environment="")
    breakdown = score_scene_plan(plan_below_threshold)
    assert breakdown.score == 6
    assert breakdown.passes is False


def test_never_raises_on_a_plan_with_every_field_blank():
    plan = _plan(main_visual_prompt="", environment="", subject_action="", visual_source="typography")
    breakdown = score_scene_plan(plan)  # must not raise
    assert 1 <= breakdown.score <= 10


# --- check_scene_variety() ----------------------------------------------------------------


def test_no_issues_for_fully_varied_scenes():
    scenes = (
        _plan(scene_number=1, camera_shot="wide_shot", camera_movement="static", motion="static", environment="a kitchen"),
        _plan(scene_number=2, camera_shot="close_up", camera_movement="slow_push_in", motion="zoom_in", environment="a bathroom"),
        _plan(scene_number=3, camera_shot="macro", camera_movement="handheld", motion="pan_left", environment="a bedroom"),
    )
    issues = check_scene_variety(VisualPlan(scenes=scenes))
    assert issues == []


def test_repeated_camera_shot_between_consecutive_scenes_is_flagged():
    scenes = (
        _plan(scene_number=1, camera_shot="close_up"),
        _plan(scene_number=2, camera_shot="close_up"),
    )
    issues = check_scene_variety(VisualPlan(scenes=scenes))
    assert VarietyIssue(scene_number=2, field="camera_shot", value="close_up") in issues


def test_repeated_field_flags_the_later_scene_number():
    scenes = (
        _plan(scene_number=5, camera_movement="handheld"),
        _plan(scene_number=6, camera_movement="handheld"),
    )
    issues = check_scene_variety(VisualPlan(scenes=scenes))
    assert all(issue.scene_number == 6 for issue in issues if issue.field == "camera_movement")


def test_non_consecutive_repeats_are_not_flagged():
    # Scene 1 and scene 3 share the same camera_shot, but they are NOT
    # consecutive (scene 2 sits between them with a different shot) -
    # only ADJACENT repeats are a real "back-to-back" problem.
    scenes = (
        _plan(scene_number=1, camera_shot="wide_shot"),
        _plan(scene_number=2, camera_shot="close_up"),
        _plan(scene_number=3, camera_shot="wide_shot"),
    )
    issues = check_scene_variety(VisualPlan(scenes=scenes))
    assert not any(issue.field == "camera_shot" for issue in issues)


def test_multiple_repeated_fields_produce_multiple_issues():
    scenes = (
        _plan(scene_number=1, camera_shot="close_up", motion="static"),
        _plan(scene_number=2, camera_shot="close_up", motion="static"),
    )
    issues = check_scene_variety(VisualPlan(scenes=scenes))
    fields_flagged = {issue.field for issue in issues}
    assert "camera_shot" in fields_flagged
    assert "motion" in fields_flagged


def test_both_scenes_with_blank_environment_is_not_flagged():
    scenes = (
        _plan(scene_number=1, environment=""),
        _plan(scene_number=2, environment=""),
    )
    issues = check_scene_variety(VisualPlan(scenes=scenes))
    assert not any(issue.field == "environment" for issue in issues)


def test_repeated_environment_text_is_flagged():
    scenes = (
        _plan(scene_number=1, environment="a sunny kitchen"),
        _plan(scene_number=2, environment="a sunny kitchen"),
    )
    issues = check_scene_variety(VisualPlan(scenes=scenes))
    assert any(issue.field == "environment" and issue.value == "a sunny kitchen" for issue in issues)


def test_single_scene_plan_has_no_issues():
    issues = check_scene_variety(VisualPlan(scenes=(_plan(scene_number=1),)))
    assert issues == []


def test_empty_plan_has_no_issues():
    issues = check_scene_variety(VisualPlan(scenes=()))
    assert issues == []
