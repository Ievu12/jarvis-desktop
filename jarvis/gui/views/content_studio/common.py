"""Shared UI building blocks for AI Content Studio's views - a status
label and a project summary tile for the "My Projects" library listing.
Pure UI, no calls into jarvis.content_studio - each panel wires these
to its own data.

Kept as this package's own self-contained copy (not a shared import
from a sibling module's own common.py) per this codebase's established
"each feature module's UI stays self-contained" convention - see
jarvis.gui.views.reel_generator.common/.design_studio.common's own
docstrings for the same reasoning stated there."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card

# One badge color per workflow state (Stage 2's own planned/creating/
# created/approved/exported/failed vocabulary - see
# jarvis.content_studio.db's own WORKFLOW_STATES docstring) - a person
# scanning the library should be able to tell a project's furthest-
# along content type's status at a glance without reading every field.
_STATUS_COLORS = {
    "planned": theme.TEXT_MUTED, "creating": theme.ACCENT_PRIMARY, "created": theme.ACCENT_PRIMARY,
    "approved": theme.SUCCESS, "exported": theme.SUCCESS, "failed": theme.DANGER,
}


def status_label(master, text: str, *, kind: str = "muted") -> ctk.CTkLabel:
    color = {
        "loading": theme.ACCENT_PRIMARY, "error": theme.DANGER, "muted": theme.TEXT_MUTED,
    }.get(kind, theme.TEXT_MUTED)
    return ctk.CTkLabel(
        master, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        text_color=color, anchor="w",
    )


def workflow_badge_color(status: str) -> str:
    return _STATUS_COLORS.get(status, theme.TEXT_MUTED)


class ContentProjectCard(Card):
    """One Content Studio project's summary tile for the "My Projects"
    library (module brief section 7) - topic, created date, and a
    per-content-type status badge row, plus a click handler to reopen
    it."""

    def __init__(
        self, master, *, topic: str, statuses: dict[str, str], created_at: str,
        on_click: Callable[[], None], **kwargs,
    ) -> None:
        super().__init__(master, **kwargs)
        button = ctk.CTkButton(
            self, text="", fg_color="transparent", hover_color=theme.BG_CARD_HOVER,
            command=on_click, height=140, corner_radius=theme.RADIUS_CARD,
        )
        button.place(relx=0, rely=0, relwidth=1, relheight=1)

        content = ctk.CTkFrame(self, fg_color="transparent")
        content.pack(fill="both", expand=True, padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        ctk.CTkLabel(
            content, text=topic[:70] + ("..." if len(topic) > 70 else ""),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=200, justify="left",
        ).pack(anchor="w")

        badges_text = "  ·  ".join(
            f"{ct.title()}: {status.title()}" for ct, status in statuses.items() if status != "planned"
        ) or "All planned"
        has_failure = any(status == "failed" for status in statuses.values())
        ctk.CTkLabel(
            content, text=badges_text,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.DANGER if has_failure else theme.ACCENT_PRIMARY, anchor="w",
            wraplength=200, justify="left",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        ctk.CTkLabel(
            content, text=created_at[:16].replace("T", " "),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
