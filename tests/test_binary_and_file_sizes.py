"""Tests for read_file's binary-file metadata reporting and
list_directory's file-size display, in both non-recursive and recursive
modes. All fixtures operate strictly inside JARVIS_ROOT."""

from __future__ import annotations

import shutil

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.tools.fs import ListDirectoryTool, ReadFileTool, _format_size


@pytest.fixture
def scratch_dir():
    d = JARVIS_ROOT / ".jarvis" / "binary_size_test_scratch"
    d.mkdir(parents=True, exist_ok=True)
    yield d
    if d.exists():
        shutil.rmtree(d)


# --- _format_size -------------------------------------------------------


@pytest.mark.parametrize(
    "num_bytes,expected_unit",
    [
        (0, "B"),
        (500, "B"),
        (1023, "B"),
        (1024, "KB"),
        (1024 * 1024 - 1, "KB"),
        (1024 * 1024, "MB"),
        (1024 * 1024 * 1024, "GB"),
    ],
)
def test_format_size_picks_correct_unit(num_bytes, expected_unit):
    assert _format_size(num_bytes).endswith(expected_unit)


def test_format_size_never_negative_or_empty():
    assert _format_size(0) != ""


# --- read_file: binary file handling --------------------------------------


def test_read_file_binary_reports_metadata_not_error(scratch_dir):
    target = scratch_dir / "image.png"
    target.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 5000)

    tool = ReadFileTool()
    result = tool.run(path=str(target))

    assert result.ok  # metadata report is a success, not a failure
    assert "not valid UTF-8" in result.output
    assert ".png" in result.output
    assert "5008 bytes" in result.output or "4.9 KB" in result.output


def test_read_file_binary_never_returns_raw_bytes_as_text(scratch_dir):
    target = scratch_dir / "data.bin"
    raw = bytes(range(256))
    target.write_bytes(raw)

    tool = ReadFileTool()
    result = tool.run(path=str(target))

    assert result.ok
    # The output must be a metadata description, not an attempt to decode
    # the raw bytes into the result string.
    assert "\x00" not in result.output
    assert "256 bytes" in result.output


def test_read_file_binary_reports_correct_extension(scratch_dir):
    target = scratch_dir / "archive.zip"
    target.write_bytes(b"PK\x03\x04" + b"\xff" * 100)

    tool = ReadFileTool()
    result = tool.run(path=str(target))

    assert result.ok
    assert ".zip" in result.output


def test_read_file_binary_no_extension_handled_gracefully(scratch_dir):
    target = scratch_dir / "noext"
    target.write_bytes(b"\xff\xfe\x00\x01" * 10)

    tool = ReadFileTool()
    result = tool.run(path=str(target))

    assert result.ok
    assert "no extension" in result.output.lower()


def test_read_file_valid_utf8_text_unaffected(scratch_dir):
    target = scratch_dir / "notes.txt"
    target.write_text("hello world", encoding="utf-8")

    tool = ReadFileTool()
    result = tool.run(path=str(target))

    assert result.ok
    assert result.output == "hello world"  # exact content, not a metadata description


# --- list_directory: file sizes ---------------------------------------------


def test_list_directory_shows_file_size(scratch_dir):
    (scratch_dir / "a.txt").write_text("x" * 2000, encoding="utf-8")

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir))

    assert result.ok
    assert "a.txt" in result.output
    assert "KB" in result.output or "B" in result.output


def test_list_directory_directories_have_no_size_shown(scratch_dir):
    (scratch_dir / "subdir").mkdir()

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir))

    subdir_line = next(line for line in result.output.splitlines() if "subdir" in line)
    assert "(" not in subdir_line  # no size parens on a directory entry


def test_list_directory_recursive_shows_file_sizes(scratch_dir):
    sub = scratch_dir / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("y" * 100, encoding="utf-8")

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir), recursive=True)

    nested_line = next(line for line in result.output.splitlines() if "nested.txt" in line)
    assert "B" in nested_line


def test_list_directory_size_matches_actual_byte_count(scratch_dir):
    content = "z" * 1500
    (scratch_dir / "sized.txt").write_bytes(content.encode("utf-8"))

    tool = ListDirectoryTool()
    result = tool.run(path=str(scratch_dir))

    sized_line = next(line for line in result.output.splitlines() if "sized.txt" in line)
    assert "1.5 KB" in sized_line
