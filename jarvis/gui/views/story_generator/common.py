"""Shared UI building blocks for AI Storytelling Generator's dashboard -
a labeled dropdown, a status label, and a project summary tile for the
"Recent Stories" strip. Pure UI, no calls into jarvis.story_generator -
the dashboard wires these to its own data.

Kept as this package's own self-contained copy (not a shared import
from jarvis.gui.views.reel_generator.common) per this codebase's
established "each feature module's UI stays self-contained" convention
- see that module's own LabeledDropdown/status_label docstring for the
same reasoning stated there."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card


def format_file_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def status_label(master, text: str, *, kind: str = "muted") -> ctk.CTkLabel:
    color = {
        "loading": theme.ACCENT_PRIMARY, "error": theme.DANGER, "muted": theme.TEXT_MUTED,
    }.get(kind, theme.TEXT_MUTED)
    return ctk.CTkLabel(
        master, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        text_color=color, anchor="w",
    )


class LabeledDropdown(ctk.CTkFrame):
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

    def set(self, value: str) -> None:
        self._var.set(value)


class StoryProjectCard(Card):
    """One Story project's summary tile for the "Recent Stories" strip -
    idea excerpt, status, created date, and a click handler to reopen
    it. Mirrors jarvis.gui.views.reel_generator.common.ReelProjectCard's
    exact shape."""

    def __init__(
        self, master, *, idea: str, status: str, structure_approved: bool, created_at: str,
        on_click: Callable[[], None], **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        button = ctk.CTkButton(
            self, text="", fg_color="transparent", hover_color=theme.BG_CARD_HOVER,
            command=on_click, height=120, corner_radius=theme.RADIUS_CARD,
        )
        button.place(relx=0, rely=0, relwidth=1, relheight=1)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        ctk.CTkLabel(
            content, text=idea[:70] + ("..." if len(idea) > 70 else ""),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=200, justify="left",
        ).pack(anchor="w")

        state_text = "✅ Story approved" if structure_approved else status.replace("_", " ").title()
        ctk.CTkLabel(
            content, text=state_text,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.ACCENT_PRIMARY if structure_approved else theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
        ctk.CTkLabel(
            content, text=created_at[:16].replace("T", " "),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
