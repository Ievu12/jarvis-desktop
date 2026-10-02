"""The multi-track timeline at the bottom of the Video Editor: one lane
per track (video, effects, audio, captions, text, stickers), a time
ruler and a playhead. Bars are selected by clicking, moved by dragging
their body and trimmed by dragging either edge; edges snap to the
playhead, to zero/the end and to other bars' edges.

Replaces the earlier read-only "Tracks overview" (MultiTrackView). All
edit math lives in jarvis.video_editor.track_layout; this widget only
turns pointer gestures into those calls and reports the resulting
EditorState through `on_state_edited`."""

from __future__ import annotations

import tkinter as tk
import uuid
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card
from jarvis.video_editor import track_layout as tl
from jarvis.video_editor.editor_state import EditorState
from jarvis.video_editor.media_import import MediaItem

TrackRef = tuple[str, int]

RULER_HEIGHT = 22
LANE_HEIGHT = 26
LANE_GAP = 3
LABEL_WIDTH = 104
EDGE_GRAB_PX = 7
SNAP_PX = 8
MIN_PX_PER_SECOND, MAX_PX_PER_SECOND = 4.0, 400.0
_TRACK_COLORS = {
    "video": theme.ACCENT_PRIMARY,
    "effects": "#c27c0e",
    "audio": "#2d8a5f",
    "captions": "#b38600",
    "text": "#8a4fb3",
    "stickers": "#d1538a",
}
_PLAYHEAD_COLOR = "#ff4d4d"
_LIVE_TRACKS = ("text", "stickers", "captions")
# Dragging these updates the preview on every mouse move (cheap: just
# a redraw). Video and audio edits restart decoding, so those show a
# ghost bar while dragging and apply once on release.


def _lane_top(row: int) -> int:
    return RULER_HEIGHT + row * (LANE_HEIGHT + LANE_GAP) + LANE_GAP


def _ruler_step(px_per_second: float) -> float:
    for step in (0.1, 0.25, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300):
        if step * px_per_second >= 64:
            return step
    return 600


def format_ruler_time(seconds: float) -> str:
    minutes, secs = divmod(max(0.0, seconds), 60)
    if secs == int(secs):
        return f"{int(minutes)}:{int(secs):02d}"
    return f"{int(minutes)}:{secs:04.1f}"


