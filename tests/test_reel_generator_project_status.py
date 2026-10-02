"""Tests for jarvis.reel_generator.project_status: the derived, 5-value
project status label (DRAFT/STORYBOARD APPROVED/GENERATING/READY FOR
REVIEW/EXPORTED) computed from an already-persisted ProjectRecord (or,
for the richer entry point, real scene-visual counts) - never a second
persisted status column, see that module's own docstring."""

from __future__ import annotations

from jarvis.reel_generator.db import ProjectRecord
from jarvis.reel_generator.project_status import (
    ProjectStatus,
    compute_motion_status,
    compute_reel_status,
    compute_status,
    compute_status_from_visuals,
)
from jarvis.reel_generator.scenes import SceneMotionClip


def _record(**overrides) -> ProjectRecord:
    defaults = dict(
        id="proj1", created_at="2026-01-01T00:00:00", original_idea="idea",
        brief_data=None, script_data=None, script_approved=False, storyboard_data=None,
        cover_path=None, caption_data=None, export_path=None, mode="idea",
        video_studio_project_id=None, video_studio_filename=None, footage_plan_data=None,
        handoff_data=None, visual_plan_data=None, visual_style=None,
        voiceover_path=None, voiceover_text=None, text_mode="baked_in", caption_style_data=None,
        storyboard_approved=False, original_storyboard_data=None, original_visual_plan_data=None,
        reel_approved=False, reel_approved_at=None,
        publish_package_data=None,
        status="created",
        cover_candidates_data=None,
        cover_integration_mode="instagram",
    )
    defaults.update(overrides)
    return ProjectRecord(**defaults)


# --- compute_status() --------------------------------------------------------------------


def test_brand_new_project_is_draft():
    assert compute_status(_record()) == ProjectStatus.DRAFT


def test_brief_only_is_still_draft():
    assert compute_status(_record(brief_data={"topic": "x"})) == ProjectStatus.DRAFT


def test_unapproved_script_is_still_draft():
    record = _record(script_data={"segments": []}, script_approved=False)
    assert compute_status(record) == ProjectStatus.DRAFT


def test_approved_script_without_storyboard_is_still_draft():
    record = _record(script_data={"segments": []}, script_approved=True)
    assert compute_status(record) == ProjectStatus.DRAFT


def test_approved_storyboard_is_storyboard_approved():
    record = _record(
        script_approved=True, storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True,
    )
    assert compute_status(record) == ProjectStatus.STORYBOARD_APPROVED


def test_storyboard_without_explicit_approval_is_draft_not_storyboard_approved():
    # A storyboard that EXISTS (and whose script was approved) but has
    # not been explicitly locked via APPROVE STORYBOARD (Storyboard
    # Creative Controls stage's own storyboard_approved flag) must never
    # be mistaken for STORYBOARD_APPROVED - the label now requires the
    # real, explicit lock, not just script_approved + storyboard
    # presence (see compute_status()'s own docstring for why this
    # changed once storyboard_approved started existing).
    record = _record(
        script_approved=True, storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=False,
    )
    assert compute_status(record) == ProjectStatus.DRAFT


def test_storyboard_approved_flag_alone_is_not_enough_without_storyboard_data():
    # storyboard_approved=True with no actual storyboard_data (a
    # theoretical/defensive case) must not claim STORYBOARD_APPROVED -
    # there is no real storyboard to have approved.
    record = _record(script_approved=True, storyboard_data=None, storyboard_approved=True)
    assert compute_status(record) == ProjectStatus.DRAFT


def test_export_path_set_is_always_exported_regardless_of_other_fields():
    record = _record(export_path="/tmp/reel.mp4")
    assert compute_status(record) == ProjectStatus.EXPORTED


def test_export_path_set_overrides_is_generating():
    record = _record(export_path="/tmp/reel.mp4")
    assert compute_status(record, is_generating=True) == ProjectStatus.EXPORTED


def test_is_generating_overrides_storyboard_approved():
    record = _record(
        script_approved=True, storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True,
    )
    assert compute_status(record, is_generating=True) == ProjectStatus.GENERATING


def test_is_generating_overrides_draft():
    record = _record()
    assert compute_status(record, is_generating=True) == ProjectStatus.GENERATING


# --- compute_status_from_visuals() --------------------------------------------------------


def test_no_scenes_rendered_yet_is_storyboard_approved():
    record = _record(
        script_approved=True, storyboard_approved=True, storyboard_data={"scenes": [{"number": 1}, {"number": 2}]},
    )
    status = compute_status_from_visuals(record, total_scenes=2, rendered_scene_count=0, failed_scene_count=0)
    assert status == ProjectStatus.STORYBOARD_APPROVED


def test_storyboard_data_without_explicit_approval_is_draft_from_visuals_too():
    record = _record(
        script_approved=True, storyboard_approved=False, storyboard_data={"scenes": [{"number": 1}, {"number": 2}]},
    )
    status = compute_status_from_visuals(record, total_scenes=2, rendered_scene_count=0, failed_scene_count=0)
    assert status == ProjectStatus.DRAFT


