"""Weekly Content Plan tool (Content Studio, tool G): select counts of
Reels/Stories/Carousels, preferred days, niche, and a weekly goal;
generate a Monday-Sunday content plan (each item with format/topic/
hook/CTA/suggested posting time/objective) - with Save Week/Regenerate
Week/Copy Plan actions. "Edit" (from the module's brief) is satisfied by
the person simply re-filling the form and regenerating/re-saving,
rather than an in-place per-item editor - a full structural inline
editor for a 7-day, multi-item plan is a larger UI than this stage
needs; regenerate-with-adjusted-inputs is the same editing capability
every other Content Studio tool already offers.
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

_WEEKDAY_LABELS = {
    "monday": "Monday", "tuesday": "Tuesday", "wednesday": "Wednesday", "thursday": "Thursday",
    "friday": "Friday", "saturday": "Saturday", "sunday": "Sunday",
}


class WeeklyPlanPanel(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]") -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._current_plan: dict[str, list[dict]] | None = None

        SectionHeader(self, "Weekly Content Plan").pack(anchor="w", pady=(0, theme.SPACE_SM))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._reels_entry = LabeledEntry(form, "Number of Reels:", placeholder="e.g. 3")
        self._reels_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._stories_entry = LabeledEntry(form, "Number of Stories:", placeholder="e.g. 5")
        self._stories_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._carousels_entry = LabeledEntry(form, "Number of Carousels:", placeholder="e.g. 1")
        self._carousels_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._days_entry = LabeledEntry(form, "Preferred days (comma-separated, optional):", placeholder="e.g. Monday, Wednesday, Friday")
        self._days_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._niche_entry = LabeledEntry(form, "Niche:", placeholder="e.g. yoga")
        self._niche_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._goal_entry = LabeledEntry(form, "Weekly goal:", placeholder="e.g. grow followers and engagement")
        self._goal_entry.pack(fill="x")

        self._action_bar = GeneratorActionBar(
            self, on_generate=self._on_generate_clicked, on_regenerate=self._on_generate_clicked,
            on_save=self._on_save_clicked, on_copy=self._on_copy_clicked,
            generate_label="Generate Week",
        )
        self._action_bar.pack(anchor="w", pady=(theme.SPACE_MD, theme.SPACE_MD))

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x")

        self._results_container = ctk.CTkScrollableFrame(self, fg_color="transparent", height=450)
        self._results_container.pack(fill="both", expand=True)

        self._set_status("Fill in the form and click Generate Week.", kind="muted")

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _render_plan(self, plan: dict[str, list[dict]]) -> None:
        for widget in self._results_container.winfo_children():
            widget.destroy()

        for day_key, label in _WEEKDAY_LABELS.items():
            items = plan.get(day_key, [])
            card = Card(self._results_container)
            card.pack(fill="x", pady=(0, theme.SPACE_SM))
            ctk.CTkLabel(
                card, text=label.upper(),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
                text_color=theme.ACCENT_PRIMARY, anchor="w",
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))

            if not items:
                ctk.CTkLabel(
                    card, text="Nothing planned.", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                    text_color=theme.TEXT_MUTED, anchor="w",
                ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_SM))
            else:
                for item in items:
                    ctk.CTkLabel(
                        card,
                        text=(
                            f"{item.get('format', '?')} — {item.get('topic', '')}\n"
                            f"Hook: {item.get('hook', '')}  |  CTA: {item.get('cta', '')}\n"
                            f"Time: {item.get('suggested_posting_time', '')}  |  Objective: {item.get('objective', '')}"
                        ),
                        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                        text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=560,
                    ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_SM))
            ctk.CTkFrame(card, fg_color="transparent", height=theme.SPACE_XS).pack()

    def _parse_int(self, entry: LabeledEntry, default: int = 0) -> int:
        raw = entry.get()
        if not raw:
            return default
        try:
            return max(0, int(raw))
        except ValueError:
            return default

    def _on_generate_clicked(self) -> None:
        niche = self._niche_entry.get()
        goal = self._goal_entry.get()
        if not niche or not goal:
            self._set_status("Niche and weekly goal are both required.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        num_reels = self._parse_int(self._reels_entry)
        num_stories = self._parse_int(self._stories_entry)
        num_carousels = self._parse_int(self._carousels_entry)
        preferred_days = [d.strip() for d in self._days_entry.get().split(",") if d.strip()]

        self._action_bar.set_busy(True)
        self._set_status("Generating your weekly plan...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: ai_services.generate_weekly_plan(
                llm, num_reels=num_reels, num_stories=num_stories, num_carousels=num_carousels,
                preferred_days=preferred_days, niche=niche, weekly_goal=goal,
            ),
            self._result_queue, source=self,
        )

    def handle_generation_result(self, result: GenerationTaskResult) -> None:
        self._action_bar.set_busy(False)
        if result.error:
            self._current_plan = None
            self._set_status(f"Generation failed: {result.error}", kind="error")
            return
        if result.value is None:
            self._current_plan = None
            self._set_status("JARVIS couldn't generate a plan for that input - try again.", kind="error")
            return
        self._current_plan = result.value
        self._clear_status()
        self._render_plan(result.value)

    def _on_save_clicked(self) -> None:
        if not self._current_plan:
            self._set_status("Nothing to save yet - generate a plan first.", kind="error")
            return
        import datetime as _dt

        today = _dt.date.today()
        week_start = (today - _dt.timedelta(days=today.weekday())).isoformat()
        db.save_weekly_plan(week_start, self._current_plan)
        self._set_status(f"Saved week starting {week_start}.", kind="muted")

    def _on_copy_clicked(self) -> None:
        if not self._current_plan:
            self._set_status("Nothing to copy yet - generate a plan first.", kind="error")
            return
        parts = []
        for day_key, label in _WEEKDAY_LABELS.items():
            items = self._current_plan.get(day_key, [])
            if not items:
                parts.append(f"{label.upper()}\n(nothing planned)")
                continue
            item_lines = "\n".join(
                f"- {item.get('format', '?')}: {item.get('topic', '')} "
                f"(Hook: {item.get('hook', '')}, CTA: {item.get('cta', '')}, "
                f"Time: {item.get('suggested_posting_time', '')})"
                for item in items
            )
            parts.append(f"{label.upper()}\n{item_lines}")
        copy_to_clipboard(self, "\n\n".join(parts))
        self._set_status("Copied full week to clipboard.", kind="muted")
