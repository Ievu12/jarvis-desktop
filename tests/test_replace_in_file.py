"""Tests for ReplaceInFileTool: literal substring replacement, count
limiting, zero-match handling, approval gating, sandbox enforcement,
binary-file refusal, and interrupt safety. All fixtures operate strictly
inside JARVIS_ROOT."""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import ReplaceInFileTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "replace_in_file_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


@pytest.fixture
def target_file(scratch_dir):
    f = scratch_dir / "sample.py"
    f.write_text("def old_name():\n    return old_name() + 1\n", encoding="utf-8")
    return f


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


# --- basic replacement behavior ---------------------------------------------


def test_replace_all_occurrences(target_file):
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target_file), search="old_name", replacement="new_name")

    assert result.ok
    assert "2 occurrence" in result.output
    content = target_file.read_text(encoding="utf-8")
    assert "old_name" not in content
    assert content.count("new_name") == 2


def test_replace_requires_approval(target_file):
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve) as mock_input:
        tool.run(path=str(target_file), search="old_name", replacement="new_name")
    mock_input.assert_called()


def test_replace_denied_leaves_file_untouched(target_file):
    original = target_file.read_text(encoding="utf-8")
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(target_file), search="old_name", replacement="new_name")

    assert not result.ok
    assert "Denied by user" in result.output
    assert target_file.read_text(encoding="utf-8") == original


def test_no_match_reports_zero_without_prompting(target_file):
    tool = ReplaceInFileTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(target_file), search="nonexistent_text", replacement="x")

    assert result.ok
    assert "no occurrences" in result.output.lower()
    mock_input.assert_not_called()
    # File genuinely untouched.
    assert "def old_name" in target_file.read_text(encoding="utf-8")


def test_replace_is_case_sensitive(scratch_dir):
    f = scratch_dir / "case.txt"
    f.write_text("Hello hello HELLO", encoding="utf-8")
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), search="hello", replacement="hi")

    assert result.ok
    assert "1 occurrence" in result.output
    assert f.read_text(encoding="utf-8") == "Hello hi HELLO"


def test_replace_pattern_is_literal_not_regex(scratch_dir):
    f = scratch_dir / "literal.txt"
    f.write_text("a.b and axb", encoding="utf-8")
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), search="a.b", replacement="MATCHED")

    assert result.ok
    assert "1 occurrence" in result.output
    content = f.read_text(encoding="utf-8")
    assert "MATCHED" in content
    assert "axb" in content  # not affected - '.' is literal, not a regex wildcard


# --- count limiting --------------------------------------------------------


def test_count_limits_replacements_from_start_of_file(scratch_dir):
    f = scratch_dir / "repeated.txt"
    f.write_text("a a a a", encoding="utf-8")
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), search="a", replacement="b", count=2)

    assert result.ok
    assert "2 occurrence" in result.output
    assert f.read_text(encoding="utf-8") == "b b a a"


def test_count_greater_than_actual_occurrences_replaces_all(scratch_dir):
    f = scratch_dir / "few.txt"
    f.write_text("x x", encoding="utf-8")
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), search="x", replacement="y", count=100)

    assert result.ok
    assert "2 occurrence" in result.output  # capped to actual count, not requested count
    assert f.read_text(encoding="utf-8") == "y y"


def test_count_zero_or_negative_denied():
    tool = ReplaceInFileTool()
    result = tool.run(path="x.txt", search="a", replacement="b", count=0)
    assert not result.ok
    result2 = tool.run(path="x.txt", search="a", replacement="b", count=-1)
    assert not result2.ok


# --- input validation --------------------------------------------------------


def test_empty_search_text_denied():
    tool = ReplaceInFileTool()
    result = tool.run(path="x.txt", search="", replacement="y")
    assert not result.ok


def test_empty_replacement_is_allowed_deletes_text(scratch_dir):
    f = scratch_dir / "delete_text.txt"
    f.write_text("remove THIS word", encoding="utf-8")
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), search="THIS ", replacement="")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "remove word"


def test_file_not_found():
    tool = ReplaceInFileTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path="does_not_exist_xyz.txt", search="a", replacement="b")
    assert not result.ok
    assert "not found" in result.output.lower()
    mock_input.assert_not_called()


def test_path_is_a_directory_not_a_file(scratch_dir):
    tool = ReplaceInFileTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(scratch_dir), search="a", replacement="b")
    assert not result.ok
    assert "not a file" in result.output.lower()
    mock_input.assert_not_called()


# --- binary file handling ----------------------------------------------------


def test_binary_file_refused(scratch_dir):
    f = scratch_dir / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    tool = ReplaceInFileTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(f), search="a", replacement="b")
    assert not result.ok
    assert "not valid utf-8" in result.output.lower()
    mock_input.assert_not_called()


# --- sandbox enforcement -----------------------------------------------------


def test_outside_sandbox_denied_by_default():
    tool = ReplaceInFileTool()
    outside = str(JARVIS_ROOT.parent / "outside.txt")
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=outside, search="a", replacement="b")
    assert not result.ok


def test_dotdot_traversal_denied():
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path="../../outside_traversal.txt", search="a", replacement="b")
    assert not result.ok


# --- approval prompt shows a real diff -----------------------------------------


def test_approval_prompt_shows_diff_not_just_count(target_file):
    tool = ReplaceInFileTool()
    with patch("builtins.input") as mock_input:
        mock_input.side_effect = _approve
        tool.run(path=str(target_file), search="old_name", replacement="new_name")
    prompt_text = mock_input.call_args[0][0]
    assert "-def old_name" in prompt_text
    assert "+def new_name" in prompt_text


# --- staleness guard: file changed after diff shown/approved, before write ------


def test_file_changed_during_approval_wait_is_refused(target_file):
    tool = ReplaceInFileTool()

    def _approve_and_mutate(*a, **k):
        target_file.write_text("something else entirely\n", encoding="utf-8")
        return "y"

    with patch("builtins.input", side_effect=_approve_and_mutate):
        result = tool.run(path=str(target_file), search="old_name", replacement="new_name")

    assert not result.ok
    assert "changed after the diff" in result.output.lower()
    assert target_file.read_text(encoding="utf-8") == "something else entirely\n"


def test_file_unchanged_during_approval_wait_writes_normally(target_file):
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(target_file), search="old_name", replacement="new_name")
    assert result.ok
    assert "new_name" in target_file.read_text(encoding="utf-8")


# --- interrupt safety --------------------------------------------------------


def test_keyboard_interrupt_during_confirmation_propagates_and_does_not_write(target_file):
    original = target_file.read_text(encoding="utf-8")
    tool = ReplaceInFileTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            tool.run(path=str(target_file), search="old_name", replacement="new_name")
    assert target_file.read_text(encoding="utf-8") == original
