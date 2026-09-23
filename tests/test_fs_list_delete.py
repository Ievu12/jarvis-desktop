"""Tests for ListDirectoryTool and DeleteFileTool: sandbox enforcement,
approval gating for deletion, and refusal to delete directories. All
fixtures create and clean up scratch files strictly inside JARVIS_ROOT."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import DeleteFileTool, ListDirectoryTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "list_delete_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    import shutil

    if d.exists():
        shutil.rmtree(d)


@pytest.fixture
def scratch_file(scratch_dir):
    f = scratch_dir / "sample.txt"
    f.write_text("sample content", encoding="utf-8")
    return f


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


# --- ListDirectoryTool ---------------------------------------------------


def test_list_directory_no_prompt_required(scratch_dir, scratch_file):
    tool = ListDirectoryTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(scratch_dir))
    mock_input.assert_not_called()
    assert result.ok


def test_list_directory_shows_files(scratch_dir, scratch_file):
    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir))
    assert result.ok
    assert "sample.txt" in result.output


def test_list_directory_shows_subdirectories(scratch_dir):
    sub = scratch_dir / "subdir"
    sub.mkdir()
    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir))
    assert result.ok
    assert "subdir" in result.output


def test_list_directory_empty_dir_reports_empty(scratch_dir):
    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir))
    assert result.ok
    assert "empty" in result.output.lower()


def test_list_directory_nonexistent_path_fails(scratch_dir):
    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir / "does_not_exist"))
    assert not result.ok
    assert "not found" in result.output.lower()


def test_list_directory_on_a_file_fails(scratch_file):
    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_file))
    assert not result.ok
    assert "not a directory" in result.output.lower()


def test_list_directory_defaults_to_project_root_when_no_path_given():
    tool = ListDirectoryTool()
    result = tool.run()
    assert result.ok


def test_list_directory_outside_sandbox_denied_by_default():
    tool = ListDirectoryTool()
    outside = JARVIS_ROOT.parent
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(outside))
    assert not result.ok


def test_list_directory_relative_path_resolves_inside_sandbox(scratch_dir, scratch_file):
    tool = ListDirectoryTool()
    rel = str(scratch_dir.relative_to(JARVIS_ROOT))
    result = tool.run(path=rel)
    assert result.ok
    assert "sample.txt" in result.output


# --- DeleteFileTool --------------------------------------------------------


def test_delete_file_requires_approval(scratch_file):
    tool = DeleteFileTool()
    with patch("builtins.input", side_effect=_approve) as mock_input:
        result = tool.run(path=str(scratch_file))
    mock_input.assert_called()
    assert result.ok
    assert not scratch_file.exists()


def test_delete_file_denied_leaves_file_intact(scratch_file):
    tool = DeleteFileTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(scratch_file))
    assert not result.ok
    assert "Denied by user" in result.output
    assert scratch_file.exists()


def test_delete_file_nonexistent_path_fails_without_prompt(scratch_dir):
    tool = DeleteFileTool()
    missing = scratch_dir / "nope.txt"
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(missing))
    assert not result.ok
    assert "not found" in result.output.lower()
    mock_input.assert_not_called()


def test_delete_file_refuses_to_delete_a_directory(scratch_dir):
    tool = DeleteFileTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(scratch_dir))
    assert not result.ok
    assert "directory" in result.output.lower()
    mock_input.assert_not_called()
    assert scratch_dir.exists()


def test_delete_file_outside_sandbox_denied_by_default():
    tool = DeleteFileTool()
    outside = JARVIS_ROOT.parent / "some_outside_file.txt"
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(outside))
    assert not result.ok


def test_delete_file_keyboard_interrupt_propagates_and_does_not_delete(scratch_file):
    tool = DeleteFileTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            tool.run(path=str(scratch_file))
    assert scratch_file.exists()


def test_delete_file_dotdot_traversal_denied():
    tool = DeleteFileTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path="../../outside_traversal.txt")
    assert not result.ok
