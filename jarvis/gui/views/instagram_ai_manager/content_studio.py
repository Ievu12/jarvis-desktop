"""Content Studio: the tabbed container for the 7 Content Studio tools
(Reel Ideas, Hook Generator, Caption Generator, CTA Generator, Hashtag
Assistant, Story Builder, Weekly Content Plan) - one CTkTabview, each
tab holding one tool panel from this package.

Owns its OWN result queue (separate from JarvisApp.result_queue, which
carries chat/voice/update results) and polls it via `.after()` exactly
like jarvis.gui.app does for its queue (see that module's docstring for
why polling, not a direct callback, is required with Tkinter background
threads) - kept separate so a Content Studio generation in flight can
never be confused with an unrelated chat reply arriving on the shared
queue. Dispatches each GenerationTaskResult to the panel identified by
its own .source field (set by that panel when it started the
background call - see jarvis.gui.worker.run_generation_in_background),
so results route correctly by identity even if more than one panel's
generation is in flight at once - every panel shares this one queue/
poll loop rather than each running its own, which would have no
behavioral benefit (Tkinter's `.after()` polling has no real
per-instance cost at this scale).
"""

from __future__ import annotations

import queue
from typing import Any

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.instagram_ai_manager.caption_generator import CaptionGeneratorPanel
from jarvis.gui.views.instagram_ai_manager.cta_generator import CTAGeneratorPanel
from jarvis.gui.views.instagram_ai_manager.hashtag_assistant import HashtagAssistantPanel
from jarvis.gui.views.instagram_ai_manager.hook_generator import HookGeneratorPanel
from jarvis.gui.views.instagram_ai_manager.reel_ideas import ReelIdeasPanel
from jarvis.gui.views.instagram_ai_manager.story_builder import StoryBuilderPanel
from jarvis.gui.views.instagram_ai_manager.weekly_plan import WeeklyPlanPanel
from jarvis.gui.worker import GenerationTaskResult

_QUEUE_POLL_INTERVAL_MS = 100


class ContentStudioView(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue: "queue.Queue[Any]" = queue.Queue()

        ctk.CTkLabel(
            self, text="Content Studio",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_MD))

        tabs = ctk.CTkTabview(
            self, fg_color=theme.BG_SURFACE, segmented_button_selected_color=theme.ACCENT_PRIMARY,
            segmented_button_selected_hover_color=theme.ACCENT_PRIMARY_HOVER,
        )
        tabs.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        tab_names = (
            "Reel Ideas", "Hooks", "Captions", "CTAs", "Hashtags", "Story Builder", "Weekly Plan",
        )
        for name in tab_names:
            tabs.add(name)

        self.reel_ideas_panel = ReelIdeasPanel(
            tabs.tab("Reel Ideas"), llm=llm, result_queue=self._result_queue,
        )
        self.reel_ideas_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self.hook_generator_panel = HookGeneratorPanel(
            tabs.tab("Hooks"), llm=llm, result_queue=self._result_queue,
        )
        self.hook_generator_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self.caption_generator_panel = CaptionGeneratorPanel(
            tabs.tab("Captions"), llm=llm, result_queue=self._result_queue,
        )
        self.caption_generator_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self.cta_generator_panel = CTAGeneratorPanel(
            tabs.tab("CTAs"), llm=llm, result_queue=self._result_queue,
        )
        self.cta_generator_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self.hashtag_assistant_panel = HashtagAssistantPanel(
            tabs.tab("Hashtags"), llm=llm, result_queue=self._result_queue,
        )
        self.hashtag_assistant_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self.story_builder_panel = StoryBuilderPanel(
            tabs.tab("Story Builder"), llm=llm, result_queue=self._result_queue,
        )
        self.story_builder_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self.weekly_plan_panel = WeeklyPlanPanel(
            tabs.tab("Weekly Plan"), llm=llm, result_queue=self._result_queue,
        )
        self.weekly_plan_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self._poll_queue()

    def _poll_queue(self) -> None:
        try:
            while True:
                result = self._result_queue.get_nowait()
                self._handle_result(result)
        except queue.Empty:
            pass
        self.after(_QUEUE_POLL_INTERVAL_MS, self._poll_queue)

    def _handle_result(self, result: Any) -> None:
        # Routed by identity via GenerationTaskResult.source (set to the
        # requesting panel itself when it called
        # run_generation_in_background() - see each panel's
        # _start_generation()), not by "whichever panel most recently
        # started something" - correct even if two panels' requests are
        # ever in flight at the same time.
        if isinstance(result, GenerationTaskResult) and result.source is not None:
            result.source.handle_generation_result(result)
