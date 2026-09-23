"""Direct tests for jarvis.session.store: missing/valid/corrupted session
files, non-list JSON, and that corruption is surfaced as a visible
warning rather than silently discarded. Uses an isolated SESSION_FILE via
monkeypatch - never the real project's session.json."""

from __future__ import annotations

import json

import pytest

from jarvis.session import store


@pytest.fixture
def isolated_session_file(tmp_path, monkeypatch):
    session_path = tmp_path / "session.json"
    monkeypatch.setattr(store, "SESSION_FILE", session_path)
    return session_path


def test_missing_file_returns_empty_history_no_warning(isolated_session_file):
    assert not isolated_session_file.exists()
    result = store.load_history()
    assert result.history == []
    assert result.warning is None


def test_valid_session_round_trips(isolated_session_file):
    original = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]
    store.save_history(original)

    result = store.load_history()
    assert result.history == original
    assert result.warning is None


def test_empty_history_round_trips(isolated_session_file):
    store.save_history([])
    result = store.load_history()
    assert result.history == []
    assert result.warning is None


def test_corrupted_json_returns_empty_history_with_warning(isolated_session_file):
    isolated_session_file.write_text("{not valid json at all", encoding="utf-8")

    result = store.load_history()
    assert result.history == []
    assert result.warning is not None
    assert "corrupted" in result.warning.lower()


def test_corrupted_json_does_not_delete_the_file(isolated_session_file):
    isolated_session_file.write_text("{not valid json", encoding="utf-8")
    store.load_history()
    # The corrupted file is left in place for inspection, not deleted.
    assert isolated_session_file.exists()
    assert isolated_session_file.read_text(encoding="utf-8") == "{not valid json"


def test_truncated_json_returns_empty_history_with_warning(isolated_session_file):
    valid = json.dumps([{"role": "user", "content": "hello world"}])
    isolated_session_file.write_text(valid[: len(valid) // 2], encoding="utf-8")

    result = store.load_history()
    assert result.history == []
    assert result.warning is not None


def test_non_list_json_object_returns_empty_history_with_warning(isolated_session_file):
    isolated_session_file.write_text(json.dumps({"not": "a list"}), encoding="utf-8")

    result = store.load_history()
    assert result.history == []
    assert result.warning is not None
    assert "unexpected format" in result.warning.lower()


def test_non_list_json_scalar_returns_empty_history_with_warning(isolated_session_file):
    isolated_session_file.write_text(json.dumps("just a string"), encoding="utf-8")

    result = store.load_history()
    assert result.history == []
    assert result.warning is not None


def test_empty_file_returns_empty_history_with_warning(isolated_session_file):
    isolated_session_file.write_text("", encoding="utf-8")

    result = store.load_history()
    assert result.history == []
    assert result.warning is not None  # empty string is invalid JSON, not valid empty history


def test_save_then_load_preserves_nested_structure(isolated_session_file):
    original = [
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "t1", "name": "read_file", "input": {"path": "x.txt"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "data", "is_error": False}],
        },
    ]
    store.save_history(original)
    result = store.load_history()
    assert result.history == original


def test_save_creates_parent_directory_if_missing(tmp_path, monkeypatch):
    nested_path = tmp_path / "does" / "not" / "exist" / "session.json"
    monkeypatch.setattr(store, "SESSION_FILE", nested_path)

    store.save_history([{"role": "user", "content": "hi"}])
    assert nested_path.exists()
