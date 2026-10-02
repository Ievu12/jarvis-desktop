"""Caption Generator tool (Content Studio, tool C): generate short/
medium/long Instagram captions from a topic, content description, tone,
and optional hook/target audience/CTA - with Generate/Copy/Save actions
per caption length.
"""

from __future__ import annotations

import queue
from typing import Any

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.instagram_ai_manager.common import (
    GeneratorActionBar,
    LabeledDropdown,
    LabeledEntry,
    copy_to_clipboard,
    status_label,
)
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.instagram_ai_manager import ai_services, db

_TONES = ("friendly", "expert", "inspirational", "educational", "personal", "sales")
_LENGTH_LABELS = {"short_caption": "Short", "medium_caption": "Medium", "long_caption": "Long"}


class CaptionGeneratorPanel(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]") -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._current_caption: dict[str, str] | None = None
        self._current_topic: str = ""
        self._current_tone: str = _TONES[0]

        SectionHeader(self, "Caption Generator").pack(anchor="w", pady=(0, theme.SPACE_SM))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._topic_entry = LabeledEntry(form, "Topic:", placeholder="e.g. skincare")
        self._topic_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._content_entry = LabeledEntry(form, "Reel/content description:", placeholder="What is the content about?")
        self._content_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._hook_entry = LabeledEntry(form, "Hook (optional):", placeholder="If you already have one")
        self._hook_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._audience_entry = LabeledEntry(form, "Target audience (optional):", placeholder="")
        self._audience_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._cta_entry = LabeledEntry(form, "CTA to include (optional):", placeholder="")
        self._cta_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._tone_dropdown = LabeledDropdown(form, "Tone:", _TONES)
        self._tone_dropdown.pack(fill="x")

        self._action_bar = GeneratorActionBar(
            self, on_generate=self._on_generate_clicked, on_regenerate=self._on_generate_clicked,
            on_save=self._on_save_clicked,
        )
        self._action_bar.pack(anchor="w", pady=(theme.SPACE_MD, theme.SPACE_MD))

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x")

        self._results_container = ctk.CTkScrollableFrame(self, fg_color="transparent", height=300)
        self._results_container.pack(fill="both", expand=True)

        self._set_status("Fill in the topic and description, then click Generate.", kind="muted")

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _render_caption(self, caption: dict[str, str]) -> None:
        for widget in self._results_container.winfo_children():
            widget.destroy()

        for key, label in _LENGTH_LABELS.items():
            text = caption.get(key, "")
            card = Card(self._results_container)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))
            header_row = ctk.CTkFrame(card, fg_color="transparent")
            header_row.pack(fill="x", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
            ctk.CTkLabel(
                header_row, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
                text_color=theme.TEXT_PRIMARY,
            ).pack(side="left")
            ctk.CTkButton(
                header_row, text="Copy", width=60, height=24,
                fg_color=theme.BG_CARD_HOVER, hover_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(size=theme.FONT_SIZE_CAPTION),
                command=lambda t=text: self._copy_one(t),
            ).pack(side="right")
            ctk.CTkLabel(
                card, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=540,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

    def _copy_one(self, text: str) -> None:
        copy_to_clipboard(self, text)
        self._set_status("Copied to clipboard.", kind="muted")

    def _on_generate_clicked(self) -> None:
        topic = self._topic_entry.get()
        content_description = self._content_entry.get()
        if not topic or not content_description:
            self._set_status("Topic and content description are both required.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        self._current_topic = topic
        self._current_tone = self._tone_dropdown.get()
        hook = self._hook_entry.get() or None
        audience = self._audience_entry.get() or None
        cta = self._cta_entry.get() or None

        self._action_bar.set_busy(True)
        self._set_status("Generating captions...", kind="loading")
        llm = self._llm
        tone = self._current_tone
        run_generation_in_background(
            lambda: ai_services.generate_caption(
                llm, topic=topic, content_description=content_description, hook=hook,
                tone=tone, target_audience=audience, cta=cta,
            ),
            self._result_queue, source=self,
        )

    def handle_generation_result(self, result: GenerationTaskResult) -> None:
        self._action_bar.set_busy(False)
        if result.error:
            self._current_caption = None
            self._set_status(f"Generation failed: {result.error}", kind="error")
            return
        if result.value is None:
            self._current_caption = None
            self._set_status("JARVIS couldn't generate a caption for that input - try again.", kind="error")
            return
        self._current_caption = result.value
        self._clear_status()
        self._render_caption(result.value)

    def _on_save_clicked(self) -> None:
        if not self._current_caption:
            self._set_status("Nothing to save yet - generate a caption first.", kind="error")
            return
        db.save_caption(self._current_topic, self._current_tone, self._current_caption)
        self._set_status(f"Saved caption for \"{self._current_topic}\".", kind="muted")
