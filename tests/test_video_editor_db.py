"""Tests for jarvis.video_editor.db: SQLite metadata storage for Video
Editor projects. Redirected to a per-test tmp_path database file - no
test touches the real VIDEO_EDITOR_DB_FILE, and this file never touches
jarvis.video_studio.db's own schema/tables at all (confirmed by using
an entirely separate, isolated SQLite file throughout).

Confirms: a new project defaults to NULL timeline/media/export-format
and status "created", each save_*() function touches only its own
column(s), get_project()/list_projects() round-trip real JSON data
correctly, and delete_project_record() removes only the metadata row
(never touching on-disk files - that's jarvis.video_editor.storage's
own, separate responsibility)."""

from __future__ import annotations

import pytest

from jarvis.video_editor import db


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "video_editor.db"
    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", db_file)
    return db_file


def test_db_file_created_on_first_use(_isolated_db_file):
    assert not _isolated_db_file.exists()
    db.create_project_record("proj1", "My Project")
    assert _isolated_db_file.exists()


def test_create_project_record_defaults():
    db.create_project_record("proj1", "My Project")
    record = db.get_project("proj1")
    assert record is not None
    assert record.name == "My Project"
    assert record.timeline_data is None
    assert record.media_items_data is None
    assert record.export_format_last_used is None
    assert record.status == "created"


def test_get_project_returns_none_for_unknown_id():
    assert db.get_project("does-not-exist") is None


def test_save_timeline_stores_and_round_trips_json():
    db.create_project_record("proj1", "My Project")
    timeline_data = {"items": [{"clip_id": "c1", "media_item_id": "m1"}], "aspect_ratio": "9:16"}
    db.save_timeline("proj1", timeline_data)
    record = db.get_project("proj1")
    assert record is not None
    assert record.timeline_data == timeline_data


def test_save_timeline_does_not_touch_media_items_or_status():
    db.create_project_record("proj1", "My Project")
    db.save_media_items("proj1", [{"media_item_id": "m1"}])
    db.set_status("proj1", "editing")
    db.save_timeline("proj1", {"items": [], "aspect_ratio": "9:16"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.media_items_data == [{"media_item_id": "m1"}]
    assert record.status == "editing"


def test_save_media_items_stores_and_round_trips_json():
    db.create_project_record("proj1", "My Project")
    media_data = [{"media_item_id": "m1", "kind": "video"}, {"media_item_id": "m2", "kind": "photo"}]
    db.save_media_items("proj1", media_data)
    record = db.get_project("proj1")
    assert record is not None
    assert record.media_items_data == media_data


def test_save_media_items_does_not_touch_timeline():
    db.create_project_record("proj1", "My Project")
    db.save_timeline("proj1", {"items": [], "aspect_ratio": "16:9"})
    db.save_media_items("proj1", [{"media_item_id": "m1"}])
    record = db.get_project("proj1")
    assert record is not None
    assert record.timeline_data == {"items": [], "aspect_ratio": "16:9"}


def test_save_export_format_last_used():
    db.create_project_record("proj1", "My Project")
    db.save_export_format_last_used("proj1", "9:16 1080p")
    record = db.get_project("proj1")
    assert record is not None
    assert record.export_format_last_used == "9:16 1080p"


def test_set_status_updates_only_status():
    db.create_project_record("proj1", "My Project")
    db.save_timeline("proj1", {"items": [], "aspect_ratio": "9:16"})
    db.set_status("proj1", "exported")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "exported"
    assert record.timeline_data == {"items": [], "aspect_ratio": "9:16"}


def test_list_projects_returns_newest_first():
    db.create_project_record("proj1", "First")
    db.create_project_record("proj2", "Second")
    db.save_timeline("proj1", {"items": [], "aspect_ratio": "9:16"})  # bumps proj1's updated_at
    projects = db.list_projects()
    assert projects[0].id == "proj1"


def test_list_projects_respects_limit():
    for i in range(5):
        db.create_project_record(f"proj{i}", f"Project {i}")
    assert len(db.list_projects(limit=3)) == 3


def test_delete_project_record_removes_row():
    db.create_project_record("proj1", "My Project")
    db.delete_project_record("proj1")
    assert db.get_project("proj1") is None


def test_delete_project_record_nonexistent_does_not_raise():
    db.delete_project_record("does-not-exist")


def test_schema_creation_is_idempotent_across_multiple_connections():
    db.create_project_record("proj1", "My Project")
    db.create_project_record("proj2", "Another Project")  # a second _connect() call, same db file
    assert len(db.list_projects()) == 2
