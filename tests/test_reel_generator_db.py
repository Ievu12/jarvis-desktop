"""Tests for jarvis.reel_generator.db: SQLite metadata storage for Reel
projects. Redirected to a per-test tmp_path database file - no test
touches the real .jarvis/reel_generator.db. Extra coverage beyond
test_design_studio_db.py's own equivalent tests: the script_approved
flag's exact state machine (module brief section 4's hard approval
gate) - starts False, set True only by approve_script(), and reset back
to False by any subsequent save_script() call (a regenerated script has
not been re-approved, even if a previous version once was)."""

from __future__ import annotations

import pytest

from jarvis.reel_generator import db


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "reel_generator.db"
    monkeypatch.setattr(db, "REEL_GENERATOR_DB_FILE", db_file)
    return db_file


def test_db_file_created_on_first_use(_isolated_db_file):
    assert not _isolated_db_file.exists()
    db.create_project_record("proj1", "Create a Reel about yoga")
    assert _isolated_db_file.exists()


def test_create_project_record_defaults_to_created_status():
    db.create_project_record("proj1", "Create a Reel about yoga")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "created"
    assert record.original_idea == "Create a Reel about yoga"
    assert record.brief_data is None
    assert record.script_data is None
    assert record.script_approved is False
    assert record.handoff_data is None


def test_get_project_returns_none_for_unknown_id():
    assert db.get_project("does-not-exist") is None


