"""Tests for MoveFileTool: dual-path sandbox enforcement (source and
destination independently), no-overwrite refusal, files-only scoping,
approval gating, and interrupt safety. All fixtures operate strictly
inside JARVIS_ROOT."""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import MoveFileTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "move_file_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


@pytest.fixture
def source_file(scratch_dir):
    f = scratch_dir / "source.txt"
    f.write_text("move me", encoding="utf-8")
    return f


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


# --- basic move behavior ------------------------------------------------


def test_move_requires_approval(scratch_dir, source_file):
    tool = MoveFileTool()
    dest = scratch_dir / "dest.txt"
    with patch("builtins.input", side_effect=_approve) as mock_input:
        result = tool.run(source=str(source_file), destination=str(dest))
    mock_input.assert_called()
    assert result.ok
    assert not source_file.exists()
    assert dest.exists()
    assert dest.read_text(encoding="utf-8") == "move me"


def test_move_denied_leaves_both_paths_untouched(scratch_dir, source_file):
    tool = MoveFileTool()
    dest = scratch_dir / "dest.txt"
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(source=str(source_file), destination=str(dest))
    assert not result.ok
    assert "Denied by user" in result.output
    assert source_file.exists()
    assert not dest.exists()


def test_move_creates_missing_destination_parents(scratch_dir, source_file):
    tool = MoveFileTool()
    dest = scratch_dir / "newsub" / "dest.txt"
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(source=str(source_file), destination=str(dest))
    assert result.ok
    assert dest.exists()
    assert not source_file.exists()


def test_move_relative_paths_resolve_inside_sandbox(scratch_dir, source_file):
    tool = MoveFileTool()
    rel_source = str(source_file.relative_to(JARVIS_ROOT))
    rel_dest = str((scratch_dir / "renamed.txt").relative_to(JARVIS_ROOT))
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(source=rel_source, destination=rel_dest)
    assert result.ok
    assert (scratch_dir / "renamed.txt").exists()


# --- no-overwrite refusal -------------------------------------------------


def test_move_refuses_when_destination_already_exists(scratch_dir, source_file):
    tool = MoveFileTool()
    dest = scratch_dir / "existing_dest.txt"
    dest.write_text("already here", encoding="utf-8")

    with patch("builtins.input") as mock_input:
        result = tool.run(source=str(source_file), destination=str(dest))

    mock_input.assert_not_called()
    assert not result.ok
    assert "already exists" in result.output.lower()
    # Neither side touched.
    assert source_file.exists()
    assert dest.read_text(encoding="utf-8") == "already here"


# --- files only, no directories -------------------------------------------


def test_move_refuses_when_source_is_a_directory(scratch_dir):
    tool = MoveFileTool()
    src_dir = scratch_dir / "a_directory"
    src_dir.mkdir()
    dest = scratch_dir / "moved_dir"

    with patch("builtins.input") as mock_input:
        result = tool.run(source=str(src_dir), destination=str(dest))

    mock_input.assert_not_called()
    assert not result.ok
    assert "directory" in result.output.lower()
    assert src_dir.exists()
    assert not dest.exists()


def test_move_source_not_found(scratch_dir):
    tool = MoveFileTool()
    missing = scratch_dir / "does_not_exist.txt"
    dest = scratch_dir / "dest.txt"

    with patch("builtins.input") as mock_input:
        result = tool.run(source=str(missing), destination=str(dest))

    mock_input.assert_not_called()
    assert not result.ok
    assert "not found" in result.output.lower()


# --- dual-path sandbox enforcement -----------------------------------------


def test_move_source_outside_sandbox_denied_by_default(scratch_dir):
    tool = MoveFileTool()
    outside_source = JARVIS_ROOT.parent / "outside_source.txt"
    dest = scratch_dir / "dest.txt"

    with patch("builtins.input", side_effect=_deny):
        result = tool.run(source=str(outside_source), destination=str(dest))

    assert not result.ok
    assert not dest.exists()


def test_move_destination_outside_sandbox_denied_by_default(scratch_dir, source_file):
    tool = MoveFileTool()
    outside_dest = JARVIS_ROOT.parent / "outside_dest.txt"

    with patch("builtins.input", side_effect=_deny):
        result = tool.run(source=str(source_file), destination=str(outside_dest))

    assert not result.ok
    assert source_file.exists()
    assert not outside_dest.exists()


def test_move_destination_outside_sandbox_prompts_twice_when_both_approved(
    scratch_dir, source_file
):
    # First approval is for the destination's outside-sandbox exception
    # (request_outside_access), second is the move's own side-effect
    # confirmation. Both must be explicitly approved for the move to happen.
    tool = MoveFileTool()
    outside_dest = JARVIS_ROOT.parent / "jarvis_move_test_outside_tmp.txt"
    try:
        with patch("builtins.input", side_effect=[_approve(), _approve()]) as mock_input:
            result = tool.run(source=str(source_file), destination=str(outside_dest))
        assert mock_input.call_count == 2
        assert result.ok
        assert outside_dest.exists()
        assert not source_file.exists()
    finally:
        if outside_dest.exists():
            outside_dest.unlink()


def test_move_dotdot_traversal_source_denied():
    tool = MoveFileTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(source="../../outside_traversal.txt", destination="dest.txt")
    assert not result.ok


# --- interrupt safety --------------------------------------------------------


def test_move_keyboard_interrupt_during_confirmation_propagates_and_does_not_move(
    scratch_dir, source_file
):
    tool = MoveFileTool()
    dest = scratch_dir / "dest.txt"
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            tool.run(source=str(source_file), destination=str(dest))
    assert source_file.exists()
    assert not dest.exists()
