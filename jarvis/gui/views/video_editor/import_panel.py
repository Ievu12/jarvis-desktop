"""Import panel: the "upload video or photo" area and the resulting
media list for one Video Editor project. File selection uses
tkinter.filedialog directly, same convention jarvis.gui.views
.video_studio.dashboard already established (no existing drag-and-drop
pattern anywhere in this GUI to reuse - confirmed by this feature's own
architecture inspection, and tkinterdnd2 is not a dependency of this
project). Every import is real, possibly-slow I/O (copying a video
file, probing it) and therefore run through jarvis.gui.worker
.run_generation_in_background() on the owning dashboard's own result
queue - this panel itself holds no background-thread logic, it only
renders the upload button/media list and calls back into its owner."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import format_duration
from jarvis.gui.widgets import Card
from jarvis.video_editor.media_import import MediaItem

_FILETYPES = (
    ("Video and photo files", "*.mp4 *.mov *.m4v *.webm *.jpg *.jpeg *.png"),
    ("Video files", "*.mp4 *.mov *.m4v *.webm"),
    ("Photo files", "*.jpg *.jpeg *.png"),
    ("All files", "*.*"),
)


class ImportPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, on_files_chosen: Callable[[list[Path]], None],
        on_add_to_timeline: Callable[[MediaItem], None], **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_files_chosen = on_files_chosen
        self._on_add_to_timeline = on_add_to_timeline

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="📥 IMPORT MEDIA",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner, text="Add video clips (MP4/MOV/M4V/WEBM) and photos (JPG/PNG) to this project.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkButton(
            inner, text="➕ Add video or photo", command=self._on_add_clicked, width=200,
        ).pack(anchor="w")

        self._media_list_container = ctk.CTkFrame(self, fg_color="transparent")
        self._media_list_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

    def _on_add_clicked(self) -> None:
        paths = filedialog.askopenfilenames(title="Add video or photo", filetypes=_FILETYPES)
        if not paths:
            return
        self._on_files_chosen([Path(p) for p in paths])

    def render_media_list(self, media_items: dict[str, MediaItem]) -> None:
        """Re-renders the imported-media list from the CURRENT real
        state - called by the owning dashboard after every import
        completes, never maintaining its own separate copy of the media
        list (same "read already-tracked state, don't invent a second
        source of truth" convention established across this codebase's
        other dashboards, e.g. jarvis.gui.views.reel_generator
        .dashboard's own step-indicator derivation)."""
        for child in self._media_list_container.winfo_children():
            child.destroy()
        if not media_items:
            return

        card = Card(self._media_list_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text=f"🎞️ {len(media_items)} item(s) imported",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        for item in media_items.values():
            if item.kind == "video":
                detail = f"{item.width}x{item.height}, {format_duration(item.duration_seconds or 0.0)}"
            else:
                detail = f"{item.width}x{item.height} photo"
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            ctk.CTkLabel(
                row, text=f"• {item.original_filename} ({detail})",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(side="left")
            ctk.CTkButton(
                row, text="➕ Add to Timeline", width=140, height=24,
                command=lambda m=item: self._on_add_to_timeline(m),
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
            ).pack(side="right")
