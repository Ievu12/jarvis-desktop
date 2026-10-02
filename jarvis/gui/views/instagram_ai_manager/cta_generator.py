"""CTA Generator tool (Content Studio, tool D): generate calls-to-action
grouped by category (comments/saves/shares/follows/dm/website/product/
engagement) for a topic - with Generate/Copy/Save actions. Same
structure as jarvis.gui.views.instagram_ai_manager.hook_generator (see
that module's docstring); CTAs don't have a "select one" interaction
per the module's brief (only "allow copying and saving"), so this panel
skips the selection-highlight behavior hook_generator has.
"""

from __future__ import annotations

import queue
from typing import Any

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.instagram_ai_manager.common import (
    GeneratorActionBar,
    LabeledEntry,
    copy_to_clipboard,
    status_label,
)
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.instagram_ai_manager import ai_services, db

_CATEGORY_LABELS = {
    "comments": "Comments", "saves": "Saves", "shares": "Shares", "follows": "Follows",
    "dm": "DM", "website": "Website", "product": "Product", "engagement": "Engagement",
}


class CTAGeneratorPanel(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]") -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._current_ctas: dict[str, list[str]] | None = None
        self._current_topic: str = ""

        SectionHeader(self, "CTA Generator").pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            self, text="Generate calls-to-action grouped by goal for a topic.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        self._topic_entry = LabeledEntry(self, "Topic:", placeholder="e.g. skincare routines")
        self._topic_entry.pack(fill="x", pady=(0, theme.SPACE_MD))

        self._action_bar = GeneratorActionBar(
            self, on_generate=self._on_generate_clicked, on_copy=self._on_copy_all_clicked,
            on_save=self._on_save_clicked,
        )
        self._action_bar.pack(anchor="w", pady=(0, theme.SPACE_MD))

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x")

        self._results_container = ctk.CTkScrollableFrame(self, fg_color="transparent", height=400)
        self._results_container.pack(fill="both", expand=True)

        self._set_status("Enter a topic and click Generate.", kind="muted")

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _render_ctas(self, ctas: dict[str, list[str]]) -> None:
        for widget in self._results_container.winfo_children():
            widget.destroy()

        for category, cta_list in ctas.items():
            label = _CATEGORY_LABELS.get(category, category)
            card = Card(self._results_container)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))
            ctk.CTkLabel(
                card, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
            for cta_text in cta_list:
                row = ctk.CTkFrame(card, fg_color="transparent")
                row.pack(fill="x", padx=theme.SPACE_MD, pady=2)
                ctk.CTkLabel(
                    row, text=cta_text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                    text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=460,
                ).pack(side="left", fill="x", expand=True)
                ctk.CTkButton(
                    row, text="Copy", width=50, height=22, fg_color=theme.BG_CARD_HOVER,
                    hover_color=theme.BORDER_SUBTLE, font=ctk.CTkFont(size=theme.FONT_SIZE_CAPTION),
                    command=lambda t=cta_text: self._copy_one(t),
                ).pack(side="right")
            ctk.CTkFrame(card, fg_color="transparent", height=theme.SPACE_SM).pack()

    def _copy_one(self, text: str) -> None:
        copy_to_clipboard(self, text)
        self._set_status("Copied to clipboard.", kind="muted")

    def _on_generate_clicked(self) -> None:
        topic = self._topic_entry.get()
        if not topic:
            self._set_status("Enter a topic first.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        self._current_topic = topic
        self._action_bar.set_busy(True)
        self._set_status("Generating CTAs...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: ai_services.generate_cta(llm, topic), self._result_queue, source=self,
        )

    def handle_generation_result(self, result: GenerationTaskResult) -> None:
        self._action_bar.set_busy(False)
        if result.error:
            self._current_ctas = None
            self._set_status(f"Generation failed: {result.error}", kind="error")
            return
        if result.value is None:
            self._current_ctas = None
            self._set_status("JARVIS couldn't generate CTAs for that topic - try again.", kind="error")
            return
        self._current_ctas = result.value
        self._clear_status()
        self._render_ctas(result.value)

    def _on_save_clicked(self) -> None:
        if not self._current_ctas:
            self._set_status("Nothing to save yet - generate CTAs first.", kind="error")
            return
        db.save_cta_set(self._current_topic, self._current_ctas)
        self._set_status(f"Saved CTAs for \"{self._current_topic}\".", kind="muted")

    def _on_copy_all_clicked(self) -> None:
        if not self._current_ctas:
            self._set_status("Nothing to copy yet - generate CTAs first.", kind="error")
            return
        parts = []
        for category, cta_list in self._current_ctas.items():
            label = _CATEGORY_LABELS.get(category, category)
            parts.append(f"{label}:\n" + "\n".join(f"- {c}" for c in cta_list))
        copy_to_clipboard(self, "\n\n".join(parts))
        self._set_status("Copied all CTAs to clipboard.", kind="muted")
