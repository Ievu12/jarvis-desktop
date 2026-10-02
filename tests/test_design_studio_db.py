"""Tests for jarvis.design_studio.db: SQLite metadata storage for
design projects. Redirected to a per-test tmp_path database file - no
test touches the real .jarvis/design_studio.db."""

from __future__ import annotations

import pytest

from jarvis.design_studio import db


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "design_studio.db"
    monkeypatch.setattr(db, "DESIGN_STUDIO_DB_FILE", db_file)
    return db_file


def test_db_file_created_on_first_use(_isolated_db_file):
    assert not _isolated_db_file.exists()
    db.create_project_record("proj1", "Create a Story about yoga")
    assert _isolated_db_file.exists()


def test_create_project_record_defaults_to_created_status():
    db.create_project_record("proj1", "Create a Story about yoga")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "created"
    assert record.original_prompt == "Create a Story about yoga"
    assert record.brief_data is None
    assert record.design_path is None


def test_get_project_returns_none_for_unknown_id():
    assert db.get_project("does-not-exist") is None


def test_save_brief_stores_json_and_advances_status():
    db.create_project_record("proj1", "prompt")
    db.save_brief("proj1", {"headline": "Test Headline", "cta": "Click"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "brief_generated"
    assert record.brief_data == {"headline": "Test Headline", "cta": "Click"}


def test_save_brief_overwrites_previous_brief():
    db.create_project_record("proj1", "prompt")
    db.save_brief("proj1", {"headline": "First"})
    db.save_brief("proj1", {"headline": "Second"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.brief_data == {"headline": "Second"}


def test_save_design_path_advances_status():
    db.create_project_record("proj1", "prompt")
    db.save_design_path("proj1", "/some/path/design.jpg")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "rendered"
    assert record.design_path == "/some/path/design.jpg"


def test_set_status_updates_only_status():
    db.create_project_record("proj1", "prompt")
    db.save_brief("proj1", {"headline": "Test"})
    db.set_status("proj1", "exported")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "exported"
    assert record.brief_data == {"headline": "Test"}


def test_list_projects_returns_newest_first():
    db.create_project_record("proj1", "a")
    db.create_project_record("proj2", "b")
    db.create_project_record("proj3", "c")
    ids = [p.id for p in db.list_projects()]
    assert ids == ["proj3", "proj2", "proj1"]


def test_list_projects_respects_limit():
    for i in range(5):
        db.create_project_record(f"proj{i}", f"prompt {i}")
    assert len(db.list_projects(limit=2)) == 2


def test_delete_project_record_removes_row():
    db.create_project_record("proj1", "prompt")
    db.delete_project_record("proj1")
    assert db.get_project("proj1") is None


def test_delete_project_record_nonexistent_does_not_raise():
    db.delete_project_record("does-not-exist")  # must not raise


def test_schema_creation_is_idempotent_across_multiple_connections():
    db.create_project_record("proj1", "a")
    db.create_project_record("proj2", "b")  # must not raise on existing schema
    assert len(db.list_projects()) == 2
