"""Home dashboard view: greeting, Quick Actions, and compact summaries
of Tasks/Activity/Integrations - the landing panel when JARVIS opens.
All data comes from jarvis.gui.dashboard_data (read-only, already
covered by its own tests); Quick Actions call back into JarvisApp's
EXISTING chat-submission path (`submit_chat_message`, the same
_submit_user_input() the Chat view's send button and mic button already
use) - no new action-execution logic is added here, a Quick Action is
just a pre-written chat message.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import dashboard_data, theme
from jarvis.gui.widgets import Card, QuickActionButton, SectionHeader, StatusPill, empty_state_label
from jarvis.integrations.manager import IntegrationStatus

QUICK_ACTIONS: tuple[tuple[str, str], ...] = (
    ("🎬  Create Instagram Reel", "JARVIS, duok Reel idėją."),
    ("📖  Create Story Ideas", "JARVIS, paruošk Story."),
    ("📷  Check Instagram", "Patikrink mano Instagram paskyros būseną ir naujausius rezultatus."),
    ("✉️  Check Gmail", "Patikrink naujausius Gmail laiškus."),
    ("💳  Check Stripe", "Patikrink Stripe balansą ir paskutinius mokėjimus."),
    ("🗓️  Create today's plan", "JARVIS, ką šiandien turėčiau padaryti?"),
    ("✅  Show today's tasks", "Parodyk dabartinį užduočių sąrašą."),
)

_INTEGRATION_STATUS_COLOR = {
    IntegrationStatus.CONNECTED: theme.SUCCESS,
    IntegrationStatus.NOT_CONFIGURED: theme.TEXT_MUTED,
    IntegrationStatus.DISCONNECTED: theme.STATUS_WAITING,
    IntegrationStatus.ERROR: theme.DANGER,
}


class HomeView(ctk.CTkScrollableFrame):
    def __init__(self, master, *, submit_chat_message: Callable[[str], None], navigate: Callable[[str], None]) -> None:
        super().__init__(master, fg_color="transparent")
        self._submit_chat_message = submit_chat_message
        self._navigate = navigate

        self._build_greeting()
        self._build_quick_actions()

        columns = ctk.CTkFrame(self, fg_color="transparent")
        columns.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))
        columns.grid_columnconfigure(0, weight=1, uniform="col")
        columns.grid_columnconfigure(1, weight=1, uniform="col")

        left = ctk.CTkFrame(columns, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, theme.SPACE_SM))
        right = ctk.CTkFrame(columns, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(theme.SPACE_SM, 0))

        self._build_tasks_summary(left)
        self._build_integrations_summary(left)
        self._build_activity_summary(right)

        self.refresh()

    # --- greeting -----------------------------------------------------------------------

    def _build_greeting(self) -> None:
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_MD))

        self._greeting_label = ctk.CTkLabel(
            header, text=dashboard_data.time_of_day_greeting(),
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_HERO, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        )
        self._greeting_label.pack(anchor="w")

        ctk.CTkLabel(
            header, text="What can I help you with today?",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SUBTITLE),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    # --- quick actions -------------------------------------------------------------------

    def _build_quick_actions(self) -> None:
        SectionHeader(self, "Quick Actions").pack(
            anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_SM, theme.SPACE_SM)
        )

        grid = ctk.CTkFrame(self, fg_color="transparent")
        grid.pack(fill="x", padx=theme.SPACE_LG, pady=(0, theme.SPACE_MD))
        for i in range(3):
            grid.grid_columnconfigure(i, weight=1, uniform="qa")

        for index, (label, message) in enumerate(QUICK_ACTIONS):
            row, col = divmod(index, 3)
            button = QuickActionButton(
                grid, label, command=lambda m=message: self._run_quick_action(m),
            )
            button.grid(row=row, column=col, sticky="ew", padx=theme.SPACE_XS, pady=theme.SPACE_XS)

    def _run_quick_action(self, message: str) -> None:
        self._navigate("chat")
        self._submit_chat_message(message)

    # --- tasks summary -------------------------------------------------------------------

    def _build_tasks_summary(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x", pady=(0, theme.SPACE_MD))
        SectionHeader(card, "Today's Tasks").pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM))
        self._tasks_container = ctk.CTkFrame(card, fg_color="transparent")
        self._tasks_container.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

    def _refresh_tasks_summary(self) -> None:
        for widget in self._tasks_container.winfo_children():
            widget.destroy()

        tasks = dashboard_data.get_tasks()
        if not tasks:
            empty_state_label(self._tasks_container, "No tasks yet.").pack(anchor="w")
            return

        for task in tasks[:5]:
            row = ctk.CTkFrame(self._tasks_container, fg_color="transparent")
            row.pack(fill="x", pady=2)
            status_color = theme.SUCCESS if task.done else theme.TEXT_MUTED
            StatusPill(row, "DONE" if task.done else "OPEN", status_color).pack(side="left")
            ctk.CTkLabel(
                row, text=task.text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left", padx=(theme.SPACE_SM, 0), fill="x", expand=True)

    # --- integrations summary --------------------------------------------------------------

    def _build_integrations_summary(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="x")
        SectionHeader(card, "Integrations").pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM))
        self._integrations_container = ctk.CTkFrame(card, fg_color="transparent")
        self._integrations_container.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

    def _refresh_integrations_summary(self) -> None:
        for widget in self._integrations_container.winfo_children():
            widget.destroy()

        statuses = dashboard_data.get_integration_statuses()
        if not statuses:
            empty_state_label(self._integrations_container, "No integrations registered.").pack(anchor="w")
            return

        for status in statuses:
            row = ctk.CTkFrame(self._integrations_container, fg_color="transparent")
            row.pack(fill="x", pady=2)
            color = _INTEGRATION_STATUS_COLOR.get(status.status, theme.TEXT_MUTED)
            StatusPill(row, status.status.value.replace("_", " ").upper(), color).pack(side="left")
            ctk.CTkLabel(
                row, text=status.service_name.replace("_", " ").title(),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left", padx=(theme.SPACE_SM, 0))

    # --- activity summary ------------------------------------------------------------------

    def _build_activity_summary(self, parent) -> None:
        card = Card(parent)
        card.pack(fill="both", expand=True)
        SectionHeader(card, "Recent Activity").pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM))
        self._activity_container = ctk.CTkFrame(card, fg_color="transparent")
        self._activity_container.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

    def _refresh_activity_summary(self) -> None:
        for widget in self._activity_container.winfo_children():
            widget.destroy()

        entries = dashboard_data.get_recent_activity(limit=6)
        if not entries:
            empty_state_label(self._activity_container, "No activity recorded yet.").pack(anchor="w")
            return

        for entry in entries:
            row = ctk.CTkFrame(self._activity_container, fg_color="transparent")
            row.pack(fill="x", pady=2)
            if entry.ok is True:
                color = theme.SUCCESS
            elif entry.ok is False:
                color = theme.DANGER
            else:
                color = theme.TEXT_MUTED
            ctk.CTkLabel(row, text="●", text_color=color, font=ctk.CTkFont(size=10), width=14).pack(side="left")
            ctk.CTkLabel(
                row, text=entry.summary, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left", padx=(theme.SPACE_XS, 0), fill="x", expand=True)

    # --- public refresh -------------------------------------------------------------------

    def refresh(self) -> None:
        """Re-fetches every panel's data - called on view construction
        and whenever the Home tab is navigated back to (see
        jarvis.gui.app), so data doesn't go stale across a long-running
        session without requiring a manual reload."""
        self._greeting_label.configure(text=dashboard_data.time_of_day_greeting())
        self._refresh_tasks_summary()
        self._refresh_integrations_summary()
        self._refresh_activity_summary()
