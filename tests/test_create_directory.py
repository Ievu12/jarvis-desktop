"""Tests for CreateDirectoryTool: sandbox enforcement, approval gating,
idempotent no-op when the directory already exists, refusal when a file
occupies the target path, and interrupt safety. All fixtures operate
strictly inside JARVIS_ROOT."""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import CreateDirectoryTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "create_dir_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


def test_create_directory_requires_approval(scratch_dir):
    tool = CreateDirectoryTool()
    target = scratch_dir / "newdir"
    with patch("builtins.input", side_effect=_approve) as mock_input:
        result = tool.run(path=str(target))
    mock_input.assert_called()
    assert result.ok
    assert target.is_dir()


def test_create_directory_denied_does_not_create(scratch_dir):
    tool = CreateDirectoryTool()
    target = scratch_dir / "newdir"
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(target))
    assert not result.ok
    assert "Denied by user" in result.output
    assert not target.exists()


def test_create_directory_creates_nested_parents(scratch_dir):
    tool = CreateDirectoryTool()
    target = scratch_dir / "a" / "b" / "c"
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target))
    assert result.ok
    assert target.is_dir()
    assert (scratch_dir / "a").is_dir()
    assert (scratch_dir / "a" / "b").is_dir()


def test_create_directory_already_exists_is_idempotent_no_prompt(scratch_dir):
    tool = CreateDirectoryTool()
    target = scratch_dir / "existing"
    target.mkdir()

    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(target))

    mock_input.assert_not_called()
    assert result.ok
    assert "already exists" in result.output.lower()


def test_create_directory_refuses_when_file_occupies_path(scratch_dir):
    tool = CreateDirectoryTool()
    target = scratch_dir / "occupied"
    target.write_text("i am a file", encoding="utf-8")

    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(target))

    mock_input.assert_not_called()
    assert not result.ok
    assert "file already exists" in result.output.lower()
    assert target.is_file()  # untouched


def test_create_directory_outside_sandbox_denied_by_default():
    tool = CreateDirectoryTool()
    outside = JARVIS_ROOT.parent / "some_outside_new_dir"
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(outside))
    assert not result.ok
    assert not outside.exists()


def test_create_directory_dotdot_traversal_denied():
    tool = CreateDirectoryTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path="../../outside_traversal_dir")
    assert not result.ok


def test_create_directory_keyboard_interrupt_propagates_and_does_not_create(scratch_dir):
    tool = CreateDirectoryTool()
    target = scratch_dir / "interrupted"
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            tool.run(path=str(target))
    assert not target.exists()


def test_create_directory_relative_path_resolves_inside_sandbox(scratch_dir):
    tool = CreateDirectoryTool()
    rel = str((scratch_dir / "relnew").relative_to(JARVIS_ROOT))
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=rel)
    assert result.ok
    assert (scratch_dir / "relnew").is_dir()
