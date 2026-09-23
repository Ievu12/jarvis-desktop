"""Regression test for a real bug found during live testing: printing a
model response containing a Unicode character outside the console's
legacy codepage (e.g. cp1252) crashed the CLI with UnicodeEncodeError.
_ensure_utf8_stdio() reconfigures stdout/stderr to UTF-8 with
substitution so this can never happen again."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

from jarvis.cli.main import _ensure_utf8_stdio


def test_ensure_utf8_stdio_sets_utf8_encoding(monkeypatch):
    fake_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    fake_stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr("sys.stdout", fake_stdout)
    monkeypatch.setattr("sys.stderr", fake_stderr)

    _ensure_utf8_stdio()

    assert fake_stdout.encoding.lower() == "utf-8"
    assert fake_stderr.encoding.lower() == "utf-8"


def test_ensure_utf8_stdio_allows_printing_unicode_without_crashing(monkeypatch, capsys):
    fake_stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr("sys.stdout", fake_stdout)

    _ensure_utf8_stdio()

    # This character is outside cp1252 and would previously raise
    # UnicodeEncodeError - it must not raise now.
    print("Task complete ✅", file=fake_stdout)
    fake_stdout.flush()


def test_ensure_utf8_stdio_does_not_raise_when_stream_lacks_reconfigure():
    # A stream without reconfigure() (e.g. some test/mock replacements)
    # must be handled gracefully, not crash startup.
    fake_stream = MagicMock(spec=[])  # no reconfigure attribute at all
    import sys as sys_module

    original_stdout = sys_module.stdout
    original_stderr = sys_module.stderr
    try:
        sys_module.stdout = fake_stream
        sys_module.stderr = fake_stream
        _ensure_utf8_stdio()  # must not raise
    finally:
        sys_module.stdout = original_stdout
        sys_module.stderr = original_stderr
