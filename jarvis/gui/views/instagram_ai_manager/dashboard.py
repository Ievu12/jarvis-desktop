"""Instagram AI Manager dashboard: the top-level view for the sidebar's
"📱 Instagram AI Manager" nav item, containing the module's two major
sections (per its own brief) - CONTENT STUDIO and ANALYTICS - as tabs.

Both are implemented: Content Studio (jarvis.gui.views
.instagram_ai_manager.content_studio.ContentStudioView, 7 tools) and
Analytics (jarvis.gui.views.instagram_ai_manager.analytics_view
.AnalyticsView - Reel performance analysis, best-performing content,
week-over-week comparison, best posting time, next-week
recommendations), each built on their own already-tested backing
module (jarvis.instagram_ai_manager.ai_services /.analytics_services).
"""

from __future__ import annotations

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.instagram_ai_manager.analytics_view import AnalyticsView
from jarvis.gui.views.instagram_ai_manager.content_studio import ContentStudioView


class InstagramAIManagerView(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None) -> None:
        super().__init__(master, fg_color="transparent")

        ctk.CTkLabel(
            self, text="Instagram AI Manager",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_HERO, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM))

        tabs = ctk.CTkTabview(
            self, fg_color=theme.BG_SURFACE, segmented_button_selected_color=theme.ACCENT_PRIMARY,
            segmented_button_selected_hover_color=theme.ACCENT_PRIMARY_HOVER,
        )
        tabs.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))
        tabs.add("Content Studio")
        tabs.add("Analytics")

        self.content_studio = ContentStudioView(tabs.tab("Content Studio"), llm=llm)
        self.content_studio.pack(fill="both", expand=True)

        self.analytics = AnalyticsView(tabs.tab("Analytics"))
        self.analytics.pack(fill="both", expand=True)

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate() calls
        on every panel it shows - Content Studio holds its own in-flight
        generation state across tab switches (a person navigating away
        mid-generation and back shouldn't lose it) and Analytics only
        re-fetches on an explicit Sync click (see AnalyticsView.refresh()'s
        own docstring for why), so this deliberately does nothing rather
        than resetting anything."""
        pass
