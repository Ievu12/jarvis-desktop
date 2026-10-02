"""Shared UI building blocks for AI Design Studio's views - a labeled
dropdown, a status label, and a design-thumbnail card for the "Recent
Designs" strip. Pure UI, no calls into jarvis.design_studio - each view
module wires these to its own data.

Kept as this package's own self-contained copies (not shared imports
from jarvis.gui.views.video_studio.common/
jarvis.gui.views.instagram_ai_manager.common) per this codebase's
established "each feature module's UI stays self-contained" convention
- see either of those modules' own LabeledDropdown/status_label
docstrings for the same reasoning stated there.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk
from PIL import Image

from jarvis.gui import theme
from jarvis.gui.widgets import Card

_THUMBNAIL_SIZE = (140, 175)  # a mix of 9:16/4:5/1:1 formats - not exact for any one, just a preview tile


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


class DesignCard(Card):
    """One design's summary tile for the "Recent Designs" strip (module
    brief, section 1) - a thumbnail (if the file is still readable),
    prompt excerpt, status, and a click handler to reopen it. Mirrors
    jarvis.gui.views.video_studio.cover_panel's CTkImage thumbnail
    pattern (Image.open() wrapped in a try/except so one unreadable
    file doesn't break the whole grid - see that module's own
    docstring for the same precedent)."""

    def __init__(
        self, master, *, prompt: str, status: str, created_at: str, design_path: str | None,
        on_click: Callable[[], None], **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        button = ctk.CTkButton(
            self, text="", fg_color="transparent", hover_color=theme.BG_CARD_HOVER,
            command=on_click, height=175, corner_radius=theme.RADIUS_CARD,
        )
        button.place(relx=0, rely=0, relwidth=1, relheight=1)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        thumbnail_shown = False
        if design_path:
            try:
                pil_image = Image.open(design_path)
                ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=_THUMBNAIL_SIZE)
                ctk.CTkLabel(content, image=ctk_image, text="").pack(anchor="w")
                thumbnail_shown = True
            except Exception:
                thumbnail_shown = False

        if not thumbnail_shown:
            ctk.CTkLabel(
                content, text=prompt[:60] + ("..." if len(prompt) > 60 else ""),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=180, justify="left",
            ).pack(anchor="w")

        ctk.CTkLabel(
            content, text=status.replace("_", " ").title(),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
        ctk.CTkLabel(
            content, text=created_at[:16].replace("T", " "),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")