def test_partially_rendered_scenes_is_generating():
    record = _record(
        script_approved=True, storyboard_approved=True, storyboard_data={"scenes": [{"number": 1}, {"number": 2}]},
    )
    status = compute_status_from_visuals(record, total_scenes=2, rendered_scene_count=1, failed_scene_count=0)
    assert status == ProjectStatus.GENERATING


def test_a_failed_scene_is_generating_even_if_others_succeeded():
    record = _record(
        script_approved=True, storyboard_approved=True, storyboard_data={"scenes": [{"number": 1}, {"number": 2}]},
    )
    status = compute_status_from_visuals(record, total_scenes=2, rendered_scene_count=1, failed_scene_count=1)
    assert status == ProjectStatus.GENERATING


def test_all_scenes_rendered_successfully_is_ready_for_review():
    record = _record(
        script_approved=True, storyboard_approved=True, storyboard_data={"scenes": [{"number": 1}, {"number": 2}]},
    )
    status = compute_status_from_visuals(record, total_scenes=2, rendered_scene_count=2, failed_scene_count=0)
    assert status == ProjectStatus.READY_FOR_REVIEW


def test_export_path_set_overrides_ready_for_review():
    record = _record(
        script_approved=True, storyboard_approved=True, storyboard_data={"scenes": [{"number": 1}]},
        export_path="/tmp/reel.mp4",
    )
    status = compute_status_from_visuals(record, total_scenes=1, rendered_scene_count=1, failed_scene_count=0)
    assert status == ProjectStatus.EXPORTED


def test_live_is_generating_flag_overrides_ready_for_review_counts():
    # A live GUI re-render (e.g. REGENERATE SCENE) is actively running
    # even though the OLD counts still look complete - the live flag
    # takes priority.
    record = _record(script_approved=True, storyboard_approved=True, storyboard_data={"scenes": [{"number": 1}]})
    status = compute_status_from_visuals(
        record, total_scenes=1, rendered_scene_count=1, failed_scene_count=0, is_generating=True,
    )
    assert status == ProjectStatus.GENERATING


def test_no_storyboard_at_all_is_draft_regardless_of_counts():
    record = _record(script_approved=True, storyboard_approved=True, storyboard_data=None)
    status = compute_status_from_visuals(record, total_scenes=0, rendered_scene_count=0, failed_scene_count=0)
    assert status == ProjectStatus.DRAFT


# --- ProjectStatus enum itself -------------------------------------------------------------


def test_status_values_match_module_brief_vocabulary_exactly():
    assert ProjectStatus.DRAFT.value == "DRAFT"
    assert ProjectStatus.STORYBOARD_APPROVED.value == "STORYBOARD APPROVED"
    assert ProjectStatus.GENERATING.value == "GENERATING"
    assert ProjectStatus.READY_FOR_REVIEW.value == "READY FOR REVIEW"
    assert ProjectStatus.EXPORTED.value == "EXPORTED"


def test_str_returns_the_display_value():
    assert str(ProjectStatus.READY_FOR_REVIEW) == "READY FOR REVIEW"


# --- compute_reel_status() (Reel Generation Workflow stage) -------------------------------


def test_reel_status_no_storyboard_is_draft():
    record = _record()
    status = compute_reel_status(record, total_scenes=0, rendered_scene_count=0, failed_scene_count=0)
    assert status == ProjectStatus.DRAFT


def test_reel_status_storyboard_not_approved_is_draft():
    record = _record(storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=False)
    status = compute_reel_status(record, total_scenes=1, rendered_scene_count=0, failed_scene_count=0)
    assert status == ProjectStatus.DRAFT


def test_reel_status_approved_storyboard_no_scenes_rendered_yet():
    record = _record(storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True)
    status = compute_reel_status(record, total_scenes=1, rendered_scene_count=0, failed_scene_count=0)
    assert status == ProjectStatus.STORYBOARD_APPROVED


def test_reel_status_is_generating_scenes_flag():
    record = _record(storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True)
    status = compute_reel_status(
        record, total_scenes=1, rendered_scene_count=0, failed_scene_count=0, is_generating_scenes=True,
    )
    assert status == ProjectStatus.GENERATING_SCENES


def test_reel_status_partial_scene_rendering_is_generating_scenes():
    record = _record(storyboard_data={"scenes": [{"number": 1}, {"number": 2}]}, storyboard_approved=True)
    status = compute_reel_status(record, total_scenes=2, rendered_scene_count=1, failed_scene_count=0)
    assert status == ProjectStatus.GENERATING_SCENES


def test_reel_status_a_failed_scene_is_generating_scenes():
    record = _record(storyboard_data={"scenes": [{"number": 1}, {"number": 2}]}, storyboard_approved=True)
    status = compute_reel_status(record, total_scenes=2, rendered_scene_count=1, failed_scene_count=1)
    assert status == ProjectStatus.GENERATING_SCENES


