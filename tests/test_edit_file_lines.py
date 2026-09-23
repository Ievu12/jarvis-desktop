"""Tests for EditFileLinesTool: line-range replacement precision (single
line, multi-line, deletion via empty content), exact line-ending
preservation for untouched lines, out-of-range refusal, approval gating,
sandbox enforcement, binary file refusal, and interrupt safety. All
fixtures operate strictly inside JARVIS_ROOT."""

from __future__ import annotations

import shutil
from unittest.mock import patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import EditFileLinesTool


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "edit_file_lines_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


def _approve(*a, **k):
    return "y"


def _deny(*a, **k):
    return "n"


# --- basic replacement behavior ---------------------------------------------


def test_replace_single_line(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=2, end_line=2, new_content="REPLACED\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "line1\nREPLACED\nline3\n"


def test_replace_multi_line_range(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("a\nb\nc\nd\ne\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=2, end_line=4, new_content="X\nY\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "a\nX\nY\ne\n"


def test_replace_first_line(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("old first\nsecond\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=1, end_line=1, new_content="new first\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "new first\nsecond\n"


def test_replace_last_line(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("first\nold last\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=2, end_line=2, new_content="new last\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "first\nnew last\n"


def test_delete_line_via_empty_content(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=2, end_line=2, new_content="")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "a\nc\n"


def test_insert_multiple_lines_in_place_of_one(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=2, end_line=2, new_content="x\ny\nz\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "a\nx\ny\nz\nc\n"


# --- exact preservation of untouched lines -----------------------------------


def test_lines_outside_range_preserved_exactly_including_line_endings(scratch_dir):
    f = scratch_dir / "code.py"
    # Mixed content, ensure untouched lines are byte-identical afterward.
    f.write_text("keep this exactly\nCHANGE ME\nkeep this too\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        tool.run(path=str(f), start_line=2, end_line=2, new_content="changed\n")

    content = f.read_text(encoding="utf-8")
    assert content.startswith("keep this exactly\n")
    assert content.endswith("keep this too\n")


def test_file_without_trailing_newline_handled(scratch_dir):
    f = scratch_dir / "no_trailing_newline.txt"
    f.write_text("line1\nline2", encoding="utf-8")  # no trailing newline
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=1, end_line=1, new_content="LINE1\n")

    assert result.ok
    assert f.read_text(encoding="utf-8") == "LINE1\nline2"


# --- out-of-range refusal ------------------------------------------------------


def test_start_line_beyond_file_length_refused(scratch_dir):
    f = scratch_dir / "short.txt"
    f.write_text("a\nb\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(f), start_line=99, end_line=99, new_content="x")
    assert not result.ok
    assert "beyond the end" in result.output
    mock_input.assert_not_called()


def test_end_line_beyond_file_length_refused(scratch_dir):
    f = scratch_dir / "short.txt"
    f.write_text("a\nb\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(f), start_line=1, end_line=99, new_content="x")
    assert not result.ok
    mock_input.assert_not_called()


def test_start_line_zero_denied():
    tool = EditFileLinesTool()
    result = tool.run(path="x.txt", start_line=0, end_line=1, new_content="x")
    assert not result.ok
    assert "1 or greater" in result.output


def test_start_line_negative_denied():
    tool = EditFileLinesTool()
    result = tool.run(path="x.txt", start_line=-5, end_line=1, new_content="x")
    assert not result.ok


def test_end_line_before_start_line_denied():
    tool = EditFileLinesTool()
    result = tool.run(path="x.txt", start_line=5, end_line=2, new_content="x")
    assert not result.ok
    assert "end_line must be >=" in result.output


def test_out_of_range_never_reaches_approval_prompt(scratch_dir):
    f = scratch_dir / "short.txt"
    f.write_text("a\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input") as mock_input:
        tool.run(path=str(f), start_line=5, end_line=5, new_content="x")
    mock_input.assert_not_called()


# --- approval, preview content, and denial ------------------------------------


def test_denied_leaves_file_untouched(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=str(f), start_line=2, end_line=2, new_content="X\n")

    assert not result.ok
    assert "Denied by user" in result.output
    assert f.read_text(encoding="utf-8") == "a\nb\nc\n"


def test_approval_prompt_shows_old_and_new_content(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("keep\nOLD_LINE_MARKER\nkeep\n", encoding="utf-8")
    tool = EditFileLinesTool()

    captured_prompt = []

    def capture_and_deny(prompt=""):
        captured_prompt.append(prompt)
        return "n"

    with patch("builtins.input", side_effect=capture_and_deny):
        tool.run(path=str(f), start_line=2, end_line=2, new_content="NEW_LINE_MARKER\n")

    full_prompt = " ".join(captured_prompt)
    assert "OLD_LINE_MARKER" in full_prompt
    assert "NEW_LINE_MARKER" in full_prompt


# --- file existence / type checks --------------------------------------------


def test_file_not_found():
    tool = EditFileLinesTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path="does_not_exist_xyz.txt", start_line=1, end_line=1, new_content="x")
    assert not result.ok
    assert "not found" in result.output.lower()
    mock_input.assert_not_called()


def test_path_is_a_directory(scratch_dir):
    tool = EditFileLinesTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(scratch_dir), start_line=1, end_line=1, new_content="x")
    assert not result.ok
    assert "not a file" in result.output.lower()
    mock_input.assert_not_called()


def test_binary_file_refused(scratch_dir):
    f = scratch_dir / "image.png"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
    tool = EditFileLinesTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(path=str(f), start_line=1, end_line=1, new_content="x")
    assert not result.ok
    assert "not valid utf-8" in result.output.lower()
    mock_input.assert_not_called()


# --- staleness guard: file changed after diff shown/approved, before write ------


def test_file_changed_during_approval_wait_is_refused(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    tool = EditFileLinesTool()

    def _approve_and_mutate(*a, **k):
        f.write_text("totally different\n", encoding="utf-8")
        return "y"

    with patch("builtins.input", side_effect=_approve_and_mutate):
        result = tool.run(path=str(f), start_line=2, end_line=2, new_content="X\n")

    assert not result.ok
    assert "changed after the diff" in result.output.lower()
    assert f.read_text(encoding="utf-8") == "totally different\n"


def test_file_unchanged_during_approval_wait_writes_normally(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=_approve):
        result = tool.run(path=str(f), start_line=2, end_line=2, new_content="X\n")
    assert result.ok
    assert f.read_text(encoding="utf-8") == "a\nX\nc\n"


# --- sandbox + interrupt -------------------------------------------------------


def test_outside_sandbox_denied_by_default():
    tool = EditFileLinesTool()
    outside = str(JARVIS_ROOT.parent / "outside.txt")
    with patch("builtins.input", side_effect=_deny):
        result = tool.run(path=outside, start_line=1, end_line=1, new_content="x")
    assert not result.ok


def test_keyboard_interrupt_propagates_and_does_not_write(scratch_dir):
    f = scratch_dir / "code.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    tool = EditFileLinesTool()
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            tool.run(path=str(f), start_line=2, end_line=2, new_content="X\n")
    assert f.read_text(encoding="utf-8") == "a\nb\nc\n"
