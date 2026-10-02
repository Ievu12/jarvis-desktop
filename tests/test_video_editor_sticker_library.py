"""Tests for jarvis.video_editor.sticker_library: real, file-backed
favorites and saved collections persistence - every test redirects
VIDEO_EDITOR_STICKER_LIBRARY_FILE to a per-test tmp_path, writes real
JSON to disk, and reloads it to confirm genuine round-tripping."""

from __future__ import annotations

import pytest

from jarvis.video_editor import sticker_library as sl
from jarvis.video_editor.stickers import StickerInstance


@pytest.fixture(autouse=True)
def _isolated_library_file(tmp_path, monkeypatch):
    path = tmp_path / "sticker_library.json"
    monkeypatch.setattr(sl, "VIDEO_EDITOR_STICKER_LIBRARY_FILE", path)


def test_load_library_with_no_file_returns_empty_state():
    state = sl.load_library()
    assert state.favorite_shapes == ()
    assert state.collections == ()


def test_load_library_with_corrupt_json_returns_empty_state(tmp_path):
    sl.VIDEO_EDITOR_STICKER_LIBRARY_FILE.parent.mkdir(parents=True, exist_ok=True)
    sl.VIDEO_EDITOR_STICKER_LIBRARY_FILE.write_text("{not valid json", encoding="utf-8")
    state = sl.load_library()
    assert state == sl.StickerLibraryState()


def test_toggle_favorite_adds_then_removes():
    state = sl.toggle_favorite("heart")
    assert state.favorite_shapes == ("heart",)

    state = sl.toggle_favorite("heart")
    assert state.favorite_shapes == ()


def test_toggle_favorite_persists_to_disk():
    sl.toggle_favorite("star")
    reloaded = sl.load_library()
    assert reloaded.favorite_shapes == ("star",)


def test_toggle_favorite_with_multiple_shapes_sorted():
    sl.toggle_favorite("star")
    sl.toggle_favorite("heart")
    state = sl.load_library()
    assert state.favorite_shapes == ("heart", "star")


def test_save_collection_persists_real_presets():
    sticker = StickerInstance(
        start_seconds=0.0, end_seconds=1.0, shape="flower", x_fraction=0.3,
        y_fraction=0.4, size_fraction=0.2, rotation_degrees=15.0, opacity=0.8, animation="spin",
    )
    preset = sl.StickerPreset.from_sticker_instance(sticker)
    sl.save_collection("Spring", [preset])

    reloaded = sl.load_library()
    assert len(reloaded.collections) == 1
    assert reloaded.collections[0].name == "Spring"
    assert reloaded.collections[0].presets[0].shape == "flower"
    assert reloaded.collections[0].presets[0].rotation_degrees == 15.0


def test_save_collection_with_same_name_replaces_it():
    preset_a = sl.StickerPreset.from_sticker_instance(
        StickerInstance(start_seconds=0.0, end_seconds=1.0, shape="heart"),
    )
    preset_b = sl.StickerPreset.from_sticker_instance(
        StickerInstance(start_seconds=0.0, end_seconds=1.0, shape="star"),
    )
    sl.save_collection("MySet", [preset_a])
    sl.save_collection("MySet", [preset_b])

    state = sl.load_library()
    assert len(state.collections) == 1
    assert state.collections[0].presets[0].shape == "star"


def test_delete_collection_removes_it():
    preset = sl.StickerPreset.from_sticker_instance(
        StickerInstance(start_seconds=0.0, end_seconds=1.0, shape="heart"),
    )
    sl.save_collection("ToDelete", [preset])
    sl.delete_collection("ToDelete")

    state = sl.load_library()
    assert state.collections == ()


def test_preset_round_trip_applies_fresh_timing():
    original = StickerInstance(
        start_seconds=1.0, end_seconds=2.0, shape="heart", x_fraction=0.25,
        y_fraction=0.75, size_fraction=0.3, rotation_degrees=45.0, opacity=0.5, animation="bounce",
    )
    preset = sl.StickerPreset.from_sticker_instance(original)
    restored = preset.to_sticker_instance(start_seconds=10.0, end_seconds=15.0)

    assert restored.start_seconds == 10.0
    assert restored.end_seconds == 15.0
    assert restored.shape == "heart"
    assert restored.x_fraction == 0.25
    assert restored.rotation_degrees == 45.0
    assert restored.animation == "bounce"


def test_preset_round_trip_preserves_custom_path():
    import pathlib

    original = StickerInstance(
        start_seconds=0.0, end_seconds=1.0, custom_path=pathlib.Path("C:/some/sticker.png"),
    )
    preset = sl.StickerPreset.from_sticker_instance(original)
    restored = preset.to_sticker_instance(start_seconds=0.0, end_seconds=1.0)
    assert restored.custom_path == pathlib.Path("C:/some/sticker.png")
    assert restored.shape is None
