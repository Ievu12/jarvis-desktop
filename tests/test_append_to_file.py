"""Tests for AppendToFileTool: appends without touching existing content,
creates the file if missing, approval gating, sandbox enforcement, binary
file refusal, and interrupt safety. All fixtures operate strictly inside
JARVIS_ROOT."""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import AppendToFileTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "append_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


def test_append_to_existing_file_preserves_prior_content(scratch_dir):
    f = scratch_dir / "log.txt"
    f.write_text("line one\n", encoding="utf-8")

    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), content="line two\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "line one\nline two\n"


def test_append_requires_approval(scratch_dir):
    f = scratch_dir / "log.txt"
    f.write_text("existing\n", encoding="utf-8")
    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=_approve) as mock_input:
        tool.run(path=str(f), content="more\n")
    mock_input.assert_called()


def test_append_denied_leaves_file_untouched(scratch_dir):
    f = scratch_dir / "log.txt"
    f.write_text("existing\n", encoding="utf-8")
    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(f), content="more\n")

    assert not result.ok
    assert "Denied by user" in result.output
    assert f.read_text(encoding="utf-8") == "existing\n"


def test_append_creates_missing_file(scratch_dir):
    f = scratch_dir / "new.txt"
    assert not f.exists()
    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), content="first content\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "first content\n"


def test_append_creates_missing_parent_directories(scratch_dir):
    f = scratch_dir / "nested" / "deep" / "new.txt"
    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), content="content\n")

    assert result.ok
    assert f.exists()
    assert f.read_text(encoding="utf-8") == "content\n"


def test_append_multiple_times_accumulates(scratch_dir):
    f = scratch_dir / "accumulate.txt"
    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=_approve):
        tool.run(path=str(f), content="a\n")
        tool.run(path=str(f), content="b\n")
        tool.run(path=str(f), content="c\n")

    assert f.read_text(encoding="utf-8") == "a\nb\nc\n"


def test_append_to_a_directory_refused(scratch_dir):
    tool = AppendToFileTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(scratch_dir), content="x")
    assert not result.ok
    assert "not a file" in result.output.lower()
    mock_input.assert_not_called()


def test_append_to_binary_file_refused(scratch_dir):
    f = scratch_dir / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    tool = AppendToFileTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(f), content="x")
    assert not result.ok
    assert "not valid utf-8" in result.output.lower()
    mock_input.assert_not_called()


# --- approval prompt shows a real diff -----------------------------------------


def test_append_approval_shows_diff_of_added_content(scratch_dir):
    f = scratch_dir / "log.txt"
    f.write_text("line one\n", encoding="utf-8")
    tool = AppendToFileTool()
    with patch("builtins.input") as mock_input:
        mock_input.side_effect = _approve
        tool.run(path=str(f), content="line two\n")
    prompt_text = mock_input.call_args[0][0]
    assert "+line two" in prompt_text
    assert "line one" in prompt_text  # unchanged context shown too


def test_append_to_new_file_approval_shows_preview(scratch_dir):
    f = scratch_dir / "brand_new.txt"
    tool = AppendToFileTool()
    with patch("builtins.input") as mock_input:
        mock_input.side_effect = _approve
        tool.run(path=str(f), content="first line\n")
    prompt_text = mock_input.call_args[0][0]
    assert "first line" in prompt_text
    assert "New file" in prompt_text


# --- staleness guard: file changed after diff shown/approved, before write ------


def test_file_changed_during_approval_wait_is_refused(scratch_dir):
    f = scratch_dir / "log.txt"
    f.write_text("original\n", encoding="utf-8")
    tool = AppendToFileTool()

    def _approve_and_mutate(*a, **k):
        f.write_text("changed from outside\n", encoding="utf-8")
        return "y"

    with patch("builtins.input", side_effect=_approve_and_mutate):
        result = tool.run(path=str(f), content="new line\n")

    assert not result.ok
    assert "changed after the diff" in result.output.lower()
    assert f.read_text(encoding="utf-8") == "changed from outside\n"


def test_file_unchanged_during_approval_wait_appends_normally(scratch_dir):
    f = scratch_dir / "log.txt"
    f.write_text("original\n", encoding="utf-8")
    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), content="new line\n")
    assert result.ok
    assert f.read_text(encoding="utf-8") == "original\nnew line\n"


def test_outside_sandbox_denied_by_default():
    tool = AppendToFileTool()
    outside = str(JARVIS_ROOT.parent / "outside.txt")
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=outside, content="x")
    assert not result.ok


def test_keyboard_interrupt_propagates_and_does_not_write(scratch_dir):
    f = scratch_dir / "log.txt"
    f.write_text("original\n", encoding="utf-8")
    tool = AppendToFileTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            tool.run(path=str(f), content="new\n")
    assert f.read_text(encoding="utf-8") == "original\n"
