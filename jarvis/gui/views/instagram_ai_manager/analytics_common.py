"""Shared UI building blocks for the Analytics tab (jarvis.gui.views
.instagram_ai_manager.analytics_view) - a lightweight horizontal bar
"chart" built from plain CTk frames (no matplotlib or other new
dependency - the module's brief lists "matplotlib for charts" as a
recommended option, but every visualization this tab actually needs
(comparing a handful of labeled values) is well served by a proportional
bar, and adding a whole plotting library/canvas-embedding dependency for
that would be disproportionate - see this package's other modules for
the same "no new dependency" preference), plus an insufficient-data
message card reused by every analytics section for the
"insufficient_data" flag every jarvis.instagram_ai_manager
.analytics_services result carries.
"""

from __future__ import annotations

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card


class BarRow(ctk.CTkFrame):
    """One labeled horizontal bar: a left-aligned label, a bar whose
    width is proportional to `value / max_value`, and the raw value
    formatted by the caller. Used for reach/engagement/follower-style
    comparisons across a handful of items (e.g. top Reels, weekday
    comparison) - not a general charting widget, just enough to make
    relative magnitude visually obvious without a plotting dependency."""

    _BAR_MAX_WIDTH = 260
    _BAR_HEIGHT = 14

    def __init__(
        self, master, *, label: str, value: float, max_value: float, value_text: str, **kwargs
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w", width=140, wraplength=140, justify="left",
        ).grid(row=0, column=0, sticky="w", padx=(0, theme.SPACE_SM))

        track = ctk.CTkFrame(
            self, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON,
            width=self._BAR_MAX_WIDTH, height=self._BAR_HEIGHT,
        )
        track.grid(row=0, column=1, sticky="w")
        track.grid_propagate(False)

        fraction = 0.0 if max_value <= 0 else max(0.0, min(1.0, value / max_value))
        fill_width = max(2, int(self._BAR_MAX_WIDTH * fraction))
        fill = ctk.CTkFrame(
            track, fg_color=theme.ACCENT_PRIMARY, corner_radius=theme.RADIUS_BUTTON,
            width=fill_width, height=self._BAR_HEIGHT,
        )
        fill.place(x=0, y=0)

        ctk.CTkLabel(
            self, text=value_text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).grid(row=0, column=2, sticky="w", padx=(theme.SPACE_SM, 0))


class BarChartCard(Card):
    """A titled card containing a vertical stack of BarRows - the
    standard shape used for every ranked-comparison visualization on
    this tab (top Reels by performance score, best days, best hours,
    format comparison)."""

    def __init__(self, master, title: str, rows: list[tuple[str, float, str]], **kwargs) -> None:
        """`rows` is a list of (label, value, value_text) tuples,
        already in the order they should be displayed - this widget
        does not sort them itself, since callers already rank their own
        data (e.g. by performance_score) and may want to preserve that
        order over a purely by-value bar-length order."""
        super().__init__(master, **kwargs)
        ctk.CTkLabel(
            self, text=title, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM))

        if not rows:
            ctk.CTkLabel(
                self, text="No data.", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
            return

        max_value = max((v for _, v, _ in rows), default=0.0)
        for label, value, value_text in rows:
            BarRow(
                self, label=label, value=value, max_value=max_value, value_text=value_text,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_SM))
        ctk.CTkFrame(self, fg_color="transparent", height=theme.SPACE_XS).pack()


def insufficient_data_card(master, message: str | None) -> Card:
    """A standard "not enough data yet" card - used by every analytics
    section for its own insufficient_data flag, so the wording/styling
    of "we're not going to guess" is consistent across all five
    sections rather than each writing its own."""
    card = Card(master)
    ctk.CTkLabel(
        card, text="Not enough data yet",
        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
        text_color=theme.TEXT_SECONDARY, anchor="w",
    ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
    ctk.CTkLabel(
        card, text=message or "JARVIS won't guess at a trend or ranking without enough real data behind it.",
        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
    ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
    return card


def error_card(master, message: str) -> Card:
    """A standard error-state card (e.g. Instagram not connected, or the
    background fetch itself raised) - visually distinct (DANGER-colored
    heading) from insufficient_data_card's neutral "not enough data yet"
    so a person can tell "nothing to show because JARVIS can't reach
    Instagram" apart from "nothing to show because there isn't enough
    history"."""
    card = Card(master)
    ctk.CTkLabel(
        card, text="Couldn't load this",
        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
        text_color=theme.DANGER, anchor="w",
    ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
    ctk.CTkLabel(
        card, text=message,
        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
    ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
    return card
