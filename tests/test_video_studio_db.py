"""Tests for jarvis.video_studio.db: SQLite metadata storage for video
projects. Redirected to a per-test tmp_path database file - no test
touches the real .jarvis/video_studio.db."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.video_studio import db
from jarvis.video_studio.export import ExportResult


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "video_studio.db"
    monkeypatch.setattr(db, "VIDEO_STUDIO_DB_FILE", db_file)
    return db_file


def test_db_file_created_on_first_use(_isolated_db_file):
    assert not _isolated_db_file.exists()
    db.create_project_record("proj1", "video.mp4")
    assert _isolated_db_file.exists()


def test_create_project_record_defaults_to_uploaded_status():
    db.create_project_record("proj1", "video.mp4")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "uploaded"
    assert record.original_filename == "video.mp4"
    assert record.analysis_data is None


def test_get_project_returns_none_for_unknown_id():
    assert db.get_project("does-not-exist") is None


def test_save_analysis_stores_json_and_advances_status():
    db.create_project_record("proj1", "video.mp4")
    db.save_analysis("proj1", {"duration_seconds": 12.5, "scene_changes": [1, 2]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "analyzed"
    assert record.analysis_data == {"duration_seconds": 12.5, "scene_changes": [1, 2]}


def test_save_analysis_overwrites_previous_analysis():
    db.create_project_record("proj1", "video.mp4")
    db.save_analysis("proj1", {"duration_seconds": 1.0})
    db.save_analysis("proj1", {"duration_seconds": 2.0})
    record = db.get_project("proj1")
    assert record is not None
    assert record.analysis_data == {"duration_seconds": 2.0}


def test_set_status_updates_only_status():
    db.create_project_record("proj1", "video.mp4")
    db.save_analysis("proj1", {"duration_seconds": 1.0})
    db.set_status("proj1", "transcribed")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "transcribed"
    assert record.analysis_data == {"duration_seconds": 1.0}


def test_save_transcript_stores_json_and_advances_status():
    db.create_project_record("proj1", "video.mp4")
    db.save_transcript("proj1", {"full_text": "hello world", "segments": []})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "transcribed"
    assert record.transcript_data == {"full_text": "hello world", "segments": []}


def test_save_transcript_overwrites_previous_transcript():
    db.create_project_record("proj1", "video.mp4")
    db.save_transcript("proj1", {"full_text": "first"})
    db.save_transcript("proj1", {"full_text": "second"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.transcript_data == {"full_text": "second"}


def test_save_highlights_stores_json_without_changing_status():
    db.create_project_record("proj1", "video.mp4")
    db.save_transcript("proj1", {"full_text": "hello"})
    db.save_highlights("proj1", {"candidates": [{"reason": "r"}]})
    record = db.get_project("proj1")
    assert record is not None
    assert record.highlights_data == {"candidates": [{"reason": "r"}]}
    assert record.status == "transcribed"  # unchanged by save_highlights()


def test_new_project_has_no_transcript_or_highlights():
    db.create_project_record("proj1", "video.mp4")
    record = db.get_project("proj1")
    assert record is not None
    assert record.transcript_data is None
    assert record.highlights_data is None


def test_list_projects_returns_newest_first():
    db.create_project_record("proj1", "a.mp4")
    db.create_project_record("proj2", "b.mp4")
    db.create_project_record("proj3", "c.mp4")
    ids = [p.id for p in db.list_projects()]
    assert ids == ["proj3", "proj2", "proj1"]


def test_list_projects_respects_limit():
    for i in range(5):
        db.create_project_record(f"proj{i}", f"{i}.mp4")
    assert len(db.list_projects(limit=2)) == 2


def test_delete_project_record_removes_row():
    db.create_project_record("proj1", "video.mp4")
    db.delete_project_record("proj1")
    assert db.get_project("proj1") is None


def test_delete_project_record_nonexistent_does_not_raise():
    db.delete_project_record("does-not-exist")  # must not raise


def test_schema_creation_is_idempotent_across_multiple_connections():
    db.create_project_record("proj1", "a.mp4")
    db.create_project_record("proj2", "b.mp4")  # must not raise on existing schema
    assert len(db.list_projects()) == 2


def test_migration_adds_missing_columns_to_pre_existing_db(_isolated_db_file):
    # Simulates a database created by an older version of this module
    # (Stage 1: only analysis_data/status, no transcript_data/
    # highlights_data) - confirms _migrate() adds the new columns
    # in place rather than every later query raising "no such column".
    import sqlite3

    _isolated_db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_isolated_db_file))
    conn.executescript(
        """
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            analysis_data TEXT,
            status TEXT NOT NULL DEFAULT 'uploaded'
        );
        INSERT INTO projects (id, created_at, original_filename, analysis_data, status)
        VALUES ('old1', '2026-01-01T00:00:00+00:00', 'old.mp4', NULL, 'uploaded');
        """
    )
    conn.commit()
    conn.close()

    record = db.get_project("old1")  # must not raise
    assert record is not None
    assert record.original_filename == "old.mp4"
    assert record.transcript_data is None
    assert record.highlights_data is None
    assert record.reel_plan_data is None

    db.save_transcript("old1", {"full_text": "now it works"})
    record2 = db.get_project("old1")
    assert record2 is not None
    assert record2.transcript_data == {"full_text": "now it works"}


def test_save_reel_plan_stores_json_and_advances_status():
    db.create_project_record("proj1", "video.mp4")
    db.save_reel_plan("proj1", {"hook": "h", "clips": []})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "reel_created"
    assert record.reel_plan_data == {"hook": "h", "clips": []}


def test_save_reel_plan_overwrites_previous_plan():
    db.create_project_record("proj1", "video.mp4")
    db.save_reel_plan("proj1", {"hook": "first"})
    db.save_reel_plan("proj1", {"hook": "second"})
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_plan_data == {"hook": "second"}


# --- exports -------------------------------------------------------------------------------


def _export_result(
    output_path="out.mp4", export_format="instagram_reel", duration_seconds=10.0,
    width=1080, height=1920, file_size_bytes=1000,
) -> ExportResult:
    return ExportResult(
        output_path=Path(output_path), export_format=export_format, duration_seconds=duration_seconds,
        width=width, height=height, file_size_bytes=file_size_bytes,
    )


def test_save_export_record_advances_status_to_exported():
    db.create_project_record("proj1", "video.mp4")
    db.save_export_record("proj1", _export_result())
    record = db.get_project("proj1")
    assert record is not None
    assert record.status == "exported"


def test_save_export_record_returns_new_id():
    db.create_project_record("proj1", "video.mp4")
    export_id1 = db.save_export_record("proj1", _export_result())
    export_id2 = db.save_export_record("proj1", _export_result())
    assert export_id1 != export_id2


def test_list_exports_returns_newest_first():
    db.create_project_record("proj1", "video.mp4")
    db.save_export_record("proj1", _export_result(output_path="a.mp4"))
    db.save_export_record("proj1", _export_result(output_path="b.mp4"))
    exports = db.list_exports("proj1")
    assert [e.file_path for e in exports] == ["b.mp4", "a.mp4"]


def test_list_exports_scoped_to_project():
    db.create_project_record("proj1", "a.mp4")
    db.create_project_record("proj2", "b.mp4")
    db.save_export_record("proj1", _export_result(output_path="only_proj1.mp4"))
    assert len(db.list_exports("proj1")) == 1
    assert len(db.list_exports("proj2")) == 0


def test_list_exports_empty_for_project_with_no_exports():
    db.create_project_record("proj1", "video.mp4")
    assert db.list_exports("proj1") == []


def test_export_record_carries_all_fields():
    db.create_project_record("proj1", "video.mp4")
    db.save_export_record(
        "proj1", _export_result(
            output_path="my_export.mp4", export_format="tiktok", duration_seconds=12.5,
            width=1080, height=1920, file_size_bytes=5000,
        ),
    )
    export = db.list_exports("proj1")[0]
    assert export.export_format == "tiktok"
    assert export.file_path == "my_export.mp4"
    assert export.duration_seconds == 12.5
    assert export.width == 1080
    assert export.height == 1920
    assert export.file_size_bytes == 5000
