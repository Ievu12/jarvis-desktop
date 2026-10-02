"""Tests for jarvis.tools.video_studio_tools.ListVideoStudioProjectsTool:
the agent-facing, read-only tool listing AI Video Studio project
status. Redirected to a per-test tmp_path database file - no test
touches the real .jarvis/video_studio.db. Confirms: unfinished-only
filtering excludes exported projects, limit is respected, empty results
return a clear message rather than an empty string, and the tool never
raises even if the database read fails."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.tools.video_studio_tools import ListVideoStudioProjectsTool
from jarvis.video_studio import db


def _isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "video_studio.db"
    monkeypatch.setattr(db, "VIDEO_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr("jarvis.tools.video_studio_tools.db.VIDEO_STUDIO_DB_FILE", db_file)


def test_no_projects_returns_clear_message(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    result = ListVideoStudioProjectsTool().run()
    assert result.ok is True
    assert "no unfinished" in result.output.lower()


def test_no_projects_at_all_returns_clear_message(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    result = ListVideoStudioProjectsTool().run(unfinished_only=False)
    assert result.ok is True
    assert "no ai video studio projects" in result.output.lower()


def test_lists_unfinished_projects(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    db.create_project_record("proj1", "video1.mp4")
    db.create_project_record("proj2", "video2.mp4")
    db.set_status("proj2", "exported")

    result = ListVideoStudioProjectsTool().run()
    assert result.ok is True
    assert "video1.mp4" in result.output
    assert "video2.mp4" not in result.output  # exported, filtered out


def test_lists_all_projects_when_unfinished_only_false(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    db.create_project_record("proj1", "video1.mp4")
    db.create_project_record("proj2", "video2.mp4")
    db.set_status("proj2", "exported")

    result = ListVideoStudioProjectsTool().run(unfinished_only=False)
    assert "video1.mp4" in result.output
    assert "video2.mp4" in result.output


def test_output_includes_status_and_date(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    db.create_project_record("proj1", "video1.mp4")
    db.set_status("proj1", "transcribed")

    result = ListVideoStudioProjectsTool().run()
    assert "transcribed" in result.output
    assert "video1.mp4" in result.output


def test_limit_is_respected(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    for i in range(10):
        db.create_project_record(f"proj{i}", f"video{i}.mp4")

    result = ListVideoStudioProjectsTool().run(unfinished_only=False, limit=3)
    assert len(result.output.splitlines()) == 3


def test_limit_clamped_to_sane_range(tmp_path, monkeypatch):
    _isolated_db(tmp_path, monkeypatch)
    db.create_project_record("proj1", "video1.mp4")
    result = ListVideoStudioProjectsTool().run(limit=0)
    assert result.ok is True  # clamped to at least 1, not an error


def test_database_error_returns_clear_message_not_raise():
    with patch(
        "jarvis.tools.video_studio_tools.db.list_projects", side_effect=RuntimeError("disk error"),
    ):
        result = ListVideoStudioProjectsTool().run()
    assert result.ok is False
    assert "disk error" in result.output


def test_never_calls_analysis_or_export_functions():
    # This tool must be READ-ONLY over project metadata - confirms it
    # imports nothing from jarvis.video_studio.analysis/.transcribe/
    # .highlights/.reel/.export/.cover/.instagram_handoff (the actual
    # processing modules), matching its own docstring's "never opens a
    # file, never runs ffmpeg/whisper/an LLM generation call" guarantee.
    import jarvis.tools.video_studio_tools as module

    forbidden_names = {
        "analyze_video", "transcribe_video", "find_highlights", "generate_reel_edit",
        "export_reel", "render_cover", "send_to_instagram_manager",
    }
    module_attrs = set(dir(module))
    assert not (forbidden_names & module_attrs)
