"""Story Builder tool (Content Studio, tool F): enter a topic, goal, and
number of Stories, generate a full Story sequence (each Story with
number/text/visual idea/interactive element/sticker suggestion/CTA) -
with Regenerate/Copy/Save/Create Full Sequence actions per the module's
brief. "Create Full Sequence" is a Save under a clearer label for this
tool specifically (there is no separate publishing step - see this
module's own note on that below).
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
from jarvis.gui.widgets import SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.instagram_ai_manager import ai_services, db

_DEFAULT_STORY_COUNT = "5"


class StoryBuilderPanel(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]") -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._current_sequence: list[dict] | None = None
        self._current_topic: str = ""

        SectionHeader(self, "Story Builder").pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            self, text="Build a complete Instagram Story sequence for a topic and goal.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._topic_entry = LabeledEntry(form, "Topic:", placeholder="e.g. behind the scenes of my morning routine")
        self._topic_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._goal_entry = LabeledEntry(form, "Goal:", placeholder="e.g. build trust and drive DMs")
        self._goal_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._count_entry = LabeledEntry(form, "Number of Stories:", placeholder=_DEFAULT_STORY_COUNT)
        self._count_entry.pack(fill="x")

        self._action_bar = GeneratorActionBar(
            self, on_generate=self._on_generate_clicked, on_regenerate=self._on_generate_clicked,
            on_save=self._on_save_clicked, on_copy=self._on_copy_clicked,
        )
        self._action_bar.pack(anchor="w", pady=(theme.SPACE_MD, theme.SPACE_MD))

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x")

        self._results_container = ctk.CTkScrollableFrame(self, fg_color="transparent", height=400)
        self._results_container.pack(fill="both", expand=True)

        self._set_status("Fill in the topic and goal, then click Generate.", kind="muted")

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _render_sequence(self, sequence: list[dict]) -> None:
        for widget in self._results_container.winfo_children():
            widget.destroy()

        for story in sequence:
            title = f"Story {story.get('story_number', '?')} — {story.get('stage', '')}"
            body_lines = [
                f"Text: {story.get('text', '')}",
                f"Visual idea: {story.get('visual_idea', '')}",
                f"Interactive element: {story.get('interactive_element', '')}",
                f"Sticker suggestion: {story.get('sticker_suggestion', '')}",
                f"CTA: {story.get('cta', '')}",
            ]
            card = ResultCard(self._results_container, title, body_lines)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))

    def _parse_count(self) -> int:
        raw = self._count_entry.get()
        if not raw:
            return int(_DEFAULT_STORY_COUNT)
        try:
            return int(raw)
        except ValueError:
            return int(_DEFAULT_STORY_COUNT)

    def _on_generate_clicked(self) -> None:
        topic = self._topic_entry.get()
        goal = self._goal_entry.get()
        if not topic or not goal:
            self._set_status("Topic and goal are both required.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        self._current_topic = topic
        num_stories = self._parse_count()
        self._action_bar.set_busy(True)
        self._set_status(f"Generating {num_stories} Stories...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: ai_services.generate_story_sequence(llm, topic=topic, goal=goal, num_stories=num_stories),
            self._result_queue, source=self,
        )

    def handle_generation_result(self, result: GenerationTaskResult) -> None:
        self._action_bar.set_busy(False)
        if result.error:
            self._current_sequence = None
            self._set_status(f"Generation failed: {result.error}", kind="error")
            return
        if result.value is None:
            self._current_sequence = None
            self._set_status("JARVIS couldn't generate a Story sequence for that input - try again.", kind="error")
            return
        self._current_sequence = result.value
        self._clear_status()
        self._render_sequence(result.value)

    def _on_save_clicked(self) -> None:
        # This IS "Create Full Sequence" from the module's brief - saved
        # locally (jarvis.instagram_ai_manager.db), never posted/
        # scheduled to Instagram. There is no Instagram Story-publishing
        # capability anywhere in this codebase to call even if this
        # button wanted to (see jarvis.integrations.connectors.instagram
        # .InstagramConnector - read-only, no publish action exists).
        if not self._current_sequence:
            self._set_status("Nothing to save yet - generate a sequence first.", kind="error")
            return
        db.save_story_sequence(self._current_topic, self._current_sequence)
        self._set_status(f"Saved Story sequence for \"{self._current_topic}\".", kind="muted")

    def _on_copy_clicked(self) -> None:
        if not self._current_sequence:
            self._set_status("Nothing to copy yet - generate a sequence first.", kind="error")
            return
        parts = []
        for story in self._current_sequence:
            parts.append(
                f"Story {story.get('story_number', '?')} — {story.get('stage', '')}\n"
                f"Text: {story.get('text', '')}\n"
                f"Visual idea: {story.get('visual_idea', '')}\n"
                f"Interactive element: {story.get('interactive_element', '')}\n"
                f"Sticker suggestion: {story.get('sticker_suggestion', '')}\n"
                f"CTA: {story.get('cta', '')}"
            )
        copy_to_clipboard(self, "\n\n---\n\n".join(parts))
        self._set_status("Copied full sequence to clipboard.", kind="muted")
