"""One snapshot of everything the person edits in a project - the
timeline plus every layer on top of it - and the project-wide undo/redo
history over those snapshots.

Before this, only the clip list had undo/redo (TimelinePanel's own
TimelineHistory); moving a sticker, changing a caption or trimming the
music could not be undone. Every piece of EditorState is already an
immutable (frozen) value, so a snapshot is just a tuple of references -
no copying, no diffing - the same reasoning timeline_history.py gives
for Timeline alone."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from jarvis.video_editor.audio_mixing import MusicTrack
from jarvis.video_editor.captions import CaptionLine, CaptionStyle
from jarvis.video_editor.reels import ReelsLayers
from jarvis.video_editor.stickers import StickerInstance
from jarvis.video_editor.text_overlay import TextOverlay
from jarvis.video_editor.timeline import Timeline

MAX_HISTORY_ENTRIES = 100
COALESCE_SECONDS = 1.0
# Edits carrying the same coalesce key within this long of each other
# (a slider being dragged, a word being typed) become ONE undo step.


@dataclass(frozen=True)
class EditorState:
    timeline: Timeline = field(default_factory=Timeline)
    text_overlays: tuple[TextOverlay, ...] = ()
    stickers: tuple[StickerInstance, ...] = ()
    caption_style: CaptionStyle | None = None
    caption_lines: tuple[CaptionLine, ...] | None = None
    music_track: MusicTrack | None = None
    reels: ReelsLayers | None = None
    """Instagram Reels mode layers (animated subtitles, ...); None outside Reels mode."""


@dataclass
class _Entry:
    state: EditorState
    label: str
    coalesce_key: str | None
    recorded_at: float


class EditorHistory:
    """`current` is the state on screen. `record()` is called after
    every edit with the resulting state; `undo()`/`redo()` return the
    state to put back on screen (or None when there is nothing to do)."""

    def __init__(self, *, max_entries: int = MAX_HISTORY_ENTRIES, clock: Callable[[], float] = time.monotonic) -> None:
        self._max_entries = max_entries
        self._clock = clock
        self._current: _Entry | None = None
        self._past: list[_Entry] = []
        self._future: list[_Entry] = []

    @property
    def current(self) -> EditorState | None:
        return self._current.state if self._current is not None else None

    def reset(self, state: EditorState) -> None:
        """A project was opened: its history starts here."""
        self._current = _Entry(state, "", None, float("-inf"))
        self._past.clear()
        self._future.clear()

    def record(self, state: EditorState, *, label: str = "", coalesce_key: str | None = None) -> bool:
        """Returns True if a new undo step was created (False for a
        no-op or an edit merged into the previous step)."""
        now = self._clock()
        if self._current is None:
            self.reset(state)
            return False
        if state == self._current.state:
            return False
        merge = (
            coalesce_key is not None
            and coalesce_key == self._current.coalesce_key
            and now - self._current.recorded_at <= COALESCE_SECONDS
            and not self._future
            and self._past
        )
        if merge:
            self._current = _Entry(state, self._current.label, coalesce_key, now)
            return False
        self._past.append(self._current)
        if len(self._past) > self._max_entries:
            self._past.pop(0)
        self._future.clear()
        self._current = _Entry(state, label, coalesce_key, now)
        return True

    def can_undo(self) -> bool:
        return bool(self._past)

    def can_redo(self) -> bool:
        return bool(self._future)

    @property
    def undo_label(self) -> str:
        return self._current.label if self._past and self._current is not None else ""

    @property
    def redo_label(self) -> str:
        return self._future[-1].label if self._future else ""

    def undo(self) -> EditorState | None:
        if not self._past or self._current is None:
            return None
        self._future.append(self._current)
        previous = self._past.pop()
        # A restored step never merges with the next edit.
        self._current = _Entry(previous.state, previous.label, None, previous.recorded_at)
        return self._current.state

    def redo(self) -> EditorState | None:
        if not self._future or self._current is None:
            return None
        self._past.append(self._current)
        following = self._future.pop()
        self._current = _Entry(following.state, following.label, None, following.recorded_at)
        return self._current.state