class TrackTimelinePanel(ctk.CTkFrame):
    def __init__(
        self, master, *,
        on_seek: Callable[[float], None],
        on_selection_changed: Callable[[TrackRef | None], None],
        on_state_edited: Callable[[EditorState, bool, str, str | None], None],
        **kwargs,
    ) -> None:
        """`on_state_edited(state, final, label, coalesce_key)` - `final`
        is False for the live updates while a text/sticker/caption bar
        is being dragged and True once per finished edit."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_seek = on_seek
        self._on_selection_changed = on_selection_changed
        self._on_state_edited = on_state_edited

        self._state = EditorState()
        self._media_items: dict[str, MediaItem] = {}
        self._bars: dict[str, list[tl.TrackBar]] = {track: [] for track in tl.TRACKS}
        self._total = 0.0
        self._transitions: list[tuple[int, float, float, str]] = []
        self._playhead = 0.0
        self._px_per_second = 40.0
        self._fit = True
        self._selected: TrackRef | None = None
        self._drag: dict | None = None
        self._playhead_items: tuple[int, int] | None = None

        card = Card(self)
        card.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(0, theme.SPACE_XS))
        ctk.CTkLabel(
            header, text="⏱ LAIKO JUOSTA",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(side="left", padx=(0, theme.SPACE_MD))
        button_style = dict(
            height=24, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        )
        self._split_button = ctk.CTkButton(
            header, text="✂ Padalinti", width=92, command=self.split_at_playhead, **button_style,
        )
        self._split_button.pack(side="left", padx=(0, theme.SPACE_XS))
        self._duplicate_button = ctk.CTkButton(
            header, text="⧉ Kopijuoti", width=92, command=self.duplicate_selected, **button_style,
        )
        self._duplicate_button.pack(side="left", padx=(0, theme.SPACE_XS))
        self._delete_button = ctk.CTkButton(
            header, text="🗑 Ištrinti", width=84, command=self.delete_selected, **button_style,
        )
        self._delete_button.pack(side="left")

        ctk.CTkButton(header, text="↔", width=32, command=self.zoom_to_fit, **button_style).pack(side="right")
        ctk.CTkButton(header, text="＋", width=32, command=lambda: self.zoom(1.5), **button_style).pack(
            side="right", padx=(theme.SPACE_XS, theme.SPACE_XS),
        )
        ctk.CTkButton(header, text="－", width=32, command=lambda: self.zoom(1 / 1.5), **button_style).pack(side="right")
        ctk.CTkLabel(
            header, text="Mastelis:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED,
        ).pack(side="right", padx=(0, theme.SPACE_XS))

        canvas_height = _lane_top(len(tl.TRACKS)) + 2
        body = ctk.CTkFrame(inner, fg_color="transparent")
        body.pack(fill="both", expand=True)
        self._label_canvas = tk.Canvas(
            body, width=LABEL_WIDTH, height=canvas_height, bg=theme.BG_CARD, highlightthickness=0,
        )
        self._label_canvas.pack(side="left", fill="y")
        right = ctk.CTkFrame(body, fg_color="transparent")
        right.pack(side="left", fill="both", expand=True)
        self._canvas = tk.Canvas(
            right, height=canvas_height, bg=theme.BG_CARD, highlightthickness=0, cursor="arrow",
            xscrollincrement=1,
        )
        self._canvas.pack(fill="x", expand=True)
        self._scrollbar = ctk.CTkScrollbar(right, orientation="horizontal", command=self._canvas.xview, height=12)
        self._scrollbar.pack(fill="x")
        self._canvas.configure(xscrollcommand=self._scrollbar.set)

        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Motion>", self._on_hover)
        self._canvas.bind("<Configure>", lambda _e: self._on_resized())
        self._canvas.bind("<Delete>", lambda _e: self.delete_selected())
        self._canvas.bind("<BackSpace>", lambda _e: self.delete_selected())
        self._canvas.bind("<Control-d>", lambda _e: self.duplicate_selected())
        self._canvas.bind("<s>", lambda _e: self.split_at_playhead())
        for sequence in ("<Control-MouseWheel>", "<Control-Button-4>", "<Control-Button-5>"):
            self._canvas.bind(sequence, self._on_zoom_wheel)
        for sequence in ("<Shift-MouseWheel>", "<MouseWheel>"):
            self._canvas.bind(sequence, self._on_scroll_wheel)

        self._draw_labels()
        self._update_buttons()
        self._redraw()

    # --- dashboard-facing API ----------------------------------------------------------------

    def render(self, state: EditorState, media_items: dict[str, MediaItem]) -> None:
        self._state = state
        self._media_items = media_items
        self._bars = tl.build_track_bars(state, media_items)
        self._total = tl.total_duration(state, media_items)
        self._transitions = tl.transition_markers(state, media_items)
        if self._selected is not None and self._bar(self._selected) is None:
            self._selected = None
        if self._fit:
            self._fit_zoom()
        self._update_buttons()
        self._redraw()

    def set_playhead(self, t: float, *, follow: bool = False) -> None:
        self._playhead = max(0.0, t)
        self._place_playhead()
        if follow:
            self._scroll_to_show(self._playhead)

    def select(self, ref: TrackRef | None) -> None:
        """Selects from outside (the preview) without notifying back."""
        if ref is not None and self._bar(ref) is None:
            ref = None
        if ref != self._selected:
            self._selected = ref
            self._update_buttons()
            self._redraw()

    @property
    def selected(self) -> TrackRef | None:
        return self._selected

    @property
    def px_per_second(self) -> float:
        return self._px_per_second

    def x_for(self, seconds: float) -> float:
        """Canvas x (scroll-independent) of timeline time `seconds`."""
        return seconds * self._px_per_second

    def lane_center_y(self, track: str) -> float:
        return _lane_top(tl.TRACKS.index(track)) + LANE_HEIGHT / 2

    def zoom(self, factor: float) -> None:
        self._fit = False
        self._px_per_second = max(MIN_PX_PER_SECOND, min(MAX_PX_PER_SECOND, self._px_per_second * factor))
        self._redraw()
        self._scroll_to_show(self._playhead)

    def zoom_to_fit(self) -> None:
        self._fit = True
        self._fit_zoom()
        self._redraw()

    # --- toolbar actions -----------------------------------------------------------------------

    def split_at_playhead(self) -> None:
        new_state = tl.split_at(self._state, self._media_items, self._playhead, new_clip_id=_new_clip_id())
        if new_state is not None:
            self._on_state_edited(new_state, True, "Padalintas klipas", None)

    def duplicate_selected(self) -> None:
        if self._selected is None:
            return
        track, index = self._selected
        result = tl.duplicate_element(self._state, track, index, new_clip_id=_new_clip_id(), total=self._total)
        if result is None:
            return
        new_state, new_index = result
        self._on_state_edited(new_state, True, "Nukopijuota", None)
        self._set_selected((track, new_index), notify=True)

    def delete_selected(self) -> None:
        if self._selected is None:
            return
        track, index = self._selected
        bar = self._bar(self._selected)
        if bar is None or (track == "captions" and not self._state.caption_lines):
            return
        new_state = tl.delete_element(self._state, track, index)
        self._set_selected(None, notify=True)
        self._on_state_edited(new_state, True, "Ištrinta", None)

    # --- drawing -------------------------------------------------------------------------------

    def _draw_labels(self) -> None:
        canvas = self._label_canvas
        canvas.delete("all")
        for row, track in enumerate(tl.TRACKS):
            top = _lane_top(row)
            canvas.create_rectangle(0, top, LABEL_WIDTH, top + LANE_HEIGHT, fill=theme.BG_CARD_HOVER, outline="")
            canvas.create_rectangle(0, top, 4, top + LANE_HEIGHT, fill=_TRACK_COLORS[track], outline="")
            canvas.create_text(
                10, top + LANE_HEIGHT / 2, text=tl.TRACK_LABELS[track], anchor="w",
                fill=theme.TEXT_SECONDARY, font=(theme.FONT_FAMILY_BODY, 10),
            )

    def _content_width(self) -> float:
        return max(self._visible_width(), self.x_for(self._total) + 60)

    def _visible_width(self) -> int:
        width = self._canvas.winfo_width()
        return width if width > 1 else 600

    def _fit_zoom(self) -> None:
        if self._total <= 0:
            return
        usable = max(100, self._visible_width() - 30)
        self._px_per_second = max(MIN_PX_PER_SECOND, min(MAX_PX_PER_SECOND, usable / self._total))

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        width = self._content_width()
        canvas.configure(scrollregion=(0, 0, width, _lane_top(len(tl.TRACKS)) + 2))

        # ruler
        canvas.create_rectangle(0, 0, width, RULER_HEIGHT, fill=theme.BG_CARD, outline="")
        step = _ruler_step(self._px_per_second)
        n = 0
        while n * step * self._px_per_second <= width:
            seconds = n * step
            x = self.x_for(seconds)
            canvas.create_line(x, RULER_HEIGHT - 7, x, RULER_HEIGHT, fill=theme.TEXT_MUTED)
            canvas.create_text(
                x + 3, 3, text=format_ruler_time(seconds), anchor="nw", fill=theme.TEXT_MUTED,
                font=(theme.FONT_FAMILY_BODY, 8),
            )
            n += 1

        for row, track in enumerate(tl.TRACKS):
            top = _lane_top(row)
            canvas.create_rectangle(0, top, width, top + LANE_HEIGHT, fill=theme.BG_CARD_HOVER, outline="")
            if self._total > 0:
                end_x = self.x_for(self._total)
                canvas.create_line(end_x, top, end_x, top + LANE_HEIGHT, fill=theme.BORDER_SUBTLE)
            for bar in self._bars[track]:
                self._draw_bar(bar, top)

        # Transitions: where two video bars overlap, with a ⇄ mark.
        video_top = _lane_top(tl.TRACKS.index("video"))
        for _index, start, end, kind in self._transitions:
            x1, x2 = self.x_for(start), max(self.x_for(end), self.x_for(start) + 8)
            canvas.create_rectangle(
                x1, video_top + 3, x2, video_top + LANE_HEIGHT - 3, fill="#ffffff", outline="#ffffff",
                stipple="gray25", tags=("transition",),
            )
            canvas.create_text(
                (x1 + x2) / 2, video_top + LANE_HEIGHT / 2, text="⇄", fill="#ffffff",
                font=(theme.FONT_FAMILY_BODY, 10, "bold"), tags=("transition",),
            )

        if self._drag is not None and self._drag.get("ghost") is not None:
            ghost_track, start, end = self._drag["ghost"]
            top = _lane_top(tl.TRACKS.index(ghost_track))
            canvas.create_rectangle(
                self.x_for(start), top - 1, max(self.x_for(end), self.x_for(start) + 3), top + LANE_HEIGHT + 1,
                outline="white", width=2, dash=(4, 2),
            )
        self._playhead_items = None
        self._place_playhead()

    def _draw_bar(self, bar: tl.TrackBar, top: int) -> None:
        canvas = self._canvas
        x1, x2 = self.x_for(bar.start), max(self.x_for(bar.end), self.x_for(bar.start) + 3)
        selected = self._selected == (bar.track, bar.index)
        fixed = not (bar.can_move or bar.can_trim_start or bar.can_trim_end)
        canvas.create_rectangle(
            x1 + 1, top + 1, x2 - 1, top + LANE_HEIGHT - 1, fill=_TRACK_COLORS[bar.track],
            outline="white" if selected else theme.BG_CARD, width=2 if selected else 1,
            stipple="gray50" if fixed else "",
        )
        if bar.can_trim_start or bar.can_trim_end:
            for edge_x, editable in ((x1 + 3, bar.can_trim_start), (x2 - 3, bar.can_trim_end)):
                if editable and x2 - x1 > 14:
                    canvas.create_line(edge_x, top + 7, edge_x, top + LANE_HEIGHT - 7, fill="#ffffff", width=1)
        max_chars = int((x2 - x1 - 12) / 6.5)
        if max_chars >= 3:
            label = bar.label if len(bar.label) <= max_chars else bar.label[: max_chars - 1] + "…"
            canvas.create_text(
                x1 + 7, top + LANE_HEIGHT / 2, text=label, anchor="w", fill="white",
                font=(theme.FONT_FAMILY_BODY, 9),
            )

    def _place_playhead(self) -> None:
        x = self.x_for(self._playhead)
        bottom = _lane_top(len(tl.TRACKS))
        if self._playhead_items is None:
            line = self._canvas.create_line(x, 0, x, bottom, fill=_PLAYHEAD_COLOR, width=2)
            head = self._canvas.create_polygon(x - 6, 0, x + 6, 0, x, 9, fill=_PLAYHEAD_COLOR, outline="")
            self._playhead_items = (line, head)
        else:
            line, head = self._playhead_items
            self._canvas.coords(line, x, 0, x, bottom)
            self._canvas.coords(head, x - 6, 0, x + 6, 0, x, 9)

    def _scroll_to_show(self, seconds: float) -> None:
        width = self._content_width()
        if width <= 0:
            return
        x = self.x_for(seconds)
        left = self._canvas.canvasx(0)
        visible = self._visible_width()
        if x < left or x > left + visible - 20:
            self._canvas.xview_moveto(max(0.0, (x - visible * 0.25) / width))

    def _update_buttons(self) -> None:
        editable_selection = self._selected is not None and self._selected[0] != "effects"
        state = "normal" if self._selected is not None else "disabled"
        self._delete_button.configure(state=state)
        self._duplicate_button.configure(
            state="normal" if editable_selection and self._selected[0] != "audio" else "disabled",
        )
        self._split_button.configure(state="normal" if self._state.timeline.items else "disabled")

    def _on_resized(self) -> None:
        if self._fit:
            self._fit_zoom()
        self._redraw()

    # --- pointer -------------------------------------------------------------------------------

    def _bar(self, ref: TrackRef) -> tl.TrackBar | None:
        track, index = ref
        for bar in self._bars.get(track, ()):
            if bar.index == index:
                return bar
        return None

    def _hit(self, x: float, y: float) -> tuple[tl.TrackBar | None, str]:
        """(bar under the pointer, the drag mode it would start)."""
        if y < RULER_HEIGHT:
            return None, "seek"
        row = int((y - RULER_HEIGHT) // (LANE_HEIGHT + LANE_GAP))
        if not 0 <= row < len(tl.TRACKS):
            return None, "none"
        bars = self._bars[tl.TRACKS[row]]
        # The bar the pointer is inside wins; only then one just beside it
        # (so at a boundary between two clips each side grabs its own edge).
        inside = [b for b in bars if self.x_for(b.start) <= x < self.x_for(b.end)]
        near = [b for b in bars if self.x_for(b.start) - 2 <= x <= self.x_for(b.end) + 2]
        for bar in reversed(inside or near):
            x1, x2 = self.x_for(bar.start), self.x_for(bar.end)
            if bar.can_trim_start and abs(x - x1) <= EDGE_GRAB_PX and x2 - x1 > EDGE_GRAB_PX * 2:
                return bar, "trim_start"
            if bar.can_trim_end and abs(x - x2) <= EDGE_GRAB_PX:
                return bar, "trim_end"
            return bar, "move" if bar.can_move else "select"
        return None, "seek"

    def _on_hover(self, event) -> None:
        if self._drag is not None:
            return
        _bar, mode = self._hit(self._canvas.canvasx(event.x), event.y)
        self._canvas.configure(
            cursor={"trim_start": "sb_h_double_arrow", "trim_end": "sb_h_double_arrow", "move": "fleur"}.get(mode, "arrow"),
        )

    def _set_selected(self, ref: TrackRef | None, *, notify: bool) -> None:
        if ref == self._selected:
            return
        self._selected = ref
        self._update_buttons()
        self._redraw()
        if notify:
            self._on_selection_changed(ref)

    def _on_press(self, event) -> None:
        self._canvas.focus_set()
        x, y = self._canvas.canvasx(event.x), event.y
        bar, mode = self._hit(x, y)
        if bar is None:
            if mode == "seek":
                self._set_selected(None, notify=True)
                self._drag = {"mode": "seek"}
                self._seek_to_x(x)
            return
        self._set_selected((bar.track, bar.index), notify=True)
        if mode == "select":
            return
        self._drag = {
            "mode": mode, "bar": bar, "start_x": x, "state": self._state, "moved": False, "ghost": None,
            "snap_points": self._snap_points(exclude=bar),
        }

    def _on_motion(self, event) -> None:
        drag = self._drag
        if drag is None:
            return
        x = self._canvas.canvasx(event.x)
        if drag["mode"] == "seek":
            self._seek_to_x(x)
            return
        if not drag["moved"] and abs(x - drag["start_x"]) < 3:
            return
        drag["moved"] = True
        result = self._dragged(drag, x)
        if result is None:
            return
        new_state, ghost = result
        if drag["bar"].track in _LIVE_TRACKS:
            self._on_state_edited(new_state, False, "", None)
        else:
            drag["ghost"] = ghost
            self._redraw()

    def _on_release(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag is None or drag["mode"] == "seek" or not drag["moved"]:
            return
        result = self._dragged(drag, self._canvas.canvasx(event.x))
        if result is None:
            self._redraw()
            return
        new_state, _ghost = result
        label = {"move": "Perkelta", "trim_start": "Pakeista pradžia", "trim_end": "Pakeista trukmė"}[drag["mode"]]
        if new_state == drag["state"]:
            self._redraw()
            return
        self._on_state_edited(new_state, True, label, None)

    def _seek_to_x(self, x: float) -> None:
        if self._total <= 0:
            return
        t = max(0.0, min(self._total, x / self._px_per_second))
        self.set_playhead(t)
        self._on_seek(t)

    def _snap_points(self, *, exclude: tl.TrackBar) -> list[float]:
        points = [0.0, self._total, self._playhead]
        for bars in self._bars.values():
            for bar in bars:
                if bar.track == exclude.track and bar.index == exclude.index:
                    continue
                points.extend((bar.start, bar.end))
        return points

    def _snapped(self, drag: dict, t: float) -> float:
        return tl.snap(t, drag["snap_points"], SNAP_PX / self._px_per_second)

    def _dragged(self, drag: dict, x: float) -> tuple[EditorState, tuple[str, float, float]] | None:
        """(the state as it would be with the pointer at x, the ghost
        bar to draw) for the drag in progress."""
        bar: tl.TrackBar = drag["bar"]
        state: EditorState = drag["state"]
        delta = (x - drag["start_x"]) / self._px_per_second
        mode = drag["mode"]
        track = bar.track
        length = bar.end - bar.start

        if mode == "move":
            start = bar.start + delta
            snapped_start = self._snapped(drag, start)
            snapped_end = self._snapped(drag, start + length)
            if snapped_start != start:
                start = snapped_start
            elif snapped_end != start + length:
                start = snapped_end - length
            if track == "video":
                target = tl.drop_index(state, self._media_items, start + length / 2, moving=bar.index)
                return tl.move_item(state, bar.index, target), (track, max(0.0, start), max(0.0, start) + length)
            return tl.move_overlay(state, track, bar.index, start, total=self._total), (track, start, start + length)

        if mode == "trim_start":
            new_start = min(self._snapped(drag, bar.start + delta), bar.end - tl.MIN_DURATION_SECONDS)
            if track == "video":
                return tl.trim_item_start(state, bar.index, new_start - bar.start), (track, new_start, bar.end)
            return tl.trim_overlay(state, track, bar.index, start=new_start, total=self._total), (track, new_start, bar.end)

        new_end = max(self._snapped(drag, bar.end + delta), bar.start + tl.MIN_DURATION_SECONDS)
        if track == "video":
            new_state = tl.set_item_duration(state, bar.index, new_end - bar.start, self._media_items)
            return new_state, (track, bar.start, new_end)
        if track == "audio":
            return tl.set_music_length(state, new_end - bar.start), (track, bar.start, new_end)
        return tl.trim_overlay(state, track, bar.index, end=new_end, total=self._total), (track, bar.start, new_end)

    # --- wheel ------------------------------------------------------------------------------------

    def _on_zoom_wheel(self, event) -> None:
        up = getattr(event, "delta", 0) > 0 or getattr(event, "num", 0) == 4
        self.zoom(1.25 if up else 0.8)

    def _on_scroll_wheel(self, event) -> None:
        self._canvas.xview_scroll(-40 if event.delta > 0 else 40, "units")


def _new_clip_id() -> str:
    return uuid.uuid4().hex[:12]