def test_save_brief_stores_json_and_advances_status():
    db.create_project_record("proj1", "idea")
    db.save_brief("proj1", {"topic": "yoga", "cta": "Save"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "brief_generated"
    assert record.brief_data == {"topic": "yoga", "cta": "Save"}


def test_save_brief_overwrites_previous_brief():
    db.create_project_record("proj1", "idea")
    db.save_brief("proj1", {"topic": "First"})
    db.save_brief("proj1", {"topic": "Second"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.brief_data == {"topic": "Second"}


def test_save_script_advances_status_and_does_not_approve():
    db.create_project_record("proj1", "idea")
    db.save_script("proj1", {"segments": []})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "script_generated"
    assert record.script_data == {"segments": []}
    assert record.script_approved is False


def test_approve_script_sets_flag_and_status():
    db.create_project_record("proj1", "idea")
    db.save_script("proj1", {"segments": []})
    db.approve_script("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.script_approved is True
    assert record.status == "script_approved"


def test_regenerating_script_resets_approval():
    db.create_project_record("proj1", "idea")
    db.save_script("proj1", {"segments": ["v1"]})
    db.approve_script("proj1")
    assert db.get_project("proj1").script_approved is True  # type: ignore[union-attr]

    db.save_script("proj1", {"segments": ["v2"]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.script_approved is False
    assert record.script_data == {"segments": ["v2"]}
    assert record.status == "script_generated"


def test_save_visual_plan_stores_json_and_style_without_touching_storyboard():
    db.create_project_record("proj1", "idea")
    db.save_storyboard("proj1", {"scenes": [{"number": 1}]})
    db.save_visual_plan("proj1", {"scenes": [{"scene_number": 1}]}, visual_style="cinematic")
    record = db.get_project("proj1")
    assert record is not None
    assert record.visual_plan_data == {"scenes": [{"scene_number": 1}]}
    assert record.visual_style == "cinematic"
    assert record.storyboard_data == {"scenes": [{"number": 1}]}  # untouched


def test_visual_plan_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.visual_plan_data is None
    assert record.visual_style is None


def test_voiceover_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.voiceover_path is None
    assert record.voiceover_text is None


def test_save_voiceover_stores_path_and_text():
    db.create_project_record("proj1", "idea")
    db.save_voiceover("proj1", "/tmp/voiceover/narration.wav", "Hello there.")
    record = db.get_project("proj1")
    assert record is not None
    assert record.voiceover_path == "/tmp/voiceover/narration.wav"
    assert record.voiceover_text == "Hello there."


def test_save_voiceover_overwrites_previous_voiceover():
    db.create_project_record("proj1", "idea")
    db.save_voiceover("proj1", "/tmp/old.wav", "Old text.")
    db.save_voiceover("proj1", "/tmp/new.wav", "New text.")
    record = db.get_project("proj1")
    assert record is not None
    assert record.voiceover_path == "/tmp/new.wav"
    assert record.voiceover_text == "New text."


def test_save_voiceover_does_not_touch_other_fields():
    db.create_project_record("proj1", "idea")
    db.save_storyboard("proj1", {"scenes": [{"number": 1}]})
    db.save_voiceover("proj1", "/tmp/narration.wav", "Hello there.")
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_data == {"scenes": [{"number": 1}]}


def test_clear_voiceover_resets_both_fields():
    db.create_project_record("proj1", "idea")
    db.save_voiceover("proj1", "/tmp/narration.wav", "Hello there.")
    db.clear_voiceover("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.voiceover_path is None
    assert record.voiceover_text is None


def test_text_mode_defaults_to_baked_in():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.text_mode == "baked_in"


def test_save_text_mode_updates_it():
    db.create_project_record("proj1", "idea")
    db.save_text_mode("proj1", "overlay")
    record = db.get_project("proj1")
    assert record is not None
    assert record.text_mode == "overlay"


def test_save_text_mode_does_not_touch_other_fields():
    db.create_project_record("proj1", "idea")
    db.save_storyboard("proj1", {"scenes": [{"number": 1}]})
    db.save_text_mode("proj1", "overlay")
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_data == {"scenes": [{"number": 1}]}


def test_caption_style_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.caption_style_data is None


def test_save_caption_style_stores_json():
    db.create_project_record("proj1", "idea")
    style = {"font": "Impact", "position": "top", "size": "large", "animation": "fade"}
    db.save_caption_style("proj1", style)
    record = db.get_project("proj1")
    assert record is not None
    assert record.caption_style_data == style


def test_save_caption_style_overwrites_previous():
    db.create_project_record("proj1", "idea")
    db.save_caption_style("proj1", {"font": "Arial", "position": "bottom", "size": "medium", "animation": "none"})
    db.save_caption_style("proj1", {"font": "Impact", "position": "top", "size": "large", "animation": "fade"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.caption_style_data["font"] == "Impact"


def test_set_status_updates_only_status():
    db.create_project_record("proj1", "idea")
    db.save_brief("proj1", {"topic": "Test"})
    db.set_status("proj1", "exported")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "exported"
    assert record.brief_data == {"topic": "Test"}


def test_list_projects_returns_newest_first():
    db.create_project_record("proj1", "a")
    db.create_project_record("proj2", "b")
    db.create_project_record("proj3", "c")
    ids = [p.id for p in db.list_projects()]
    assert ids == ["proj3", "proj2", "proj1"]


def test_list_projects_respects_limit():
    for i in range(5):
        db.create_project_record(f"proj{i}", f"idea {i}")
    assert len(db.list_projects(limit=2)) == 2


def test_delete_project_record_removes_row():
    db.create_project_record("proj1", "idea")
    db.delete_project_record("proj1")
    assert db.get_project("proj1") is None


def test_delete_project_record_nonexistent_does_not_raise():
    db.delete_project_record("does-not-exist")  # must not raise


def test_schema_creation_is_idempotent_across_multiple_connections():
    db.create_project_record("proj1", "a")
    db.create_project_record("proj2", "b")  # must not raise on existing schema
    assert len(db.list_projects()) == 2


# --- Mode A (footage) + Instagram hand-off ------------------------------------------------


def test_create_footage_project_record_sets_mode_and_links_video_project():
    db.create_footage_project_record(
        "proj1", "my_video.mp4", video_studio_project_id="vs-proj-1", video_studio_filename="my_video.mp4",
    )
    record = db.get_project("proj1")
    assert record is not None
    assert record.mode == "footage"
    assert record.video_studio_project_id == "vs-proj-1"
    assert record.video_studio_filename == "my_video.mp4"
    assert record.script_approved is True  # Mode A has no script-approval step of its own


def test_default_mode_is_idea():
    db.create_project_record("proj1", "some idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.mode == "idea"


def test_save_footage_plan_stores_json_and_advances_status():
    db.create_footage_project_record(
        "proj1", "my_video.mp4", video_studio_project_id="vs-proj-1", video_studio_filename="my_video.mp4",
    )
    db.save_footage_plan("proj1", {"hook": "Test hook"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.footage_plan_data == {"hook": "Test hook"}
    assert record.status == "plan_generated"


def test_save_handoff_stores_json_and_does_not_touch_other_fields():
    db.create_project_record("proj1", "idea")
    db.save_brief("proj1", {"topic": "yoga"})
    db.save_handoff("proj1", {"hook_set_id": 1, "caption_id": 2})
    record = db.get_project("proj1")
    assert record is not None
    assert record.handoff_data == {"hook_set_id": 1, "caption_id": 2}
    assert record.brief_data == {"topic": "yoga"}


def test_save_handoff_overwrites_previous_handoff():
    db.create_project_record("proj1", "idea")
    db.save_handoff("proj1", {"hook_set_id": 1})
    db.save_handoff("proj1", {"hook_set_id": 2})
    record = db.get_project("proj1")
    assert record is not None
    assert record.handoff_data == {"hook_set_id": 2}


def test_storyboard_approved_defaults_to_false():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_approved is False


def test_approve_storyboard_sets_the_flag():
    db.create_project_record("proj1", "idea")
    db.approve_storyboard("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_approved is True


def test_approve_storyboard_does_not_touch_other_fields():
    db.create_project_record("proj1", "idea")
    db.save_storyboard("proj1", {"scenes": [{"number": 1}]})
    db.approve_storyboard("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_data == {"scenes": [{"number": 1}]}


def test_unlock_storyboard_clears_the_flag():
    db.create_project_record("proj1", "idea")
    db.approve_storyboard("proj1")
    db.unlock_storyboard("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_approved is False


def test_original_storyboard_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.original_storyboard_data is None


def test_save_original_storyboard_snapshot_stores_it():
    db.create_project_record("proj1", "idea")
    db.save_original_storyboard_snapshot("proj1", {"scenes": [{"number": 1}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.original_storyboard_data == {"scenes": [{"number": 1}]}


def test_save_storyboard_does_not_touch_the_original_snapshot():
    db.create_project_record("proj1", "idea")
    db.save_original_storyboard_snapshot("proj1", {"scenes": [{"number": 1, "on_screen_text": "original"}]})
    db.save_storyboard("proj1", {"scenes": [{"number": 1, "on_screen_text": "edited"}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_data == {"scenes": [{"number": 1, "on_screen_text": "edited"}]}
    assert record.original_storyboard_data == {"scenes": [{"number": 1, "on_screen_text": "original"}]}


def test_original_visual_plan_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.original_visual_plan_data is None


def test_save_original_visual_plan_snapshot_stores_it():
    db.create_project_record("proj1", "idea")
    db.save_original_visual_plan_snapshot("proj1", {"scenes": [{"scene_number": 1}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.original_visual_plan_data == {"scenes": [{"scene_number": 1}]}


def test_save_visual_plan_does_not_touch_the_original_snapshot():
    db.create_project_record("proj1", "idea")
    db.save_original_visual_plan_snapshot("proj1", {"scenes": [{"scene_number": 1, "motion": "static"}]})
    db.save_visual_plan("proj1", {"scenes": [{"scene_number": 1, "motion": "zoom_in"}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.visual_plan_data == {"scenes": [{"scene_number": 1, "motion": "zoom_in"}]}
    assert record.original_visual_plan_data == {"scenes": [{"scene_number": 1, "motion": "static"}]}


# --- reel_approved (Reel Generation Workflow stage) ---------------------------------------


def test_reel_approved_defaults_to_false():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_approved is False


def test_approve_reel_sets_the_flag():
    db.create_project_record("proj1", "idea")
    db.approve_reel("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_approved is True


def test_approve_reel_does_not_touch_other_fields():
    db.create_project_record("proj1", "idea")
    db.save_export_path("proj1", "/tmp/reel.mp4")
    db.approve_reel("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.export_path == "/tmp/reel.mp4"


def test_unlock_reel_approval_clears_the_flag():
    db.create_project_record("proj1", "idea")
    db.approve_reel("proj1")
    db.unlock_reel_approval("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_approved is False


def test_save_export_path_clears_a_previous_reel_approval():
    # A fresh export is a genuinely new Reel render that has not itself
    # been reviewed/approved yet, even if a PREVIOUS export for this
    # project once was - see save_export_path()'s own docstring.
    db.create_project_record("proj1", "idea")
    db.save_export_path("proj1", "/tmp/reel_v1.mp4")
    db.approve_reel("proj1")
    db.save_export_path("proj1", "/tmp/reel_v2.mp4")
    record = db.get_project("proj1")
    assert record is not None
    assert record.export_path == "/tmp/reel_v2.mp4"
    assert record.reel_approved is False


# --- reel_approved_at (Reel Preview + Approve Reel stage) ---------------------------------


def test_reel_approved_at_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_approved_at is None


def test_approve_reel_records_a_real_timestamp():
    db.create_project_record("proj1", "idea")
    db.approve_reel("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_approved_at is not None
    # A real ISO-8601 timestamp, not a placeholder string.
    from datetime import datetime

    datetime.fromisoformat(record.reel_approved_at)


def test_unlock_reel_approval_clears_the_timestamp_too():
    db.create_project_record("proj1", "idea")
    db.approve_reel("proj1")
    db.unlock_reel_approval("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_approved_at is None


def test_save_export_path_clears_the_approval_timestamp_too():
    db.create_project_record("proj1", "idea")
    db.save_export_path("proj1", "/tmp/reel_v1.mp4")
    db.approve_reel("proj1")
    db.save_export_path("proj1", "/tmp/reel_v2.mp4")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_approved_at is None


# --- publish_package_data (Content Package + Ready to Publish stage) ----------------------


def test_publish_package_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.publish_package_data is None


def test_save_publish_package_stores_it():
    db.create_project_record("proj1", "idea")
    db.save_publish_package("proj1", {"status": "ready", "caption_text": "hello"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.publish_package_data == {"status": "ready", "caption_text": "hello"}


def test_save_publish_package_overwrites_previous():
    db.create_project_record("proj1", "idea")
    db.save_publish_package("proj1", {"status": "ready", "caption_text": "first"})
    db.save_publish_package("proj1", {"status": "ready", "caption_text": "second"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.publish_package_data == {"status": "ready", "caption_text": "second"}


def test_save_publish_package_with_none_clears_it():
    db.create_project_record("proj1", "idea")
    db.save_publish_package("proj1", {"status": "ready"})
    db.save_publish_package("proj1", None)
    record = db.get_project("proj1")
    assert record is not None
    assert record.publish_package_data is None


def test_save_publish_package_does_not_touch_other_fields():
    db.create_project_record("proj1", "idea")
    db.save_export_path("proj1", "/tmp/reel.mp4")
    db.save_publish_package("proj1", {"status": "ready"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.export_path == "/tmp/reel.mp4"


# --- cover_candidates_data (3-cover picker stage) -------------------------------------------


def test_cover_candidates_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_candidates_data is None


def test_save_cover_candidates_stores_the_full_list():
    db.create_project_record("proj1", "idea")
    candidates = [
        {"attempt": 0, "style_name": "modern", "cover_text": {"title": "A", "supporting_text": ""}, "image_path": "a.jpg"},
        {"attempt": 1, "style_name": "luxury", "cover_text": {"title": "B", "supporting_text": ""}, "image_path": "b.jpg"},
        {"attempt": 2, "style_name": "bold", "cover_text": {"title": "C", "supporting_text": ""}, "image_path": "c.jpg"},
    ]
    db.save_cover_candidates("proj1", candidates)
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_candidates_data == candidates


def test_save_cover_candidates_overwrites_previous_batch():
    db.create_project_record("proj1", "idea")
    db.save_cover_candidates("proj1", [{"attempt": 0, "style_name": "modern", "cover_text": {"title": "OLD", "supporting_text": ""}, "image_path": "old.jpg"}])
    db.save_cover_candidates("proj1", [{"attempt": 3, "style_name": "bold", "cover_text": {"title": "NEW", "supporting_text": ""}, "image_path": "new.jpg"}])
    record = db.get_project("proj1")
    assert record is not None
    assert len(record.cover_candidates_data) == 1
    assert record.cover_candidates_data[0]["cover_text"]["title"] == "NEW"


def test_save_cover_candidates_does_not_touch_cover_path():
    # cover_path stays the single "currently selected" cover - a fresh
    # candidate batch must never silently change or clear it (SELECT is
    # the only thing that writes cover_path, via the existing
    # save_cover_path()).
    db.create_project_record("proj1", "idea")
    db.save_cover_path("proj1", "/tmp/selected_cover.jpg")
    db.save_cover_candidates("proj1", [{"attempt": 0, "style_name": "modern", "cover_text": {"title": "A", "supporting_text": ""}, "image_path": "a.jpg"}])
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_path == "/tmp/selected_cover.jpg"


def test_save_cover_path_does_not_touch_cover_candidates_data():
    db.create_project_record("proj1", "idea")
    candidates = [{"attempt": 0, "style_name": "modern", "cover_text": {"title": "A", "supporting_text": ""}, "image_path": "a.jpg"}]
    db.save_cover_candidates("proj1", candidates)
    db.save_cover_path("proj1", str("a.jpg"))
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_candidates_data == candidates


# --- cover-in-export bug fix: cover_integration_mode ----------------------------------------
# Real, reported bug: a selected cover never reached the final exported
# video at all. cover_integration_mode records WHERE the cover should
# go (jarvis.gui.views.reel_generator.dashboard reads this fresh on
# every export click - see that module's _on_export_clicked()'s own
# docstring). Defaults to INSTAGRAM (the original, only behavior before
# this fix) for every existing/new project, so a project that never
# touches this setting behaves exactly as before.


def test_cover_integration_mode_defaults_to_instagram():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_integration_mode == db.COVER_INTEGRATION_MODE_INSTAGRAM


def test_save_cover_integration_mode_updates_it():
    db.create_project_record("proj1", "idea")
    db.save_cover_integration_mode("proj1", db.COVER_INTEGRATION_MODE_INTRO)
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_integration_mode == db.COVER_INTEGRATION_MODE_INTRO


def test_save_cover_integration_mode_both():
    db.create_project_record("proj1", "idea")
    db.save_cover_integration_mode("proj1", db.COVER_INTEGRATION_MODE_BOTH)
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_integration_mode == db.COVER_INTEGRATION_MODE_BOTH


def test_save_cover_integration_mode_does_not_touch_cover_path():
    db.create_project_record("proj1", "idea")
    db.save_cover_path("proj1", "/tmp/selected_cover.jpg")
    db.save_cover_integration_mode("proj1", db.COVER_INTEGRATION_MODE_INTRO)
    record = db.get_project("proj1")
    assert record is not None
    assert record.cover_path == "/tmp/selected_cover.jpg"


def test_cover_integration_mode_choices_contains_all_three_modes():
    assert set(db.COVER_INTEGRATION_MODE_CHOICES) == {
        db.COVER_INTEGRATION_MODE_INSTAGRAM, db.COVER_INTEGRATION_MODE_INTRO, db.COVER_INTEGRATION_MODE_BOTH,
    }


# --- NATURAL MOTION / AI VIDEO mode: reel_mode/motion_settings_data/motion_clips_data --------
# Real, reported requirement: three Reel modes (static/natural_motion/
# hybrid), additive to every existing column/behavior. reel_mode
# defaults to "static" for every project (both brand-new and any
# pre-existing DB predating this column) - the ONLY mode that existed
# before this feature - so a project that never touches this setting
# behaves exactly as before.


def test_reel_mode_defaults_to_static():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_STATIC


def test_save_reel_mode_updates_it():
    db.create_project_record("proj1", "idea")
    db.save_reel_mode("proj1", db.REEL_MODE_NATURAL_MOTION)
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_NATURAL_MOTION


def test_save_reel_mode_hybrid():
    db.create_project_record("proj1", "idea")
    db.save_reel_mode("proj1", db.REEL_MODE_HYBRID)
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_HYBRID


def test_save_reel_mode_rejects_unknown_mode():
    db.create_project_record("proj1", "idea")
    with pytest.raises(ValueError, match="Unknown reel_mode"):
        db.save_reel_mode("proj1", "not_a_real_mode")


def test_reel_mode_choices_contains_all_three_modes():
    assert set(db.REEL_MODE_CHOICES) == {db.REEL_MODE_STATIC, db.REEL_MODE_NATURAL_MOTION, db.REEL_MODE_HYBRID}


def test_motion_settings_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.motion_settings_data is None


def test_save_motion_settings_stores_json():
    db.create_project_record("proj1", "idea")
    settings = {"intensity": "high", "style": "cinematic", "clip_duration_seconds": 6.0}
    db.save_motion_settings("proj1", settings)
    record = db.get_project("proj1")
    assert record is not None
    assert record.motion_settings_data == settings


def test_save_motion_settings_overwrites_previous():
    db.create_project_record("proj1", "idea")
    db.save_motion_settings("proj1", {"intensity": "low"})
    db.save_motion_settings("proj1", {"intensity": "high"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.motion_settings_data == {"intensity": "high"}


def test_motion_clips_data_defaults_to_none():
    db.create_project_record("proj1", "idea")
    record = db.get_project("proj1")
    assert record is not None
    assert record.motion_clips_data is None


def test_save_motion_clips_stores_json():
    db.create_project_record("proj1", "idea")
    clips = [
        {"scene_number": 1, "video_path": "/tmp/clip_01.mp4", "source_image_path": "/tmp/scene_01.jpg", "error": None, "provider_task_id": "t1", "duration_seconds": 5.0},
    ]
    db.save_motion_clips("proj1", clips)
    record = db.get_project("proj1")
    assert record is not None
    assert record.motion_clips_data == clips


def test_save_reel_mode_does_not_touch_motion_settings_or_clips():
    db.create_project_record("proj1", "idea")
    db.save_motion_settings("proj1", {"intensity": "high"})
    db.save_motion_clips("proj1", [{"scene_number": 1}])
    db.save_reel_mode("proj1", db.REEL_MODE_HYBRID)
    record = db.get_project("proj1")
    assert record is not None
    assert record.motion_settings_data == {"intensity": "high"}
    assert record.motion_clips_data == [{"scene_number": 1}]


def test_save_motion_settings_does_not_touch_reel_mode_or_cover_integration_mode():
    db.create_project_record("proj1", "idea")
    db.save_reel_mode("proj1", db.REEL_MODE_HYBRID)
    db.save_cover_integration_mode("proj1", db.COVER_INTEGRATION_MODE_INTRO)
    db.save_motion_settings("proj1", {"intensity": "low"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_HYBRID
    assert record.cover_integration_mode == db.COVER_INTEGRATION_MODE_INTRO


def test_migration_adds_new_columns_with_correct_defaults_on_a_preexisting_db(tmp_path, monkeypatch):
    # Simulates an older reel_generator.db predating this feature's
    # three new columns - confirms the idempotent ALTER TABLE migration
    # path (not just a brand-new _SCHEMA CREATE TABLE) also produces the
    # correct defaults. Uses _SCHEMA's own base CREATE TABLE (every
    # column that ships in _SCHEMA itself, i.e. pre-dates even the
    # OLDEST _ADDED_COLUMNS entry) plus every _ADDED_COLUMNS entry
    # EXCEPT this feature's own three new ones - a faithful "the DB
    # predates only THIS stage's migration" simulation, not a
    # from-scratch hand-written schema that could drift from the real
    # one and silently stop testing anything meaningful.
    import sqlite3

    db_file = tmp_path / "old_reel_generator.db"
    monkeypatch.setattr(db, "REEL_GENERATOR_DB_FILE", db_file)

    conn = sqlite3.connect(str(db_file))
    conn.executescript(db._SCHEMA)
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    pre_existing_added_columns = [
        (name, col_type) for name, col_type in db._ADDED_COLUMNS
        if name not in ("reel_mode", "motion_settings_data", "motion_clips_data") and name not in existing_columns
    ]
    for name, col_type in pre_existing_added_columns:
        conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {col_type}")
    conn.execute(
        "INSERT INTO projects (id, created_at, original_idea) VALUES (?, ?, ?)",
        ("old-proj", "2025-01-01T00:00:00", "an old idea"),
    )
    conn.commit()
    conn.close()

    record = db.get_project("old-proj")
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_STATIC
    assert record.motion_settings_data is None
    assert record.motion_clips_data is None