def test_reel_status_all_scenes_rendered_is_scenes_ready():
    record = _record(storyboard_data={"scenes": [{"number": 1}, {"number": 2}]}, storyboard_approved=True)
    status = compute_reel_status(record, total_scenes=2, rendered_scene_count=2, failed_scene_count=0)
    assert status == ProjectStatus.SCENES_READY


def test_reel_status_is_generating_reel_flag_before_export_exists():
    record = _record(
        storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True, export_path=None,
    )
    status = compute_reel_status(
        record, total_scenes=1, rendered_scene_count=1, failed_scene_count=0, is_generating_reel=True,
    )
    assert status == ProjectStatus.GENERATING_REEL


def test_reel_status_export_exists_but_not_approved_is_reel_ready():
    record = _record(
        storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True,
        export_path="/tmp/reel.mp4", reel_approved=False,
    )
    status = compute_reel_status(record, total_scenes=1, rendered_scene_count=1, failed_scene_count=0)
    assert status == ProjectStatus.REEL_READY


def test_reel_status_export_exists_and_approved_is_reel_approved():
    record = _record(
        storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True,
        export_path="/tmp/reel.mp4", reel_approved=True,
    )
    status = compute_reel_status(record, total_scenes=1, rendered_scene_count=1, failed_scene_count=0)
    assert status == ProjectStatus.REEL_APPROVED


def test_reel_status_regenerating_reel_export_overrides_reel_approved():
    # Regenerating the final Reel (a new export in flight) while a
    # PREVIOUS export was already approved must show GENERATING_REEL,
    # not REEL_APPROVED - the OLD approval no longer describes what's
    # currently happening.
    record = _record(
        storyboard_data={"scenes": [{"number": 1}]}, storyboard_approved=True,
        export_path="/tmp/reel_old.mp4", reel_approved=True,
    )
    status = compute_reel_status(
        record, total_scenes=1, rendered_scene_count=1, failed_scene_count=0, is_generating_reel=True,
    )
    assert status == ProjectStatus.GENERATING_REEL


def test_reel_status_enum_values_match_module_brief_vocabulary():
    assert ProjectStatus.GENERATING_SCENES.value == "GENERATING SCENES"
    assert ProjectStatus.SCENES_READY.value == "SCENES READY"
    assert ProjectStatus.GENERATING_REEL.value == "GENERATING REEL"
    assert ProjectStatus.REEL_READY.value == "REEL READY"
    assert ProjectStatus.REEL_APPROVED.value == "REEL APPROVED"


# --- compute_motion_status() (NATURAL MOTION / HYBRID modes) --------------------------------
# Real requirement: a Static-mode project must NEVER be affected by
# this function at all (the GUI only ever calls it when
# record.reel_mode != db.REEL_MODE_STATIC) - the defensive branch inside
# compute_motion_status() itself is tested here too, for completeness.


def _clip(scene_number: int, *, error: str | None = None) -> SceneMotionClip:
    return SceneMotionClip(
        scene_number=scene_number, video_path=None if error else f"/tmp/clip_{scene_number:02d}.mp4",
        source_image_path=f"/tmp/scene_{scene_number:02d}.jpg", error=error, provider_task_id=None,
        duration_seconds=5.0,
    )


def test_motion_status_static_mode_falls_back_to_draft_or_storyboard_approved():
    draft_record = _record(reel_mode="static", storyboard_data=None)
    assert compute_motion_status(draft_record, []) == ProjectStatus.DRAFT

    approved_record = _record(reel_mode="static", storyboard_data={"scenes": [{"number": 1}]})
    assert compute_motion_status(approved_record, []) == ProjectStatus.STORYBOARD_APPROVED


def test_motion_status_is_generating_returns_generating_motion():
    record = _record(reel_mode="natural_motion", storyboard_data={"scenes": [{"number": 1}]})
    assert compute_motion_status(record, [], is_generating=True) == ProjectStatus.GENERATING_MOTION


def test_motion_status_no_clips_yet_returns_generating_motion():
    record = _record(reel_mode="natural_motion", storyboard_data={"scenes": [{"number": 1}]})
    assert compute_motion_status(record, []) == ProjectStatus.GENERATING_MOTION


def test_motion_status_one_failed_clip_returns_motion_partial():
    record = _record(reel_mode="hybrid", storyboard_data={"scenes": [{"number": 1}, {"number": 2}]})
    clips = [_clip(1), _clip(2, error="RUNWAY_API_KEY is not configured")]
    assert compute_motion_status(record, clips) == ProjectStatus.MOTION_PARTIAL


def test_motion_status_every_clip_succeeded_returns_storyboard_approved():
    record = _record(reel_mode="natural_motion", storyboard_data={"scenes": [{"number": 1}, {"number": 2}]})
    clips = [_clip(1), _clip(2)]
    assert compute_motion_status(record, clips) == ProjectStatus.STORYBOARD_APPROVED
