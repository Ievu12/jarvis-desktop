"""Tests for jarvis.gui.settings_store: local JSON storage for the
desktop GUI's update preferences. Redirected to a per-test tmp_path
file - no test touches the real .jarvis/update_settings.json."""

from __future__ import annotations

import json

import pytest

from jarvis.gui import settings_store


@pytest.fixture(autouse=True)
def _isolated_settings_file(tmp_path, monkeypatch):
    settings_file = tmp_path / "update_settings.json"
    monkeypatch.setattr(settings_store, "UPDATE_SETTINGS_FILE", settings_file)
    return settings_file


def test_load_missing_file_returns_documented_defaults():
    settings = settings_store.load_update_settings()
    assert settings.check_for_updates is True
    assert settings.download_updates is True
    assert settings.install_updates is False


def test_save_then_load_round_trips():
    original = settings_store.UpdateSettings(
        check_for_updates=False, download_updates=False, install_updates=True,
    )
    settings_store.save_update_settings(original)
    loaded = settings_store.load_update_settings()
    assert loaded == original


def test_corrupted_file_falls_back_to_defaults(_isolated_settings_file):
    _isolated_settings_file.parent.mkdir(parents=True, exist_ok=True)
    _isolated_settings_file.write_text("{not valid json", encoding="utf-8")
    settings = settings_store.load_update_settings()
    assert settings == settings_store.UpdateSettings()


def test_wrong_shape_falls_back_to_defaults(_isolated_settings_file):
    _isolated_settings_file.parent.mkdir(parents=True, exist_ok=True)
    _isolated_settings_file.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    settings = settings_store.load_update_settings()
    assert settings == settings_store.UpdateSettings()


def test_partial_file_fills_in_missing_fields_with_defaults(_isolated_settings_file):
    _isolated_settings_file.parent.mkdir(parents=True, exist_ok=True)
    _isolated_settings_file.write_text(json.dumps({"install_updates": True}), encoding="utf-8")
    settings = settings_store.load_update_settings()
    assert settings.install_updates is True
    assert settings.check_for_updates is True  # default, not present in file
