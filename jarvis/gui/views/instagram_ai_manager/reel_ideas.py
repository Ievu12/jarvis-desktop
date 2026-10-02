"""Reel Ideas tool (Content Studio, tool A per the module's brief):
enter a topic/niche/goal, generate 10 Reel ideas (title/concept/
target_audience/suggested_format/hook/cta/estimated_difficulty/
why_it_may_work each), with Generate/Regenerate/Save/Copy actions.

Calls jarvis.instagram_ai_manager.ai_services.generate_reel_ideas() on a
background thread (jarvis.gui.worker.run_generation_in_background) -
never blocks the UI thread with the LLM call. Save persists the current
result set via jarvis.instagram_ai_manager.db.save_reel_idea_set().
Nothing here touches Instagram - generation is a tool-free LLM call
(see ai_services' own docstring), and this panel has no "publish"
action at all.
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
    ResultCard,
    copy_to_clipboard,
    status_label,
)
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.instagram_ai_manager import ai_services, db

_IDEA_COUNT = 10


class ReelIdeasPanel(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]") -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._current_ideas: list[dict] | None = None
        self._current_topic: str = ""

        SectionHeader(self, "Reel Ideas").pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            self, text="Enter a topic, niche, or goal to generate 10 Reel ideas.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        self._topic_entry = LabeledEntry(self, "Topic / niche / goal:", placeholder="e.g. skincare, yoga, self development")
        self._topic_entry.pack(fill="x", pady=(0, theme.SPACE_MD))

        self._action_bar = GeneratorActionBar(
            self, on_generate=self._on_generate_clicked, on_regenerate=self._on_regenerate_clicked,
            on_save=self._on_save_clicked, on_copy=self._on_copy_clicked,
        )
        self._action_bar.pack(anchor="w", pady=(0, theme.SPACE_MD))

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x")

        self._results_container = ctk.CTkScrollableFrame(self, fg_color="transparent", height=400)
        self._results_container.pack(fill="both", expand=True)

        self._set_status("Enter a topic and click Generate.", kind="muted")

    # --- status/results rendering --------------------------------------------------------

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _render_ideas(self, ideas: list[dict]) -> None:
        for widget in self._results_container.winfo_children():
            widget.destroy()

        for idea in ideas:
            body_lines = [
                f"Concept: {idea.get('concept', '')}",
                f"Target audience: {idea.get('target_audience', '')}",
                f"Format: {idea.get('suggested_format', '')}",
                f"Hook: {idea.get('hook', '')}",
                f"CTA: {idea.get('cta', '')}",
                f"Difficulty: {idea.get('estimated_difficulty', '')}",
                f"Why it may work: {idea.get('why_it_may_work', '')}",
            ]
            card = ResultCard(self._results_container, idea.get("title", "(untitled)"), body_lines)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))

    # --- generate / regenerate ------------------------------------------------------------

    def _start_generation(self) -> None:
        topic = self._topic_entry.get()
        if not topic:
            self._set_status("Enter a topic first.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        self._current_topic = topic
        self._action_bar.set_busy(True)
        self._set_status("Generating 10 Reel ideas...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: ai_services.generate_reel_ideas(llm, topic, count=_IDEA_COUNT),
            self._result_queue, source=self,
        )

    def _on_generate_clicked(self) -> None:
        self._start_generation()

    def _on_regenerate_clicked(self) -> None:
        if self._current_topic:
            self._topic_entry.set(self._current_topic)
        self._start_generation()

    def handle_generation_result(self, result: GenerationTaskResult) -> None:
        """Called by the owning view (ContentStudioView) when a
        GenerationTaskResult whose .source is this panel arrives on the
        shared result queue (routed by identity - see that view's
        _handle_result())."""
        self._action_bar.set_busy(False)
        if result.error:
            self._current_ideas = None
            self._set_status(f"Generation failed: {result.error}", kind="error")
            return
        if result.value is None:
            self._current_ideas = None
            self._set_status(
                "JARVIS couldn't generate valid ideas for that topic - try rephrasing it or generating again.",
                kind="error",
            )
            return
        self._current_ideas = result.value
        self._clear_status()
        self._render_ideas(result.value)

    # --- save / copy ------------------------------------------------------------------------

    def _on_save_clicked(self) -> None:
        if not self._current_ideas:
            self._set_status("Nothing to save yet - generate ideas first.", kind="error")
            return
        db.save_reel_idea_set(self._current_topic, self._current_ideas)
        self._set_status(f"Saved {len(self._current_ideas)} Reel ideas for \"{self._current_topic}\".", kind="muted")

    def _on_copy_clicked(self) -> None:
        if not self._current_ideas:
            self._set_status("Nothing to copy yet - generate ideas first.", kind="error")
            return
        text_parts = []
        for idea in self._current_ideas:
            text_parts.append(
                f"{idea.get('title', '')}\n"
                f"Concept: {idea.get('concept', '')}\n"
                f"Target audience: {idea.get('target_audience', '')}\n"
                f"Format: {idea.get('suggested_format', '')}\n"
                f"Hook: {idea.get('hook', '')}\n"
                f"CTA: {idea.get('cta', '')}\n"
                f"Difficulty: {idea.get('estimated_difficulty', '')}\n"
                f"Why it may work: {idea.get('why_it_may_work', '')}\n"
            )
        copy_to_clipboard(self, "\n---\n".join(text_parts))
        self._set_status("Copied to clipboard.", kind="muted")
