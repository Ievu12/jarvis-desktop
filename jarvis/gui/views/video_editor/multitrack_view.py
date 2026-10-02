"""Multi-track Timeline VISUALIZATION - a read-only overview showing
Video/Music/Captions/Text as separate, time-proportional horizontal
bars, all drawn from state this dashboard already holds (Timeline
items, MusicTrack, CaptionLines, TextOverlays). This is deliberately
NOT a new data model: jarvis.video_editor.timeline.Timeline stays a
single flat ordered list (a conscious, already-tested architecture
decision - see that module's own docstring on why captions/music/text
are layered as separate overlay concepts, not tracks). Requirement
"Aiški Timeline sąsaja su matomais takeliais ir laiko žymomis" (a clear
Timeline interface with visible tracks and time markers) is satisfied
here as a VIEW over the existing state, without touching the export
pipeline, Timeline.validate(), or any panel's own editing logic -
editing still happens in each track's own real panel (TimelinePanel for
video, MusicPanel for music, CaptionsPanel for captions, TextOverlayPanel
for text); this widget never accepts a click/edit, it only draws."""

from __future__ import annotations

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card

_TRACK_HEIGHT = 28
_TRACK_GAP = 4
_CANVAS_WIDTH = 760
_LEFT_LABEL_WIDTH = 70


class MultiTrackView(ctk.CTkFrame):
    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="📐 TRACKS OVERVIEW",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._canvas = ctk.CTkCanvas(
            inner, width=_CANVAS_WIDTH, height=_TRACK_HEIGHT * 4 + _TRACK_GAP * 5 + 20,
            bg=theme.BG_CARD, highlightthickness=0,
        )
        self._canvas.pack(fill="x")

    def render(
        self, *, video_segments: list[tuple[float, float, str]], music_segment: tuple[float, float] | None,
        caption_segments: list[tuple[float, float]], text_segments: list[tuple[float, float]],
        total_duration_seconds: float,
    ) -> None:
        """Redraws every track - each `*_segments` argument is a list of
        (start_seconds, end_seconds[, label]) tuples already computed by
        the caller (the owning dashboard, which holds the real Timeline/
        MusicTrack/CaptionLines/TextOverlays state) - this widget never
        reads jarvis.video_editor state directly, keeping it a pure
        rendering function of whatever the caller passes in."""
        self._canvas.delete("all")
        total = max(total_duration_seconds, 0.01)
        usable_width = _CANVAS_WIDTH - _LEFT_LABEL_WIDTH - 10

        def x_for(seconds: float) -> float:
            return _LEFT_LABEL_WIDTH + (max(0.0, min(seconds, total)) / total) * usable_width

        tracks = [
            ("🎬 Video", video_segments, theme.ACCENT_PRIMARY),
            ("🎵 Music", [(s, e, "") for s, e in ([music_segment] if music_segment else [])], "#2d8a5f"),
            ("💬 Captions", [(s, e, "") for s, e in caption_segments], "#b38600"),
            ("🔤 Text", [(s, e, "") for s, e in text_segments], "#8a4fb3"),
        ]

        for row, (label, segments, color) in enumerate(tracks):
            y_top = row * (_TRACK_HEIGHT + _TRACK_GAP) + 10
            y_bottom = y_top + _TRACK_HEIGHT
            self._canvas.create_text(
                5, (y_top + y_bottom) / 2, text=label, anchor="w",
                fill=theme.TEXT_SECONDARY, font=(theme.FONT_FAMILY_BODY, 10),
            )
            self._canvas.create_rectangle(
                _LEFT_LABEL_WIDTH, y_top, _LEFT_LABEL_WIDTH + usable_width, y_bottom,
                fill=theme.BG_CARD_HOVER, outline="",
            )
            for start, end, seg_label in segments:
                x1, x2 = x_for(start), x_for(end)
                self._canvas.create_rectangle(x1, y_top, max(x2, x1 + 2), y_bottom, fill=color, outline="")
                if seg_label:
                    self._canvas.create_text(
                        (x1 + x2) / 2, (y_top + y_bottom) / 2, text=seg_label,
                        fill="white", font=(theme.FONT_FAMILY_BODY, 9),
                    )

        # Time axis markers (a tick every ~20% of the total duration).
        axis_y = len(tracks) * (_TRACK_HEIGHT + _TRACK_GAP) + 12
        for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
            seconds = total * fraction
            x = x_for(seconds)
            self._canvas.create_line(x, axis_y - 4, x, axis_y + 4, fill=theme.TEXT_MUTED)
            self._canvas.create_text(
                x, axis_y + 10, text=f"{seconds:.1f}s", fill=theme.TEXT_MUTED, font=(theme.FONT_FAMILY_BODY, 8),
            )
