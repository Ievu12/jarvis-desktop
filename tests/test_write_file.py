"""Tests for WriteFileTool: diff shown in the approval prompt (not just a
"write N chars" summary), the write-time staleness guard (a file changed
after the diff was shown and approved is refused, not silently
overwritten), approval gating, sandbox enforcement, and binary-file
handling. All fixtures operate strictly inside JARVIS_ROOT."""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import WriteFileTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "write_file_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


# --- approval prompt shows a real diff, not just a length -------------------------


def test_new_file_approval_shows_content_preview(scratch_dir):
    target = scratch_dir / "new.txt"
    tool = WriteFileTool()
    with patch("builtins.input") as mock_input:
        mock_input.side_effect = _approve
        tool.run(path=str(target), content="hello world\n")
    prompt_text = mock_input.call_args[0][0]
    assert "hello world" in prompt_text
    assert "New file" in prompt_text


def test_overwriting_existing_file_shows_diff_not_just_char_count(scratch_dir):
    target = scratch_dir / "existing.txt"
    target.write_text("old line\n", encoding="utf-8")
    tool = WriteFileTool()
    with patch("builtins.input") as mock_input:
        mock_input.side_effect = _approve
        tool.run(path=str(target), content="new line\n")
    prompt_text = mock_input.call_args[0][0]
    assert "-old line" in prompt_text
    assert "+new line" in prompt_text
    # The old approval text was exactly "write N chars to <path>" with no
    # diff - confirm that bare summary is gone, replaced by real content.
    assert "chars to" not in prompt_text


def test_identical_content_overwrite_shows_no_changes_message(scratch_dir):
    target = scratch_dir / "same.txt"
    target.write_text("unchanged\n", encoding="utf-8")
    tool = WriteFileTool()
    with patch("builtins.input") as mock_input:
        mock_input.side_effect = _approve
        tool.run(path=str(target), content="unchanged\n")
    prompt_text = mock_input.call_args[0][0]
    assert "no changes" in prompt_text.lower()


# --- basic write behavior (still works) ---------------------------------------------


def test_write_creates_new_file(scratch_dir):
    target = scratch_dir / "created.txt"
    tool = WriteFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target), content="content here")
    assert result.ok
    assert target.read_text(encoding="utf-8") == "content here"


def test_write_overwrites_existing_file(scratch_dir):
    target = scratch_dir / "overwrite.txt"
    target.write_text("original", encoding="utf-8")
    tool = WriteFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target), content="replaced")
    assert result.ok
    assert target.read_text(encoding="utf-8") == "replaced"


def test_write_denied_by_user_does_not_write(scratch_dir):
    target = scratch_dir / "denied.txt"
    tool = WriteFileTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(target), content="should not appear")
    assert not result.ok
    assert not target.exists()


def test_write_creates_missing_parent_directories(scratch_dir):
    target = scratch_dir / "nested" / "dir" / "file.txt"
    tool = WriteFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target), content="deep")
    assert result.ok
    assert target.read_text(encoding="utf-8") == "deep"


# --- staleness guard: file changed after diff shown/approved, before write --------


def test_file_changed_during_approval_wait_is_refused(scratch_dir):
    target = scratch_dir / "race.txt"
    target.write_text("original\n", encoding="utf-8")
    tool = WriteFileTool()

    def _approve_and_mutate(*a, **k):
        # Simulates the file being edited by hand (or another process)
        # while the human is looking at the approval prompt.
        target.write_text("changed from outside\n", encoding="utf-8")
        return "y"

    with patch("builtins.input", side_effect=_approve_and_mutate):
        result = tool.run(path=str(target), content="proposed content\n")

    assert not result.ok
    assert "changed after the diff" in result.output.lower()
    # The file must retain whatever it was mutated to, not be silently
    # overwritten with the stale-approved content.
    assert target.read_text(encoding="utf-8") == "changed from outside\n"


def test_file_unchanged_during_approval_wait_writes_normally(scratch_dir):
    target = scratch_dir / "no_race.txt"
    target.write_text("original\n", encoding="utf-8")
    tool = WriteFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target), content="updated\n")
    assert result.ok
    assert target.read_text(encoding="utf-8") == "updated\n"


def test_new_file_race_not_applicable_when_file_still_does_not_exist(scratch_dir):
    # A brand-new file (current == "") has no staleness baseline - the
    # guard only applies to files that already existed at diff time.
    target = scratch_dir / "brand_new.txt"
    tool = WriteFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target), content="first content\n")
    assert result.ok
    assert target.read_text(encoding="utf-8") == "first content\n"


def test_file_created_during_approval_wait_for_new_file_write_still_succeeds(scratch_dir):
    # If the file didn't exist when the diff was shown (current == ""),
    # but something else creates it while waiting for approval, this
    # tool's own guard (which only fires for current != "") does not
    # block the write - it proceeds and overwrites, same as the
    # historical behavior for a brand-new file target.
    target = scratch_dir / "appears_during_wait.txt"
    tool = WriteFileTool()

    def _approve_and_create(*a, **k):
        target.write_text("appeared from outside\n", encoding="utf-8")
        return "y"

    with patch("builtins.input", side_effect=_approve_and_create):
        result = tool.run(path=str(target), content="proposed content\n")

    assert result.ok
    assert target.read_text(encoding="utf-8") == "proposed content\n"


# --- binary file handling -----------------------------------------------------------


def test_overwriting_binary_file_shows_fallback_summary_not_crash(scratch_dir):
    target = scratch_dir / "binary.dat"
    target.write_bytes(b"\xff\xfe\x00\x01binary")
    tool = WriteFileTool()
    with patch("builtins.input") as mock_input:
        mock_input.side_effect = _approve
        result = tool.run(path=str(target), content="new text content")
    assert result.ok
    prompt_text = mock_input.call_args[0][0]
    assert "not valid UTF-8" in prompt_text


# --- approval is always required ------------------------------------------------------


def test_write_requires_approval(scratch_dir):
    target = scratch_dir / "approval_required.txt"
    tool = WriteFileTool()
    with patch("builtins.input", side_effect=_approve) as mock_input:
        tool.run(path=str(target), content="x")
    mock_input.assert_called()
