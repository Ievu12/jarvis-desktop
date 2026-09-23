"""Tests for jarvis.integrations.instagram_history: local, JSON-backed
storage for daily Instagram Insights snapshots. This module has no
knowledge of the Graph API or OAuth tokens - only plain JSON I/O against
jarvis.config.INSTAGRAM_INSIGHTS_HISTORY_FILE, mirroring
jarvis.session.store's pattern. Confirms: a missing file is a normal
empty history, corrupted/wrongly-shaped files are reported via .warning
without crashing, upsert overwrites rather than duplicates a date, and a
metric absent from one day's entry stays absent (never defaulted)."""

from __future__ import annotations

import json

import pytest

from jarvis.integrations import instagram_history


@pytest.fixture(autouse=True)
def _isolated_history_file(tmp_path, monkeypatch):
    history_file = tmp_path / "instagram_insights_history.json"
    monkeypatch.setattr(instagram_history, "INSTAGRAM_INSIGHTS_HISTORY_FILE", history_file)
    return history_file


# --- load_history(): missing/empty file -------------------------------------------


def test_load_history_missing_file_returns_empty_no_warning():
    result = instagram_history.load_history()
    assert result.entries == {}
    assert result.warning is None


# --- upsert_snapshot() + load_history() round trip ---------------------------------


def test_upsert_then_load_round_trips():
    instagram_history.upsert_snapshot("2026-09-20", {"reach": 100, "likes": 5})
    result = instagram_history.load_history()
    assert result.entries == {"2026-09-20": {"reach": 100, "likes": 5}}


def test_upsert_same_date_twice_overwrites_not_duplicates():
    instagram_history.upsert_snapshot("2026-09-20", {"reach": 100})
    instagram_history.upsert_snapshot("2026-09-20", {"reach": 150, "likes": 3})
    result = instagram_history.load_history()
    assert result.entries == {"2026-09-20": {"reach": 150, "likes": 3}}


def test_upsert_preserves_other_dates():
    instagram_history.upsert_snapshot("2026-09-19", {"reach": 50})
    instagram_history.upsert_snapshot("2026-09-20", {"reach": 60})
    result = instagram_history.load_history()
    assert result.entries == {
        "2026-09-19": {"reach": 50},
        "2026-09-20": {"reach": 60},
    }


def test_upsert_only_stores_given_metrics_never_fills_missing_ones():
    instagram_history.upsert_snapshot("2026-09-20", {"reach": 100})
    result = instagram_history.load_history()
    assert "profile_views" not in result.entries["2026-09-20"]
    assert "likes" not in result.entries["2026-09-20"]


def test_get_snapshot_returns_none_for_unrecorded_date():
    instagram_history.upsert_snapshot("2026-09-20", {"reach": 100})
    assert instagram_history.get_snapshot("2026-09-19") is None


def test_get_snapshot_returns_recorded_date():
    instagram_history.upsert_snapshot("2026-09-20", {"reach": 100})
    assert instagram_history.get_snapshot("2026-09-20") == {"reach": 100}


# --- corrupted / wrongly-shaped file: reported via warning, never crashes ----------


def test_load_history_corrupted_json_returns_empty_with_warning(_isolated_history_file):
    _isolated_history_file.parent.mkdir(parents=True, exist_ok=True)
    _isolated_history_file.write_text("{not valid json", encoding="utf-8")
    result = instagram_history.load_history()
    assert result.entries == {}
    assert result.warning is not None
    assert "corrupted" in result.warning.lower()


def test_load_history_wrong_top_level_type_returns_empty_with_warning(_isolated_history_file):
    _isolated_history_file.parent.mkdir(parents=True, exist_ok=True)
    _isolated_history_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = instagram_history.load_history()
    assert result.entries == {}
    assert result.warning is not None


def test_load_history_skips_non_dict_entries_without_crashing(_isolated_history_file):
    _isolated_history_file.parent.mkdir(parents=True, exist_ok=True)
    _isolated_history_file.write_text(
        json.dumps({"2026-09-20": {"reach": 100}, "2026-09-19": "not a dict"}),
        encoding="utf-8",
    )
    result = instagram_history.load_history()
    assert result.entries == {"2026-09-20": {"reach": 100}}


# --- never touches Gmail/Calendar files or session/task storage -------------------


def test_module_only_imports_its_own_config_constant():
    import jarvis.integrations.instagram_history as mod

    assert "INSTAGRAM_INSIGHTS_HISTORY_FILE" in dir(mod)
    assert "SESSION_FILE" not in dir(mod)
    assert "TASKS_FILE" not in dir(mod)
