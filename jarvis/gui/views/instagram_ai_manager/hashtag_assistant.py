"""Hashtag Assistant tool (Content Studio, tool E): generate hashtags
grouped by competition tier (niche/medium_competition/broader/branded)
for a topic, let the person select individual hashtags via checkboxes,
with Copy All / Copy Selected / Save Set actions per the module's brief.
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

_GROUP_LABELS = {
    "niche": "Niche", "medium_competition": "Medium Competition",
    "broader": "Broader", "branded": "Branded (suggestions)",
}


class HashtagAssistantPanel(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]") -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._current_hashtags: dict[str, list[str]] | None = None
        self._current_topic: str = ""
        self._checkbox_vars: dict[str, ctk.BooleanVar] = {}

        SectionHeader(self, "Hashtag Assistant").pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            self, text="Generates hashtags actually relevant to your topic - not generic viral tags.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        self._topic_entry = LabeledEntry(self, "Topic:", placeholder="e.g. skincare routines")
        self._topic_entry.pack(fill="x", pady=(0, theme.SPACE_MD))

        self._action_bar = GeneratorActionBar(
            self, on_generate=self._on_generate_clicked, on_save=self._on_save_clicked,
            generate_label="Generate",
        )
        # Copy All / Copy Selected are distinct enough from
        # GeneratorActionBar's single generic "Copy" button that they're
        # added here directly rather than stretching that shared
        # component's contract for one tool.
        self._copy_all_button = ctk.CTkButton(
            self._action_bar, text="Copy All", command=self._on_copy_all_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        )
        self._copy_all_button.pack(side="left", padx=(theme.SPACE_SM, 0))
        self._copy_selected_button = ctk.CTkButton(
            self._action_bar, text="Copy Selected", command=self._on_copy_selected_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        )
        self._copy_selected_button.pack(side="left", padx=(theme.SPACE_SM, 0))
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

    def _render_hashtags(self, hashtags: dict[str, list[str]]) -> None:
        for widget in self._results_container.winfo_children():
            widget.destroy()
        self._checkbox_vars = {}

        for group, tags in hashtags.items():
            label = _GROUP_LABELS.get(group, group)
            card = Card(self._results_container)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))
            ctk.CTkLabel(
                card, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
            for tag in tags:
                var = ctk.BooleanVar(value=False)
                self._checkbox_vars[tag] = var
                ctk.CTkCheckBox(
                    card, text=tag, variable=var,
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                    text_color=theme.TEXT_SECONDARY,
                ).pack(anchor="w", padx=theme.SPACE_MD, pady=2)
            ctk.CTkFrame(card, fg_color="transparent", height=theme.SPACE_SM).pack()

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
        self._set_status("Generating hashtags...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: ai_services.generate_hashtags(llm, topic), self._result_queue, source=self,
        )

    def handle_generation_result(self, result: GenerationTaskResult) -> None:
        self._action_bar.set_busy(False)
        if result.error:
            self._current_hashtags = None
            self._set_status(f"Generation failed: {result.error}", kind="error")
            return
        if result.value is None:
            self._current_hashtags = None
            self._set_status("JARVIS couldn't generate hashtags for that topic - try again.", kind="error")
            return
        self._current_hashtags = result.value
        self._clear_status()
        self._render_hashtags(result.value)

    def _on_save_clicked(self) -> None:
        if not self._current_hashtags:
            self._set_status("Nothing to save yet - generate hashtags first.", kind="error")
            return
        db.save_hashtag_set(self._current_topic, self._current_hashtags)
        self._set_status(f"Saved hashtag set for \"{self._current_topic}\".", kind="muted")

    def _on_copy_all_clicked(self) -> None:
        if not self._current_hashtags:
            self._set_status("Nothing to copy yet - generate hashtags first.", kind="error")
            return
        all_tags = [tag for tags in self._current_hashtags.values() for tag in tags]
        copy_to_clipboard(self, " ".join(all_tags))
        self._set_status(f"Copied all {len(all_tags)} hashtags to clipboard.", kind="muted")

    def _on_copy_selected_clicked(self) -> None:
        selected = [tag for tag, var in self._checkbox_vars.items() if var.get()]
        if not selected:
            self._set_status("No hashtags selected - check the ones you want to copy.", kind="error")
            return
        copy_to_clipboard(self, " ".join(selected))
        self._set_status(f"Copied {len(selected)} selected hashtags to clipboard.", kind="muted")
