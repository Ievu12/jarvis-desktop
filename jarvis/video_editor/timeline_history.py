"""Undo/redo history for a Timeline - pure, I/O-free value-object stack
logic, no GUI/ffmpeg knowledge at all (mirrors jarvis.video_editor
.timeline's own "plain, frozen, JSON-serializable value object" style,
so this module is trivially unit-testable on its own).

jarvis.video_editor.timeline.Timeline is already an immutable
(frozen-dataclass) value - every edit in
jarvis.gui.views.video_editor.timeline_panel.TimelinePanel already
produces a brand NEW Timeline via dataclasses.replace() rather than
mutating one in place. That means undo/redo needs no deep-copying or
diffing: each distinct Timeline value IS its own complete snapshot, so
a simple two-stack (past/future) history of Timeline references is
both correct and cheap."""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.video_editor.timeline import Timeline, TimelineItem

_MAX_HISTORY_ENTRIES = 50
# A generous but bounded cap - undo/redo is a per-session editing aid,
# not a project-history audit log; nothing here is ever persisted to
# disk (jarvis.video_editor.storage.save_project() only ever writes the
# CURRENT Timeline, same as before this module existed).


@dataclass
class TimelineHistory:
    """`current` is always the live Timeline the rest of the GUI is
    showing. `push(new_timeline)` records the PREVIOUS current onto the
    undo stack and clears the redo stack (the standard "any new edit
    invalidates redo" rule) - call it with the timeline that resulted
    from an edit, same value `on_timeline_changed` already receives,
    so wiring this in means adding one call at each existing mutation
    point, not restructuring them."""

    current: Timeline = field(default_factory=Timeline)
    _past: list[Timeline] = field(default_factory=list)
    _future: list[Timeline] = field(default_factory=list)

    def push(self, new_timeline: Timeline) -> None:
        if new_timeline == self.current:
            return  # a no-op "edit" (e.g. re-entering the same value) never creates a history entry
        self._past.append(self.current)
        if len(self._past) > _MAX_HISTORY_ENTRIES:
            self._past.pop(0)
        self._future.clear()
        self.current = new_timeline

    def can_undo(self) -> bool:
        return bool(self._past)

    def can_redo(self) -> bool:
        return bool(self._future)

    def undo(self) -> Timeline:
        """Returns the timeline to show after undoing - if there is
        nothing to undo, returns `current` unchanged (never raises;
        matches this package's established "describe problems, don't
        throw" convention via can_undo() as the thing a caller checks
        first to decide whether to even show an active Undo button)."""
        if not self._past:
            return self.current
        self._future.append(self.current)
        self.current = self._past.pop()
        return self.current

    def redo(self) -> Timeline:
        if not self._future:
            return self.current
        self._past.append(self.current)
        self.current = self._future.pop()
        return self.current

    def reset(self, timeline: Timeline) -> None:
        """Replaces `current` with `timeline` and clears BOTH stacks -
        for loading a different/newly-opened project, where the
        previous project's own undo history must never leak into the
        next one (same reasoning as TimelinePanel.render() already
        fully replacing its own `_timeline` field on project load)."""
        self.current = timeline
        self._past.clear()
        self._future.clear()


@dataclass
class TimelineClipboard:
    """Holds at most one copied TimelineClip/TimelineStill for
    copy/paste - deliberately NOT part of TimelineHistory (copying a
    clip is not itself an undoable "edit" to the timeline; only the
    later paste is, and that paste goes through the normal
    TimelineHistory.push() path like any other edit)."""

    _copied: TimelineItem | None = None

    def copy(self, item: TimelineItem) -> None:
        self._copied = item

    def has_item(self) -> bool:
        return self._copied is not None

    def paste(self, *, new_clip_id: str) -> TimelineItem | None:
        """Returns a COPY of the held item with a fresh `clip_id` (so
        pasting twice never produces two items sharing one id - every
        other id-assignment path in this package, e.g.
        TimelinePanel._new_clip_id(), already treats clip_id as unique
        per real timeline entry) and with `transition_out` reset to the
        default "cut" (a copied transition pointing at "the next clip"
        means something different once pasted somewhere else in the
        order, so carrying it over silently would misrepresent what the
        person just pasted). Returns None if nothing has been copied."""
        if self._copied is None:
            return None
        from dataclasses import replace

        from jarvis.video_editor.timeline import TransitionSpec

        return replace(self._copied, clip_id=new_clip_id, transition_out=TransitionSpec())
