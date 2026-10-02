"""Tests for jarvis.story_generator.db: SQLite metadata storage for
Story projects. Redirected to a per-test tmp_path database file - no
test touches the real .jarvis/story_generator.db. Extra coverage: the
structure_approved flag's exact state machine ("APPROVE STORY" gate) -
starts False, set True only by approve_structure(), reset back to False
by any subsequent save_structure() call - and the error_message/failed
status path (module brief requirement 12: errors visible in the UI)."""

from __future__ import annotations

import pytest

from jarvis.story_generator import db


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "story_generator.db"
    monkeypatch.setattr(db, "STORY_GENERATOR_DB_FILE", db_file)
    return db_file


def test_db_file_created_on_first_use(_isolated_db_file):
    assert not _isolated_db_file.exists()
    db.create_project_record("proj1", "A founder's failed launch", story_type="personal")
    assert _isolated_db_file.exists()


def test_create_project_record_defaults_to_created_status():
    db.create_project_record("proj1", "idea", story_type="personal")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "created"
    assert record.original_idea == "idea"
    assert record.story_type == "personal"
    assert record.structure_data is None
    assert record.structure_approved is False
    assert record.storyboard_data is None
    assert record.error_message is None


def test_get_project_returns_none_for_unknown_id():
    assert db.get_project("does-not-exist") is None


def test_save_structure_stores_json_and_advances_status():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_structure("proj1", {"beats": [{"kind": "hook", "text": "x"}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "structure_generated"
    assert record.structure_data == {"beats": [{"kind": "hook", "text": "x"}]}


def test_save_structure_overwrites_previous_structure():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_structure("proj1", {"beats": ["v1"]})
    db.save_structure("proj1", {"beats": ["v2"]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.structure_data == {"beats": ["v2"]}


def test_approve_structure_sets_flag_and_status():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_structure("proj1", {"beats": []})
    db.approve_structure("proj1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.structure_approved is True
    assert record.status == "structure_approved"


def test_regenerating_structure_resets_approval():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_structure("proj1", {"beats": ["v1"]})
    db.approve_structure("proj1")
    assert db.get_project("proj1").structure_approved is True  # type: ignore[union-attr]

    db.save_structure("proj1", {"beats": ["v2"]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.structure_approved is False
    assert record.status == "structure_generated"


def test_save_storyboard_stores_json_and_advances_status():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_storyboard("proj1", {"scenes": [{"number": 1}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.storyboard_data == {"scenes": [{"number": 1}]}
    assert record.status == "scenes_generated"


def test_save_visual_plan_stores_json_without_touching_storyboard():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_storyboard("proj1", {"scenes": [{"number": 1}]})
    db.save_visual_plan("proj1", {"scenes": [{"scene_number": 1, "visual_type": "photo_style"}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.visual_plan_data == {"scenes": [{"scene_number": 1, "visual_type": "photo_style"}]}
    assert record.storyboard_data == {"scenes": [{"number": 1}]}  # untouched by save_visual_plan


def test_visual_plan_data_defaults_to_none():
    db.create_project_record("proj1", "idea", story_type="personal")
    record = db.get_project("proj1")
    assert record is not None
    assert record.visual_plan_data is None


def test_save_export_path_advances_status():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_export_path("proj1", "/tmp/out.mp4")
    record = db.get_project("proj1")
    assert record is not None
    assert record.export_path == "/tmp/out.mp4"
    assert record.status == "exported"


def test_set_status_updates_only_status():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.save_structure("proj1", {"beats": ["x"]})
    db.set_status("proj1", "visuals_generated")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "visuals_generated"
    assert record.structure_data == {"beats": ["x"]}


def test_set_failed_records_error_and_status():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.set_failed("proj1", "FFmpeg was not found on PATH.")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "failed"
    assert record.error_message == "FFmpeg was not found on PATH."


def test_save_structure_clears_previous_error():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.set_failed("proj1", "boom")
    db.save_structure("proj1", {"beats": ["x"]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.error_message is None


def test_list_projects_returns_newest_first():
    db.create_project_record("proj1", "a", story_type="personal")
    db.create_project_record("proj2", "b", story_type="personal")
    db.create_project_record("proj3", "c", story_type="personal")
    ids = [p.id for p in db.list_projects()]
    assert ids == ["proj3", "proj2", "proj1"]


def test_list_projects_respects_limit():
    for i in range(5):
        db.create_project_record(f"proj{i}", f"idea {i}", story_type="personal")
    assert len(db.list_projects(limit=2)) == 2


def test_delete_project_record_removes_row():
    db.create_project_record("proj1", "idea", story_type="personal")
    db.delete_project_record("proj1")
    assert db.get_project("proj1") is None


def test_delete_project_record_nonexistent_does_not_raise():
    db.delete_project_record("does-not-exist")  # must not raise


def test_schema_creation_is_idempotent_across_multiple_connections():
    db.create_project_record("proj1", "a", story_type="personal")
    db.create_project_record("proj2", "b", story_type="personal")  # must not raise on existing schema
    assert len(db.list_projects()) == 2
