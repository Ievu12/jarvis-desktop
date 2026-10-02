"""Shared UI building blocks for AI Video Studio's views - a project
summary card for the "Recent Projects" strip, a labeled metadata row,
and small formatting helpers (duration/file-size display). Pure UI, no
calls into jarvis.video_studio - each view module wires these to its
own data.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card


def format_duration(seconds: float) -> str:
    """MM:SS (or H:MM:SS for anything an hour or longer) - the module
    brief's own example ("Duration: 01:42") uses MM:SS, so that's the
    default; a Reel-length or short source video is never expected to
    exceed an hour, but the fallback avoids a nonsensical "142:00" for
    a long upload."""
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_file_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def status_label(master, text: str, *, kind: str = "muted") -> ctk.CTkLabel:
    """Same loading/error/muted convention as
    jarvis.gui.views.instagram_ai_manager.common.status_label - kept as
    a separate copy rather than a shared import across the two view
    packages, matching how neither jarvis.instagram_ai_manager nor
    jarvis.video_studio import from the other's UI package (each
    feature module's UI stays self-contained; only jarvis.gui.widgets/
    jarvis.gui.theme are shared app-wide)."""
    color = {
        "loading": theme.ACCENT_PRIMARY, "error": theme.DANGER, "muted": theme.TEXT_MUTED,
    }.get(kind, theme.TEXT_MUTED)
    return ctk.CTkLabel(
        master, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        text_color=color, anchor="w",
    )


class LabeledDropdown(ctk.CTkFrame):
    """A label above a dropdown (CTkOptionMenu) - used for the Reel
    Creator's duration/style/pacing selectors (module brief, section
    4). Same shape as jarvis.gui.views.instagram_ai_manager.common
    .LabeledDropdown - kept as a separate copy per this package's own
    "each feature module's UI stays self-contained" convention (see
    status_label()'s docstring above for the same reasoning)."""

    def __init__(self, master, label: str, values: tuple[str, ...], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        self._var = ctk.StringVar(value=values[0] if values else "")
        self.dropdown = ctk.CTkOptionMenu(
            self, values=list(values), variable=self._var,
            fg_color=theme.BG_CARD, button_color=theme.ACCENT_PRIMARY,
            button_hover_color=theme.ACCENT_PRIMARY_HOVER,
        )
        self.dropdown.pack(fill="x")

    def get(self) -> str:
        return self._var.get()


class ProjectCard(Card):
    """One project's summary tile for the "Recent Projects" strip
    (module brief, section 1) - filename, status, created date, and a
    click handler to open it."""

    def __init__(
        self, master, *, filename: str, status: str, created_at: str,
        on_click: Callable[[], None], **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        button = ctk.CTkButton(
            self, text="", fg_color="transparent", hover_color=theme.BG_CARD_HOVER,
            command=on_click, height=90, corner_radius=theme.RADIUS_CARD,
        )
        button.place(relx=0, rely=0, relwidth=1, relheight=1)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_SM)
        ctk.CTkLabel(
            content, text=filename,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=180,
        ).pack(anchor="w")
        ctk.CTkLabel(
            content, text=status.replace("_", " ").title(),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
        ctk.CTkLabel(
            content, text=created_at[:16].replace("T", " "),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
