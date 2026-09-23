"""Tests for the recursive option on ListDirectoryTool: existing
non-recursive behavior stays exact, recursive walks skip housekeeping
directories, don't follow symlink/junction escapes, and cap output size.
All fixtures operate strictly inside JARVIS_ROOT."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import ListDirectoryTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "list_recursive_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


def test_non_recursive_default_behavior_unchanged(scratch_dir):
    (scratch_dir / "a.txt").write_text("x", encoding="utf-8")
    sub = scratch_dir / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("x", encoding="utf-8")

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir))
    assert result.ok
    assert "a.txt" in result.output
    assert "sub" in result.output
    assert "nested.txt" not in result.output  # one level only


def test_recursive_finds_nested_files(scratch_dir):
    sub = scratch_dir / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("x", encoding="utf-8")

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir), recursive=True)
    assert result.ok
    assert "nested.txt" in result.output


def test_recursive_reports_relative_paths(scratch_dir):
    sub = scratch_dir / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("x", encoding="utf-8")

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir), recursive=True)
    assert "sub\\nested.txt" in result.output or "sub/nested.txt" in result.output


def test_recursive_skips_housekeeping_directories(scratch_dir):
    venv_dir = scratch_dir / ".venv"
    venv_dir.mkdir()
    (venv_dir / "should_not_appear.txt").write_text("x", encoding="utf-8")

    pycache = scratch_dir / "__pycache__"
    pycache.mkdir()
    (pycache / "also_hidden.pyc").write_text("x", encoding="utf-8")

    (scratch_dir / "visible.txt").write_text("x", encoding="utf-8")

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir), recursive=True)
    assert "visible.txt" in result.output
    assert "should_not_appear.txt" not in result.output
    assert "also_hidden.pyc" not in result.output
    assert ".venv" not in result.output
    assert "__pycache__" not in result.output


def test_recursive_empty_directory_reports_empty(scratch_dir):
    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir), recursive=True)
    assert result.ok
    assert "empty" in result.output.lower()


def test_recursive_caps_output_at_max_entries(scratch_dir):
    for i in range(ListDirectoryTool.MAX_RECURSIVE_ENTRIES + 20):
        (scratch_dir / f"file_{i}.txt").write_text("x", encoding="utf-8")

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir), recursive=True)
    assert result.ok
    assert "truncated" in result.output.lower()
    lines = [
        line for line in result.output.splitlines() if line.startswith("file") or line.startswith("dir")
    ]
    assert len(lines) <= ListDirectoryTool.MAX_RECURSIVE_ENTRIES


def test_recursive_does_not_follow_directory_junction_out_of_sandbox(scratch_dir):
    # Same technique as tests/test_sandbox_symlinks.py: junctions work
    # without elevated privilege on Windows, unlike true symlinks.
    outside_target = Path(tempfile.mkdtemp(prefix="jarvis_list_recursive_escape_"))
    link_dir = scratch_dir / "escape_link"
    try:
        (outside_target / "secret_outside.txt").write_text("leaked", encoding="utf-8")

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_dir), str(outside_target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not link_dir.exists():
            pytest.skip("Cannot create a junction in this environment")

        tool = ListDirectoryTool()
        list_result = tool.run(path=str(scratch_dir), recursive=True)
        assert "secret_outside" not in list_result.output
    finally:
        if link_dir.is_dir():
            os.rmdir(link_dir)
        shutil.rmtree(outside_target, ignore_errors=True)


def test_recursive_no_prompt_required(scratch_dir):
    (scratch_dir / "a.txt").write_text("x", encoding="utf-8")
    tool = ListDirectoryTool()
    with patch("builtins.input") as mock_input:
        tool.run(path=str(scratch_dir), recursive=True)
    mock_input.assert_not_called()
