"""Small, reusable presentational widgets shared across the dashboard's
views (jarvis/gui/views/*.py) - a card container, a status pill, and a
section header. Pure UI, no JARVIS core/backend calls; every view
passes in already-fetched data (from jarvis.gui.dashboard_data) or a
callback, never reaches into JARVIS state itself.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme


class Card(ctk.CTkFrame):
    """The base 'glass panel' container used throughout the dashboard:
    a rounded, softly-bordered dark surface. Not a literal blurred
    glass effect (Tkinter can't render that - see jarvis.gui.theme's
    module docstring) - a consistent, layered dark card is the
    practical approximation used everywhere a 'panel' is called for."""

    def __init__(self, master, **kwargs):
        kwargs.setdefault("fg_color", theme.BG_CARD)
        kwargs.setdefault("corner_radius", theme.RADIUS_CARD)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.BORDER_SUBTLE)
        super().__init__(master, **kwargs)


class SectionHeader(ctk.CTkLabel):
    def __init__(self, master, text: str, **kwargs):
        kwargs.setdefault(
            "font", ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SUBTITLE, weight="bold"),
        )
        kwargs.setdefault("text_color", theme.TEXT_PRIMARY)
        kwargs.setdefault("anchor", "w")
        super().__init__(master, text=text, **kwargs)


class StatusPill(ctk.CTkLabel):
    """A small rounded label used for connection/task/automation status
    (e.g. 'CONNECTED', 'DONE', 'NOT CONFIGURED') - color chosen by the
    caller so this widget stays agnostic of any particular status enum
    (jarvis.integrations.manager.IntegrationStatus and a Task's
    done/not-done are different shapes, both rendered through this same
    widget)."""

    def __init__(self, master, text: str, color: str, **kwargs):
        kwargs.setdefault("font", ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"))
        kwargs.setdefault("fg_color", color)
        kwargs.setdefault("text_color", theme.BG_PRIMARY)
        kwargs.setdefault("corner_radius", theme.RADIUS_PILL)
        kwargs.setdefault("width", 0)
        super().__init__(master, text=f"  {text}  ", **kwargs)


class QuickActionButton(ctk.CTkButton):
    """A Home-view 'Quick Action' button (e.g. 'Check Instagram') - a
    thin styling wrapper; `command` is supplied by the caller (Home
    view), which is the only place that decides what a quick action
    actually does (typically: pre-fill and submit a chat message via
    JarvisApp's existing _submit_user_input(), reusing the real
    Agent.step() path rather than a separate action implementation)."""

    def __init__(self, master, text: str, *, command: Callable[[], None], **kwargs):
        kwargs.setdefault("fg_color", theme.BG_CARD)
        kwargs.setdefault("hover_color", theme.BG_CARD_HOVER)
        kwargs.setdefault("text_color", theme.TEXT_PRIMARY)
        kwargs.setdefault("border_width", 1)
        kwargs.setdefault("border_color", theme.BORDER_SUBTLE)
        kwargs.setdefault("corner_radius", theme.RADIUS_BUTTON)
        kwargs.setdefault("font", ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY))
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("height", 40)
        super().__init__(master, text=text, command=command, **kwargs)


def empty_state_label(master, text: str) -> ctk.CTkLabel:
    """A consistent 'nothing here yet' placeholder, used by every
    data-driven view (Tasks/Activity/Integrations/Automations) when its
    underlying jarvis.gui.dashboard_data call returns an empty list -
    never left as a blank panel, which reads as broken rather than
    empty."""
    return ctk.CTkLabel(
        master, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
        text_color=theme.TEXT_MUTED,
    )
