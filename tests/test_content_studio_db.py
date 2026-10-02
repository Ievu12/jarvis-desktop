"""Tests for jarvis.content_studio.db: SQLite metadata storage for
Content Studio projects. Redirected to a per-test tmp_path database
file - no test touches the real .jarvis/content_studio.db.

Confirms: a new project starts with every content type at 'draft' and
nothing linked, linking a content type's project id round-trips,
set_pdf_path()/set_content_type_status() work independently per content
type (approving the Reel does not affect the Post's own status), an
unknown content_type/status raises ValueError (a caller error, not a
silently-ignored no-op), plan_data round-trips and regenerating it does
NOT reset any content type's own status, and list_projects()/
get_project()/delete_project_record() behave the same way every other
module's own equivalents do."""

from __future__ import annotations

import pytest

from jarvis.content_studio import db


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "content_studio.db"
    monkeypatch.setattr(db, "CONTENT_STUDIO_DB_FILE", db_file)
    return db_file


def test_db_file_created_on_first_use(_isolated_db_file):
    assert not _isolated_db_file.exists()
    db.create_project_record("proj1", "5 minute morning yoga")
    assert _isolated_db_file.exists()


def test_new_project_starts_all_planned_and_unlinked():
    db.create_project_record("proj1", "some topic")
    record = db.get_project("proj1")
    assert record is not None
    assert record.topic == "some topic"
    assert record.plan_data is None
    for content_type in ("reel", "story", "post", "carousel", "pdf"):
        assert record.status_for(content_type) == "planned"
        assert record.error_for(content_type) is None
    assert record.reel_generator_project_id is None
    assert record.story_design_project_id is None
    assert record.post_design_project_id is None
    assert record.carousel_design_project_id is None
    assert record.pdf_path is None


def test_get_project_returns_none_for_unknown_id():
    assert db.get_project("does-not-exist") is None


def test_save_plan_round_trips():
    db.create_project_record("proj1", "some topic")
    db.save_plan("proj1", {"topic": "some topic", "items": []})
    record = db.get_project("proj1")
    assert record is not None
    assert record.plan_data == {"topic": "some topic", "items": []}


def test_save_plan_does_not_reset_any_status():
    db.create_project_record("proj1", "some topic")
    db.set_content_type_status("proj1", "reel", "approved")
    db.save_plan("proj1", {"topic": "x", "items": []})
    record = db.get_project("proj1")
    assert record is not None
    assert record.status_for("reel") == "approved"


def test_link_content_type_project_round_trips():
    db.create_project_record("proj1", "some topic")
    db.link_content_type_project("proj1", "reel", "reel-gen-id-1")
    db.link_content_type_project("proj1", "post", "design-studio-id-1")
    record = db.get_project("proj1")
    assert record is not None
    assert record.reel_generator_project_id == "reel-gen-id-1"
    assert record.post_design_project_id == "design-studio-id-1"
    assert record.linked_project_id_for("reel") == "reel-gen-id-1"
    assert record.linked_project_id_for("post") == "design-studio-id-1"
    assert record.linked_project_id_for("story") is None


def test_link_content_type_project_rejects_pdf():
    db.create_project_record("proj1", "some topic")
    with pytest.raises(ValueError, match="pdf"):
        db.link_content_type_project("proj1", "pdf", "some-id")


def test_link_content_type_project_rejects_unknown_type():
    db.create_project_record("proj1", "some topic")
    with pytest.raises(ValueError):
        db.link_content_type_project("proj1", "not_a_real_type", "some-id")


def test_set_pdf_path_round_trips():
    db.create_project_record("proj1", "some topic")
    db.set_pdf_path("proj1", "/some/path/output.pdf")
    record = db.get_project("proj1")
    assert record is not None
    assert record.pdf_path == "/some/path/output.pdf"


def test_set_content_type_status_is_independent_per_type():
    db.create_project_record("proj1", "some topic")
    db.set_content_type_status("proj1", "reel", "approved")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status_for("reel") == "approved"
    assert record.status_for("post") == "planned"  # untouched


def test_set_content_type_status_rejects_unknown_type():
    db.create_project_record("proj1", "some topic")
    with pytest.raises(ValueError):
        db.set_content_type_status("proj1", "not_a_real_type", "approved")


def test_set_content_type_status_rejects_unknown_status():
    db.create_project_record("proj1", "some topic")
    with pytest.raises(ValueError):
        db.set_content_type_status("proj1", "reel", "not_a_real_status")


def test_all_workflow_states_are_accepted():
    db.create_project_record("proj1", "some topic")
    for status in db.WORKFLOW_STATES:
        db.set_content_type_status("proj1", "reel", status)
        record = db.get_project("proj1")
        assert record is not None
        assert record.status_for("reel") == status


def test_workflow_states_include_stage_2_vocabulary():
    assert db.WORKFLOW_STATES == ("planned", "creating", "created", "approved", "exported", "failed")


# --- Stage 2: failure/retry state transitions ---------------------------------------------


def test_set_content_type_failed_records_status_and_error():
    db.create_project_record("proj1", "some topic")
    db.set_content_type_failed("proj1", "reel", "AI Reel Generator brief generation failed: network error")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status_for("reel") == "failed"
    assert record.error_for("reel") == "AI Reel Generator brief generation failed: network error"


def test_set_content_type_failed_only_affects_that_content_type():
    db.create_project_record("proj1", "some topic")
    db.set_content_type_failed("proj1", "reel", "boom")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status_for("post") == "planned"
    assert record.error_for("post") is None


def test_successful_retry_clears_previous_error():
    db.create_project_record("proj1", "some topic")
    db.set_content_type_failed("proj1", "reel", "boom")
    db.set_content_type_status("proj1", "reel", "creating")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status_for("reel") == "creating"
    assert record.error_for("reel") is None


def test_set_content_type_failed_rejects_unknown_type():
    db.create_project_record("proj1", "some topic")
    with pytest.raises(ValueError):
        db.set_content_type_failed("proj1", "not_a_real_type", "boom")


def test_creating_to_created_transition():
    db.create_project_record("proj1", "some topic")
    db.set_content_type_status("proj1", "reel", "creating")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status_for("reel") == "creating"

    db.link_content_type_project("proj1", "reel", "reel-gen-id-1")
    db.set_content_type_status("proj1", "reel", "created")
    record = db.get_project("proj1")
    assert record is not None
    assert record.status_for("reel") == "created"
    assert record.reel_generator_project_id == "reel-gen-id-1"


def test_list_projects_returns_newest_first():
    db.create_project_record("proj1", "a")
    db.create_project_record("proj2", "b")
    db.create_project_record("proj3", "c")
    ids = [p.id for p in db.list_projects()]
    assert ids == ["proj3", "proj2", "proj1"]


def test_list_projects_respects_limit():
    for i in range(5):
        db.create_project_record(f"proj{i}", f"topic {i}")
    assert len(db.list_projects(limit=2)) == 2


def test_delete_project_record_removes_row():
    db.create_project_record("proj1", "some topic")
    db.delete_project_record("proj1")
    assert db.get_project("proj1") is None


def test_delete_project_record_nonexistent_does_not_raise():
    db.delete_project_record("does-not-exist")  # must not raise


def test_schema_creation_is_idempotent_across_multiple_connections():
    db.create_project_record("proj1", "a")
    db.create_project_record("proj2", "b")  # must not raise on existing schema
    assert len(db.list_projects()) == 2
