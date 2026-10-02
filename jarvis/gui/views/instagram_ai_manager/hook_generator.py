"""Hook Generator tool (Content Studio, tool B): enter a topic, generate
hooks grouped by category (curiosity/problem_solution/educational/
controversial_but_factual/emotional/storytelling/question/authority/
short_punchy), let the person select a preferred hook, with Generate/
Copy/Save actions.

Same structure as jarvis.gui.views.instagram_ai_manager.reel_ideas - see
that module's docstring for the shared background-call/save pattern.
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
    "curiosity": "Curiosity", "problem_solution": "Problem / Solution",
    "educational": "Educational", "controversial_but_factual": "Controversial but Factual",
    "emotional": "Emotional", "storytelling": "Storytelling", "question": "Question",
    "authority": "Authority", "short_punchy": "Short & Punchy",
}


class HookGeneratorPanel(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]") -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._current_hooks: dict[str, list[str]] | None = None
        self._current_topic: str = ""
        self._selected_hook: str | None = None

        SectionHeader(self, "Hook Generator").pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            self, text="Generate hooks across multiple categories for a topic, then select your favorite.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        self._topic_entry = LabeledEntry(self, "Topic:", placeholder="e.g. skincare routines")
        self._topic_entry.pack(fill="x", pady=(0, theme.SPACE_MD))

        self._action_bar = GeneratorActionBar(
            self, on_generate=self._on_generate_clicked, on_copy=self._on_copy_selected_clicked,
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

    def _select_hook(self, hook_text: str) -> None:
        self._selected_hook = hook_text
        self._render_hooks(self._current_hooks or {})  # re-render to update selection highlight

    def _render_hooks(self, hooks: dict[str, list[str]]) -> None:
        for widget in self._results_container.winfo_children():
            widget.destroy()

        for category, hook_list in hooks.items():
            label = _CATEGORY_LABELS.get(category, category)
            card = Card(self._results_container)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))
            ctk.CTkLabel(
                card, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))

            for hook_text in hook_list:
                is_selected = hook_text == self._selected_hook
                row = ctk.CTkButton(
                    card, text=hook_text, anchor="w",
                    fg_color=theme.ACCENT_PRIMARY if is_selected else "transparent",
                    hover_color=theme.BG_CARD_HOVER if not is_selected else theme.ACCENT_PRIMARY_HOVER,
                    text_color=theme.BG_PRIMARY if is_selected else theme.TEXT_SECONDARY,
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                    command=lambda h=hook_text: self._select_hook(h),
                )
                row.pack(fill="x", padx=theme.SPACE_MD, pady=2)
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
        self._selected_hook = None
        self._action_bar.set_busy(True)
        self._set_status("Generating hooks...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: ai_services.generate_hooks(llm, topic), self._result_queue, source=self,
        )

    def handle_generation_result(self, result: GenerationTaskResult) -> None:
        self._action_bar.set_busy(False)
        if result.error:
            self._current_hooks = None
            self._set_status(f"Generation failed: {result.error}", kind="error")
            return
        if result.value is None:
            self._current_hooks = None
            self._set_status("JARVIS couldn't generate hooks for that topic - try again.", kind="error")
            return
        self._current_hooks = result.value
        self._clear_status()
        self._render_hooks(result.value)

    def _on_save_clicked(self) -> None:
        if not self._current_hooks:
            self._set_status("Nothing to save yet - generate hooks first.", kind="error")
            return
        db.save_hook_set(self._current_topic, self._current_hooks)
        self._set_status(f"Saved hooks for \"{self._current_topic}\".", kind="muted")

    def _on_copy_selected_clicked(self) -> None:
        if self._selected_hook:
            copy_to_clipboard(self, self._selected_hook)
            self._set_status("Copied selected hook to clipboard.", kind="muted")
            return
        if not self._current_hooks:
            self._set_status("Nothing to copy yet - generate hooks first.", kind="error")
            return
        # No specific hook selected - copy everything, grouped by category.
        parts = []
        for category, hook_list in self._current_hooks.items():
            label = _CATEGORY_LABELS.get(category, category)
            parts.append(f"{label}:\n" + "\n".join(f"- {h}" for h in hook_list))
        copy_to_clipboard(self, "\n\n".join(parts))
        self._set_status("Copied all hooks to clipboard.", kind="muted")
