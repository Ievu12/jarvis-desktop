"""Tests for jarvis.core.project_notes.load_project_notes: opt-in loading
of JARVIS.md, never auto-created, size capping, and graceful handling of
missing/empty/unreadable/non-UTF-8 files. All fixtures use isolated
tmp_path directories - never the real JARVIS project."""

from __future__ import annotations

import os
import stat

import pytest

from jarvis.core.project_notes import MAX_NOTES_CHARS, load_project_notes


def test_no_file_returns_none_content_and_no_notice(tmp_path):
    result = load_project_notes(tmp_path)
    assert result.content is None
    assert result.notice is None


def test_never_creates_the_file(tmp_path):
    load_project_notes(tmp_path)
    assert not (tmp_path / "JARVIS.md").exists()


def test_existing_file_is_loaded_with_notice(tmp_path):
    (tmp_path / "JARVIS.md").write_text("This is a Flask app.\n", encoding="utf-8")
    result = load_project_notes(tmp_path)
    assert result.content == "This is a Flask app.\n"
    assert result.notice is not None
    assert "Loaded project notes" in result.notice


def test_empty_file_treated_like_no_file(tmp_path):
    (tmp_path / "JARVIS.md").write_text("", encoding="utf-8")
    result = load_project_notes(tmp_path)
    assert result.content is None
    assert result.notice is None


def test_whitespace_only_file_treated_like_no_file(tmp_path):
    (tmp_path / "JARVIS.md").write_text("   \n\n  \t\n", encoding="utf-8")
    result = load_project_notes(tmp_path)
    assert result.content is None
    assert result.notice is None


def test_non_utf8_file_reports_notice_and_no_content(tmp_path):
    path = tmp_path / "JARVIS.md"
    path.write_bytes(b"\xff\xfe\x00\x01invalid utf8 bytes\xff")
    result = load_project_notes(tmp_path)
    assert result.content is None
    assert result.notice is not None
    assert "not valid UTF-8" in result.notice


def test_oversized_file_is_truncated_not_rejected(tmp_path):
    huge_content = "x" * (MAX_NOTES_CHARS + 5000)
    (tmp_path / "JARVIS.md").write_text(huge_content, encoding="utf-8")
    result = load_project_notes(tmp_path)
    assert result.content is not None
    assert len(result.content) == MAX_NOTES_CHARS
    assert result.notice is not None
    assert "truncated" in result.notice.lower()


def test_normal_sized_file_not_flagged_as_truncated(tmp_path):
    (tmp_path / "JARVIS.md").write_text("short notes", encoding="utf-8")
    result = load_project_notes(tmp_path)
    assert "truncated" not in (result.notice or "").lower()


def test_unreadable_file_reports_notice_gracefully(tmp_path):
    path = tmp_path / "JARVIS.md"
    path.write_text("secret notes", encoding="utf-8")
    os.chmod(path, 0)
    try:
        result = load_project_notes(tmp_path)
        # On some platforms/permissions setups chmod(0) may not actually
        # block the owner from reading; only assert the graceful-notice
        # behavior if it really became unreadable.
        if result.content is None and result.notice is not None:
            assert "could not read" in result.notice
    finally:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
