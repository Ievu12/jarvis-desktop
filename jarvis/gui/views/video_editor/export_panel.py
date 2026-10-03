"""Export panel: aspect ratio is read from the Timeline itself (set via
TimelinePanel's own "Format:" dropdown - a timeline only ever has ONE
aspect ratio, so this panel doesn't duplicate that choice), resolution
tier (720p/1080p/4K, requirement 8) is this panel's own control, plus a
real progress bar bound to jarvis.gui.worker.ProgressResult and a
Cancel button wired to a real threading.Event - see
jarvis.video_editor.multisource_export.export_timeline()'s own
docstring for the actual cancellation mechanism this triggers.

No in-app video playback here either (same established convention as
jarvis.gui.views.video_studio.reel_creator_panel/jarvis.gui.views
.reel_generator.dashboard's own _open_video()) - "Open exported file"
launches the real rendered MP4 in the OS's own default player via
os.startfile()."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import LabeledDropdown, format_duration, format_file_size, status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor.multisource_export import RESOLUTION_TIER_CHOICES, ExportResult


class ExportPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, on_export_clicked: Callable[[str], None], on_cancel_clicked: Callable[[], None], **kwargs,
    ) -> None:
        """`on_export_clicked(resolution_tier)` is called when the
        Export button is pressed - this panel never calls
        export_timeline()/run_cancelable_in_background() itself, it only
        collects the person's own resolution-tier choice and hands
        control back to the owning dashboard, which knows the current
        Timeline/MediaItems/project paths."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_export_clicked = on_export_clicked
        self._on_cancel_clicked = on_cancel_clicked
        self._last_result: ExportResult | None = None

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="📤 EKSPORTAS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        controls_row = ctk.CTkFrame(inner, fg_color="transparent")
        controls_row.pack(fill="x")
        self._resolution_dropdown = LabeledDropdown(controls_row, "Raiška:", RESOLUTION_TIER_CHOICES)
        self._resolution_dropdown.set("1080p")
        self._resolution_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        self._export_button = ctk.CTkButton(controls_row, text="📤 Eksportuoti MP4", command=self._handle_export_clicked, width=140)
        self._export_button.pack(side="left", padx=(0, theme.SPACE_SM))
        self._cancel_button = ctk.CTkButton(
            controls_row, text="✖ Atšaukti", command=self._on_cancel_clicked, width=100, state="disabled",
            fg_color=theme.DANGER, hover_color=theme.DANGER,
        )
        self._cancel_button.pack(side="left")

        self._progress_bar = ctk.CTkProgressBar(inner)
        self._progress_bar.set(0.0)
        self._progress_bar.pack(fill="x", pady=(theme.SPACE_SM, 0))
        self._progress_bar.pack_forget()  # hidden until an export is actually running

        self._status_container = ctk.CTkFrame(inner, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

    def _handle_export_clicked(self) -> None:
        self._on_export_clicked(self._resolution_dropdown.get())

    def set_exporting_state(self, *, exporting: bool) -> None:
        self._export_button.configure(state="disabled" if exporting else "normal")
        self._cancel_button.configure(state="normal" if exporting else "disabled")
        if exporting:
            self._progress_bar.set(0.0)
            self._progress_bar.pack(fill="x", pady=(theme.SPACE_SM, 0))
        else:
            self._progress_bar.pack_forget()

    def update_progress(self, percent: float) -> None:
        self._progress_bar.set(max(0.0, min(1.0, percent / 100.0)))

    def show_result(self, result: ExportResult) -> None:
        self._last_result = result
        for child in self._status_container.winfo_children():
            child.destroy()
        status_label(
            self._status_container,
            f"✅ Eksportuota {result.width}x{result.height}, {format_duration(result.duration_seconds)}, "
            f"{format_file_size(result.file_size_bytes)}",
            kind="muted",
        ).pack(anchor="w")
        ctk.CTkButton(
            self._status_container, text="▶ Atidaryti failą", command=lambda: self._open_file(result.output_path),
            width=170, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def show_error(self, message: str) -> None:
        for child in self._status_container.winfo_children():
            child.destroy()
        status_label(self._status_container, f"⚠️ {message}", kind="error").pack(anchor="w")

    def show_cancelled(self) -> None:
        for child in self._status_container.winfo_children():
            child.destroy()
        status_label(self._status_container, "Eksportas atšauktas.", kind="muted").pack(anchor="w")

    def _open_file(self, path: Path) -> None:
        if not path.is_file():
            self.show_error("The exported file could not be found on disk.")
            return
        try:
            os.startfile(str(path))  # noqa: S606 - opening a file this process itself just wrote, in the OS's own default player
        except OSError as e:
            self.show_error(f"Couldn't open the exported file: {e}")
