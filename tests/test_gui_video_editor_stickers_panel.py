"""Tests for jarvis.gui.views.video_editor.stickers_panel.StickersPanel:
the library browser (search/categories/favorites/collections) is tested
through its own real methods (no mocking) against a real, per-test
sticker_library.json file; the placed-stickers list (add/remove/
duplicate) is the pre-existing behavior this panel already had before
the library browser was added."""

from __future__ import annotations

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_editor.stickers_panel import StickersPanel
from jarvis.video_editor import sticker_library as sl


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_children(root):
    yield
    for child in list(root.winfo_children()):
        child.destroy()


@pytest.fixture(autouse=True)
def _isolated_library_file(tmp_path, monkeypatch):
    path = tmp_path / "sticker_library.json"
    monkeypatch.setattr(sl, "VIDEO_EDITOR_STICKER_LIBRARY_FILE", path)


def test_constructs_with_a_populated_library_grid(root):
    panel = StickersPanel(root, on_stickers_changed=lambda s: None)
    assert len(panel._library_grid.winfo_children()) > 0


def test_clicking_a_library_shape_adds_it_to_placed_stickers(root):
    stickers_seen = []
    panel = StickersPanel(root, on_stickers_changed=stickers_seen.append)
    panel._on_library_shape_clicked("flower")
    assert len(panel._stickers) == 1
    assert panel._stickers[0].shape == "flower"
    assert stickers_seen[-1][0].shape == "flower"


def test_toggling_favorite_persists_and_updates_grid(root):
    panel = StickersPanel(root, on_stickers_changed=lambda s: None)
    assert "heart" not in panel._library_state.favorite_shapes

    panel._on_toggle_favorite_clicked("heart")
    assert "heart" in panel._library_state.favorite_shapes

    reloaded = sl.load_library()
    assert "heart" in reloaded.favorite_shapes


def test_search_filters_shapes_by_name(root):
    panel = StickersPanel(root, on_stickers_changed=lambda s: None)
    panel._search_entry.insert(0, "heart")
    results = panel._current_library_shapes()
    assert "heart" in results
    assert "flower" not in results


def test_favorites_category_shows_only_favorited_shapes(root):
    panel = StickersPanel(root, on_stickers_changed=lambda s: None)
    panel._on_toggle_favorite_clicked("star")
    panel._category_dropdown.set("⭐ Favorites")
    results = panel._current_library_shapes()
    assert results == ["star"]


def test_save_and_apply_collection_round_trips_real_sticker_configuration(root):
    panel = StickersPanel(root, on_stickers_changed=lambda s: None)
    panel._on_library_shape_clicked("flower")
    assert len(panel._stickers) == 1

    presets = [sl.StickerPreset.from_sticker_instance(s) for s in panel._stickers]
    panel._library_state = sl.save_collection("My Set", presets)
    panel._render_collections()
    assert len(panel._library_state.collections) == 1

    panel._on_apply_collection_clicked(panel._library_state.collections[0])
    assert len(panel._stickers) == 2
    assert panel._stickers[1].shape == "flower"


def test_delete_collection_removes_it(root):
    panel = StickersPanel(root, on_stickers_changed=lambda s: None)
    panel._on_library_shape_clicked("star")
    presets = [sl.StickerPreset.from_sticker_instance(s) for s in panel._stickers]
    panel._library_state = sl.save_collection("ToRemove", presets)

    panel._on_delete_collection_clicked("ToRemove")
    assert panel._library_state.collections == ()


def test_existing_add_builtin_and_remove_flow_still_works(root):
    # The pre-existing (pre-library-browser) placed-sticker add/remove
    # flow must keep working unchanged.
    stickers_seen = []
    panel = StickersPanel(root, on_stickers_changed=stickers_seen.append)
    panel._on_add_builtin_clicked()
    assert len(panel._stickers) == 1
    panel._remove(0)
    assert panel._stickers == []
