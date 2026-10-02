"""Sticker favorites and saved collections - GLOBAL, person-level state
(requirement: "Paiešką ir mėgstamiausių lipdukų sąrašą" / "galimybę
išsaugoti savo lipdukų rinkinius" - search and a favorites list /
ability to save your own sticker sets), separate from any one project
(jarvis.video_editor.db) since a favorited sticker is useful across
every project, not scoped to one.

A SavedCollection holds StickerInstance CONFIGURATIONS (shape/size/
position/rotation/opacity/animation - everything except timing, which
is meaningless outside a specific timeline), so applying a saved
collection means create StickerInstance objects with the saved config
but FRESH start/end times the person sets for the new placement - the
collection is a style/look preset, never a literal copy-paste of
someone else's old timing."""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.config import VIDEO_EDITOR_STICKER_LIBRARY_FILE
from jarvis.video_editor.stickers import StickerInstance


class StickerLibraryError(Exception):
    """Raised for a genuine read/write failure (corrupt JSON, disk
    error) - a LOCAL exception, never subclassing a sibling module's own
    error type, matching this package's established per-module
    isolation convention."""


@dataclass(frozen=True)
class StickerPreset:
    """One saved sticker CONFIGURATION (no start/end timing - see this
    module's own docstring for why) - a plain, JSON-serializable value."""

    shape: str | None
    custom_path: str | None
    x_fraction: float
    y_fraction: float
    size_fraction: float
    rotation_degrees: float
    opacity: float
    animation: str
    tint: tuple[int, int, int] | None = None

    @classmethod
    def from_sticker_instance(cls, sticker: StickerInstance) -> "StickerPreset":
        return cls(
            shape=sticker.shape, custom_path=str(sticker.custom_path) if sticker.custom_path else None,
            x_fraction=sticker.x_fraction, y_fraction=sticker.y_fraction, size_fraction=sticker.size_fraction,
            rotation_degrees=sticker.rotation_degrees, opacity=sticker.opacity, animation=sticker.animation,
            tint=sticker.tint,
        )

    def to_sticker_instance(self, *, start_seconds: float, end_seconds: float) -> StickerInstance:
        """Builds a real StickerInstance from this preset, with FRESH
        timing the caller provides - a preset never carries its own old
        timing forward into a new placement."""
        return StickerInstance(
            start_seconds=start_seconds, end_seconds=end_seconds,
            shape=self.shape, custom_path=Path(self.custom_path) if self.custom_path else None,
            x_fraction=self.x_fraction, y_fraction=self.y_fraction, size_fraction=self.size_fraction,
            rotation_degrees=self.rotation_degrees, opacity=self.opacity, animation=self.animation,
            tint=self.tint,
        )


@dataclass(frozen=True)
class SavedCollection:
    """One named set of sticker presets the person saved - requirement:
    "galimybę išsaugoti savo lipdukų rinkinius" (ability to save your own
    sticker sets)."""

    name: str
    presets: tuple[StickerPreset, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class StickerLibraryState:
    """The complete persisted state - favorite shape names (built-in
    shapes only; a favorited custom-upload sticker doesn't make sense
    since its own file may not exist in a later session) plus every
    saved collection."""

    favorite_shapes: tuple[str, ...] = field(default_factory=tuple)
    collections: tuple[SavedCollection, ...] = field(default_factory=tuple)


def load_library() -> StickerLibraryState:
    """Returns the real, persisted favorites/collections, or a real
    empty StickerLibraryState if the file doesn't exist yet (the normal
    first-use case) or is corrupt (never raises for a missing/corrupt
    file - a person's sticker library is convenience state, not
    critical data, so this follows this codebase's own "describe
    problems, don't throw" spirit for non-critical state rather than
    blocking the whole Video Editor view on a bad JSON file)."""
    path = VIDEO_EDITOR_STICKER_LIBRARY_FILE
    if not path.is_file():
        return StickerLibraryState()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return StickerLibraryState()

    try:
        collections = tuple(
            SavedCollection(
                name=c["name"],
                presets=tuple(StickerPreset(**p) for p in c.get("presets", [])),
            )
            for c in raw.get("collections", [])
        )
        return StickerLibraryState(
            favorite_shapes=tuple(raw.get("favorite_shapes", [])), collections=collections,
        )
    except (KeyError, TypeError):
        return StickerLibraryState()


def save_library(state: StickerLibraryState) -> None:
    """Writes `state` to disk, overwriting whatever was there - raises
    StickerLibraryError only for a genuine disk-write failure (never for
    a malformed `state`, since it's always built from this module's own
    real dataclasses, not arbitrary external input)."""
    path = VIDEO_EDITOR_STICKER_LIBRARY_FILE
    payload = {
        "favorite_shapes": list(state.favorite_shapes),
        "collections": [
            {"name": c.name, "presets": [dataclasses.asdict(p) for p in c.presets]}
            for c in state.collections
        ],
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        raise StickerLibraryError(f"Couldn't save the sticker library: {e}") from e


def toggle_favorite(shape: str) -> StickerLibraryState:
    """Loads the current library, flips `shape`'s own favorite status,
    saves, and returns the new state - a real read-modify-write, not an
    in-memory-only toggle, so a favorite genuinely persists across GUI
    sessions."""
    state = load_library()
    favorites = set(state.favorite_shapes)
    if shape in favorites:
        favorites.discard(shape)
    else:
        favorites.add(shape)
    new_state = dataclasses.replace(state, favorite_shapes=tuple(sorted(favorites)))
    save_library(new_state)
    return new_state


def save_collection(name: str, presets: list[StickerPreset]) -> StickerLibraryState:
    """Adds (or replaces, if `name` already exists) a named collection -
    a real read-modify-write against the persisted library."""
    state = load_library()
    other_collections = tuple(c for c in state.collections if c.name != name)
    new_collection = SavedCollection(name=name, presets=tuple(presets))
    new_state = dataclasses.replace(state, collections=other_collections + (new_collection,))
    save_library(new_state)
    return new_state


def delete_collection(name: str) -> StickerLibraryState:
    state = load_library()
    new_state = dataclasses.replace(
        state, collections=tuple(c for c in state.collections if c.name != name),
    )
    save_library(new_state)
    return new_state
