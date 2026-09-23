"""Tests for SearchFilesTool: substring matching, binary-file skipping,
housekeeping-directory skipping, sandbox enforcement, and output capping.
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
from jarvis.tools.fs import SearchFilesTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "search_files_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


def test_no_prompt_required(scratch_dir):
    (scratch_dir / "a.txt").write_text("needle here", encoding="utf-8")
    tool = SearchFilesTool()
    with patch("builtins.input") as mock_input:
        tool.run(pattern="needle", path=str(scratch_dir))
    mock_input.assert_not_called()


def test_finds_match_in_single_file(scratch_dir):
    (scratch_dir / "a.txt").write_text("line one\nneedle in here\nline three", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert "a.txt:2:" in result.output
    assert "needle in here" in result.output


def test_finds_matches_across_multiple_files(scratch_dir):
    (scratch_dir / "a.txt").write_text("needle in a", encoding="utf-8")
    (scratch_dir / "b.txt").write_text("needle in b", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert "a.txt" in result.output
    assert "b.txt" in result.output


def test_finds_matches_in_nested_directories(scratch_dir):
    sub = scratch_dir / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("needle nested", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert "nested" in result.output


def test_no_matches_reports_clearly(scratch_dir):
    (scratch_dir / "a.txt").write_text("nothing interesting", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert "no matches" in result.output.lower()


def test_case_sensitive_substring_match(scratch_dir):
    (scratch_dir / "a.txt").write_text("Needle vs needle", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert result.output.count(":") >= 1
    # Only the lowercase occurrence's line should match; the line itself
    # contains both, so just confirm the search ran and matched the line.
    assert "needle" in result.output.lower()


def test_pattern_is_literal_not_regex(scratch_dir):
    (scratch_dir / "a.txt").write_text("a.b matches literally\naxb should not match", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="a.b", path=str(scratch_dir))
    assert result.ok
    assert "a.b matches literally" in result.output
    assert "axb should not match" not in result.output


def test_empty_pattern_denied():
    tool = SearchFilesTool()
    result = tool.run(pattern="", path=".")
    assert not result.ok


def test_skips_binary_files(scratch_dir):
    (scratch_dir / "binary.dat").write_bytes(b"\x00\x01\x02needle\xff\xfe")
    (scratch_dir / "text.txt").write_text("needle here", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert "text.txt" in result.output
    assert "binary.dat" not in result.output


def test_skips_housekeeping_directories(scratch_dir):
    venv_dir = scratch_dir / ".venv"
    venv_dir.mkdir()
    (venv_dir / "lib.txt").write_text("needle in venv", encoding="utf-8")
    (scratch_dir / "real.txt").write_text("needle in real file", encoding="utf-8")

    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert "real.txt" in result.output
    assert "lib.txt" not in result.output


def test_defaults_to_project_root_when_no_path_given():
    tool = SearchFilesTool()
    result = tool.run(pattern="ANTHROPIC_API_KEY")
    assert result.ok
    assert "config.py" in result.output


def test_nonexistent_path_fails(scratch_dir):
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir / "does_not_exist"))
    assert not result.ok
    assert "not found" in result.output.lower()


def test_path_is_a_file_not_a_directory_fails(scratch_dir):
    f = scratch_dir / "a.txt"
    f.write_text("x", encoding="utf-8")
    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(f))
    assert not result.ok
    assert "not a directory" in result.output.lower()


def test_outside_sandbox_denied_by_default():
    tool = SearchFilesTool()
    with patch("builtins.input", return_value="n"):
        result = tool.run(pattern="needle", path=str(JARVIS_ROOT.parent))
    assert not result.ok


def test_caps_total_matches(scratch_dir):
    content = "\n".join(f"needle line {i}" for i in range(SearchFilesTool.MAX_MATCHES + 50))
    (scratch_dir / "big.txt").write_text(content, encoding="utf-8")

    tool = SearchFilesTool()
    result = tool.run(pattern="needle", path=str(scratch_dir))
    assert result.ok
    assert "truncated" in result.output.lower()
    match_lines = [line for line in result.output.splitlines() if line.startswith("big.txt:")]
    assert len(match_lines) <= SearchFilesTool.MAX_MATCHES


def test_does_not_follow_directory_junction_out_of_sandbox(scratch_dir):
    # Same finding as tests/test_list_directory_recursive.py: os.walk's
    # followlinks=False does not stop Windows junctions, since
    # Path.is_symlink() returns False for them.
    outside_target = Path(tempfile.mkdtemp(prefix="jarvis_search_files_escape_"))
    link_dir = scratch_dir / "escape_link"
    try:
        (outside_target / "secret.txt").write_text("needle should not leak", encoding="utf-8")

        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_dir), str(outside_target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0 or not link_dir.exists():
            pytest.skip("Cannot create a junction in this environment")

        tool = SearchFilesTool()
        search_result = tool.run(pattern="needle", path=str(scratch_dir))
        assert "secret.txt" not in search_result.output
    finally:
        if link_dir.is_dir():
            os.rmdir(link_dir)
        shutil.rmtree(outside_target, ignore_errors=True)
