"""Reel templates panel: browse jarvis.video_editor.reel_templates's
own curated templates by category and apply one with a single click -
Stage 4 of the "professional Reels editor" plan (see
jarvis.gui.views.video_editor.timeline_panel's own docstring for
Stage 3, the prior stage).

This panel never applies a template's own style to anything itself -
it only shows the catalog and, on "✨ Apply", hands the chosen
ReelTemplate back to the owning dashboard via `on_template_applied`,
which is the one place that already holds references to
TimelinePanel/CaptionsPanel/TextOverlayPanel/StickersPanel and can
route each part of the template into its own real, already-validated
apply path (CaptionsPanel.apply_style()/StickersPanel.apply_presets()/
TimelinePanel.timeline via apply_template()) - same "panel collects a
choice, dashboard knows how to apply it" division of responsibility
every other panel in this package already follows (see
ImportPanel's own docstring for the identical pattern)."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import LabeledDropdown
from jarvis.gui.widgets import Card
from jarvis.video_editor.reel_templates import (
    TEMPLATE_CATEGORY_CHOICES,
    TEMPLATE_CATEGORY_LABELS,
    ReelTemplate,
    templates_in_category,
)

_CATEGORY_DISPLAY_CHOICES = tuple(TEMPLATE_CATEGORY_LABELS[c] for c in TEMPLATE_CATEGORY_CHOICES)
_CATEGORY_BY_LABEL = {label: code for code, label in TEMPLATE_CATEGORY_LABELS.items()}


class ReelTemplatesPanel(ctk.CTkFrame):
    def __init__(self, master, *, on_template_applied: Callable[[ReelTemplate], None], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_template_applied = on_template_applied

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="🎞️ REEL TEMPLATES",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner,
            text="Ready-made styles (motion, captions, stickers) you can apply to the current "
                 "timeline, then edit freely.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._category_dropdown = LabeledDropdown(inner, "Category:", _CATEGORY_DISPLAY_CHOICES)
        self._category_dropdown.dropdown.configure(command=lambda _v: self._render())
        self._category_dropdown.pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._list_container = ctk.CTkFrame(inner, fg_color="transparent")
        self._list_container.pack(fill="x")

        self._render()

    def _render(self) -> None:
        for child in self._list_container.winfo_children():
            child.destroy()
        category = _CATEGORY_BY_LABEL.get(self._category_dropdown.get())
        for template in templates_in_category(category) if category else ():
            self._render_template_row(template)

    def _render_template_row(self, template: ReelTemplate) -> None:
        row = Card(self._list_container)
        row.pack(fill="x", pady=(0, theme.SPACE_XS))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        ctk.CTkLabel(
            inner, text=template.name,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            inner, text=template.description,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=550, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkButton(
            inner, text="✨ Apply Template", width=150, height=26,
            command=lambda t=template: self._on_template_applied(t),
        ).pack(anchor="w")
