"""Tests for jarvis.core.file_diff.format_unified_diff: pure diff
formatting between two in-memory strings, no filesystem access, no side
effects. Used by every file-writing tool's approval prompt."""

from __future__ import annotations

from jarvis.core.file_diff import MAX_DIFF_CHARS, format_unified_diff


def test_identical_content_reports_no_changes():
    result = format_unified_diff("x.txt", "same\n", "same\n")
    assert "no changes" in result.lower()


def test_new_file_shows_line_count_preview():
    result = format_unified_diff("new.txt", "", "line one\nline two\n")
    assert "New file new.txt" in result
    assert "line one" in result
    assert "line two" in result
    assert "2 line" in result


def test_new_empty_file_does_not_crash():
    result = format_unified_diff("empty.txt", "", "")
    assert "no changes" in result.lower()


def test_new_file_preview_truncated_beyond_limit():
    content = "\n".join(f"line {i}" for i in range(50)) + "\n"
    result = format_unified_diff("big.txt", "", content)
    assert "line 0" in result
    assert "line 19" in result
    assert "line 49" not in result
    assert "more line" in result.lower()


def test_modified_existing_file_shows_unified_diff_markers():
    old = "hello\nworld\n"
    new = "hello\nthere\n"
    result = format_unified_diff("x.txt", old, new)
    assert "Diff for x.txt" in result
    assert "-world" in result
    assert "+there" in result


def test_diff_shows_added_lines():
    old = "a\nb\n"
    new = "a\nb\nc\n"
    result = format_unified_diff("x.txt", old, new)
    assert "+c" in result


def test_diff_shows_removed_lines():
    old = "a\nb\nc\n"
    new = "a\nc\n"
    result = format_unified_diff("x.txt", old, new)
    assert "-b" in result


def test_diff_includes_path_in_header():
    result = format_unified_diff("some/nested/path.py", "old\n", "new\n")
    assert "some/nested/path.py" in result


def test_diff_truncated_when_very_large():
    old = "line\n" * 5000
    new = "different\n" * 5000
    result = format_unified_diff("huge.txt", old, new)
    assert "truncated" in result.lower()
    assert len(result) < len(old) + len(new)


def test_diff_not_truncated_when_under_limit():
    old = "a\n"
    new = "b\n"
    result = format_unified_diff("x.txt", old, new)
    assert "truncated" not in result.lower()
    assert len(result) <= MAX_DIFF_CHARS + 200  # header/footer overhead


def test_trailing_newline_only_difference_does_not_crash():
    result = format_unified_diff("x.txt", "content", "content\n")
    # Either treated as a real diff or as effectively identical - must not
    # raise and must produce readable output either way.
    assert isinstance(result, str)
    assert result


def test_never_raises_on_arbitrary_text():
    # Unicode, empty lines, mixed content - the function must never throw.
    result = format_unified_diff("x.txt", "café\n\n日本語\n", "café\nnew\n日本語\n")
    assert isinstance(result, str)
