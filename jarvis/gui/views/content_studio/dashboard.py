"""AI Content Studio dashboard (module brief, section 9): the tabbed
container for this feature's own tools - New Content, My Projects, and
(future stages) Reels/Stories/Posts/PDFs - one CTkTabview, each tab
holding one panel from this package.

Stage 1 of this feature's staged rollout (see jarvis.content_studio's
own __init__.py docstring) covers ONLY "New Content" (topic -> content
plan) and "My Projects" (the Content Library listing) - the module
brief's own additional "Reels / Stories / Posts / PDFs" tabs are Stage
2-4 work, not built yet; this view intentionally does not add empty
placeholder tabs for them (an empty tab with nothing in it would be
more confusing than simply not showing it yet).

Owns its OWN result queue (separate from JarvisApp.result_queue) and
polls it via `.after()`, dispatching each GenerationTaskResult to the
panel identified by its own `.source` field - the EXACT same shared-
queue/poll-loop/routing-by-source pattern
jarvis.gui.views.instagram_ai_manager.content_studio.ContentStudioView
already established (see that module's own docstring for the full
rationale: kept separate so a generation in flight in one tab can never
be confused with an unrelated result arriving on a different queue, and
one shared queue/poll loop per tabbed container has no behavioral
downside at this scale)."""

from __future__ import annotations

import queue
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.content_studio.my_projects import MyProjectsPanel
from jarvis.gui.views.content_studio.new_content import NewContentPanel
from jarvis.gui.worker import GenerationTaskResult

_QUEUE_POLL_INTERVAL_MS = 100


class ContentStudioView(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None, navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._navigate = navigate
        self._result_queue: "queue.Queue[Any]" = queue.Queue()

        ctk.CTkLabel(
            self, text="AI Content Studio",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_MD))

        tabs = ctk.CTkTabview(
            self, fg_color=theme.BG_SURFACE, segmented_button_selected_color=theme.ACCENT_PRIMARY,
            segmented_button_selected_hover_color=theme.ACCENT_PRIMARY_HOVER,
        )
        tabs.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        tab_names = ("New Content", "My Projects")
        for name in tab_names:
            tabs.add(name)

        self.my_projects_panel = MyProjectsPanel(tabs.tab("My Projects"))
        self.my_projects_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self.new_content_panel = NewContentPanel(
            tabs.tab("New Content"), llm=llm, result_queue=self._result_queue,
            on_plan_created=self.my_projects_panel.refresh, navigate=navigate,
        )
        self.new_content_panel.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        self._poll_queue()

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate()
        calls on every panel it shows."""
        self.my_projects_panel.refresh()

    def _poll_queue(self) -> None:
        try:
            while True:
                result = self._result_queue.get_nowait()
                self._handle_result(result)
        except queue.Empty:
            pass
        self.after(_QUEUE_POLL_INTERVAL_MS, self._poll_queue)

    def _handle_result(self, result: Any) -> None:
        if not isinstance(result, GenerationTaskResult):
            return
        source = result.source
        # A content-plan result's source is the panel itself; a
        # per-content-type creation result's source is a
        # (content_type, panel) tuple - see
        # jarvis.gui.views.content_studio.new_content.NewContentPanel
        # .handle_result()'s own docstring for why both route here.
        if isinstance(source, tuple) and len(source) == 2 and source[1] is self.new_content_panel:
            self.new_content_panel.handle_result(result)
        elif source is self.new_content_panel:
            self.new_content_panel.handle_result(result)
