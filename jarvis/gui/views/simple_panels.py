"""Straightforward, mostly-static or single-data-source dashboard
panels: Tasks, Instagram, Gmail, Stripe, Content, Analytics, and
Automations. Grouped in one module (rather than one file each) because
each is short and follows the exact same "Card + refresh() re-reads
jarvis.gui.dashboard_data" shape already established by
jarvis.gui.views.home - splitting them into seven near-identical tiny
files would add navigation overhead without adding clarity. Chat and
Settings are NOT here: they stay directly on JarvisApp (see
jarvis.gui.app's module docstring) since ~25 existing tests already
depend on their exact widget attributes.

None of these views call an integration/tool directly - Instagram/
Gmail/Stripe panels show the SAME jarvis.gui.dashboard_data
.get_integration_statuses() status the Home view's summary uses, plus a
button that runs a natural-language request through the existing chat
path (submit_chat_message -> JarvisApp._submit_user_input() ->
Agent.step()), exactly like a Home Quick Action - never a separate,
duplicated call into InstagramConnector/GmailConnector/StripeConnector.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import dashboard_data, theme
from jarvis.gui.widgets import Card, QuickActionButton, SectionHeader, StatusPill, empty_state_label
from jarvis.integrations.manager import IntegrationStatus

_INTEGRATION_STATUS_COLOR = {
    IntegrationStatus.CONNECTED: theme.SUCCESS,
    IntegrationStatus.NOT_CONFIGURED: theme.TEXT_MUTED,
    IntegrationStatus.DISCONNECTED: theme.STATUS_WAITING,
    IntegrationStatus.ERROR: theme.DANGER,
}


class _RefreshableView(ctk.CTkScrollableFrame):
    """Common scaffold: a title, then a refreshable content area."""

    def __init__(self, master, title: str) -> None:
        super().__init__(master, fg_color="transparent")
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_MD))
        ctk.CTkLabel(
            header, text=title,
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(anchor="w")

        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

    def refresh(self) -> None:
        raise NotImplementedError


# --- Tasks ------------------------------------------------------------------------------


class TasksView(_RefreshableView):
    def __init__(self, master) -> None:
        super().__init__(master, "Tasks")
        self.card = Card(self.body)
        self.card.pack(fill="both", expand=True)
        self.list_container = ctk.CTkFrame(self.card, fg_color="transparent")
        self.list_container.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        self.refresh()

    def refresh(self) -> None:
        for widget in self.list_container.winfo_children():
            widget.destroy()

        tasks = dashboard_data.get_tasks()
        if not tasks:
            empty_state_label(self.list_container, "No tasks yet - ask JARVIS to plan something.").pack(anchor="w")
            return

        for task in tasks:
            row = ctk.CTkFrame(self.list_container, fg_color="transparent")
            row.pack(fill="x", pady=4)
            status_color = theme.SUCCESS if task.done else theme.TEXT_MUTED
            StatusPill(row, "DONE" if task.done else "OPEN", status_color).pack(side="left")
            ctk.CTkLabel(
                row, text=task.text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=520,
            ).pack(side="left", padx=(theme.SPACE_SM, 0), fill="x", expand=True)


# --- Integration panels (Instagram / Gmail / Stripe) -------------------------------------


class _IntegrationView(_RefreshableView):
    """Shared shape for Instagram/Gmail/Stripe: this integration's live
    status card, plus one or more Quick-Action buttons that route a
    natural-language request through the existing chat/agent path."""

    def __init__(self, master, *, title: str, service_name: str, actions: tuple[tuple[str, str], ...],
                 submit_chat_message: Callable[[str], None], navigate: Callable[[str], None]) -> None:
        super().__init__(master, title)
        self._service_name = service_name
        self._submit_chat_message = submit_chat_message
        self._navigate = navigate

        self.status_card = Card(self.body)
        self.status_card.pack(fill="x", pady=(0, theme.SPACE_MD))
        self.status_row = ctk.CTkFrame(self.status_card, fg_color="transparent")
        self.status_row.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        actions_card = Card(self.body)
        actions_card.pack(fill="x")
        SectionHeader(actions_card, "Actions").pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM))
        actions_container = ctk.CTkFrame(actions_card, fg_color="transparent")
        actions_container.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
        for label, message in actions:
            QuickActionButton(
                actions_container, label, command=lambda m=message: self._run_action(m),
            ).pack(fill="x", pady=theme.SPACE_XS)

        self.refresh()

    def _run_action(self, message: str) -> None:
        self._navigate("chat")
        self._submit_chat_message(message)

    def refresh(self) -> None:
        for widget in self.status_row.winfo_children():
            widget.destroy()

        statuses = {s.service_name: s for s in dashboard_data.get_integration_statuses()}
        status = statuses.get(self._service_name)
        if status is None:
            empty_state_label(self.status_row, "Integration not registered.").pack(anchor="w")
            return

        color = _INTEGRATION_STATUS_COLOR.get(status.status, theme.TEXT_MUTED)
        StatusPill(self.status_row, status.status.value.replace("_", " ").upper(), color).pack(side="left")
        ctk.CTkLabel(
            self.status_row, text=status.detail,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=480,
        ).pack(side="left", padx=(theme.SPACE_SM, 0), fill="x", expand=True)


class InstagramView(_IntegrationView):
    def __init__(self, master, *, submit_chat_message: Callable[[str], None], navigate: Callable[[str], None]) -> None:
        super().__init__(
            master, title="Instagram", service_name="instagram",
            actions=(
                ("📊  Check Instagram Insights", "Patikrink mano naujausius Instagram Insights."),
                ("📅  Today's daily report", "Parodyk šiandienos Instagram ataskaitą."),
                ("✨  Content ideas", "JARVIS, paruošk šiandienos contentą."),
            ),
            submit_chat_message=submit_chat_message, navigate=navigate,
        )


class GmailView(_IntegrationView):
    def __init__(self, master, *, submit_chat_message: Callable[[str], None], navigate: Callable[[str], None]) -> None:
        super().__init__(
            master, title="Gmail", service_name="gmail",
            actions=(
                ("📥  Check recent emails", "Patikrink naujausius Gmail laiškus."),
                ("🔴  Check unread emails", "Parodyk neperskaitytus Gmail laiškus."),
            ),
            submit_chat_message=submit_chat_message, navigate=navigate,
        )


class StripeView(_IntegrationView):
    def __init__(self, master, *, submit_chat_message: Callable[[str], None], navigate: Callable[[str], None]) -> None:
        super().__init__(
            master, title="Stripe", service_name="stripe",
            actions=(
                ("💰  Check balance", "Patikrink Stripe balansą."),
                ("🧾  Recent charges", "Parodyk paskutinius Stripe mokėjimus."),
            ),
            submit_chat_message=submit_chat_message, navigate=navigate,
        )


# --- Content -------------------------------------------------------------------------


class ContentView(_RefreshableView):
    def __init__(self, master, *, submit_chat_message: Callable[[str], None], navigate: Callable[[str], None]) -> None:
        super().__init__(master, "Content")
        self._submit_chat_message = submit_chat_message
        self._navigate = navigate

        card = Card(self.body)
        card.pack(fill="x")
        SectionHeader(card, "Today's Content Ideas").pack(
            anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM)
        )
        ctk.CTkLabel(
            card,
            text="JARVIS can suggest Reel, Story, and Carousel ideas - hooks, captions,\nand calls to action - grounded in your real Instagram performance when available.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
            text_color=theme.TEXT_SECONDARY, justify="left", anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

        actions_container = ctk.CTkFrame(card, fg_color="transparent")
        actions_container.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
        for label, message in (
            ("🗓️  Create today's full content plan", "JARVIS, paruošk šiandienos contentą."),
            ("🎬  Reel idea", "JARVIS, duok Reel idėją."),
            ("📖  Story ideas", "JARVIS, paruošk Story."),
            ("🖼️  Carousel idea", "Pasiūlyk man Carousel idėją šiandienai."),
        ):
            QuickActionButton(
                actions_container, label, command=lambda m=message: self._run(m),
            ).pack(fill="x", pady=theme.SPACE_XS)

    def _run(self, message: str) -> None:
        self._navigate("chat")
        self._submit_chat_message(message)

    def refresh(self) -> None:
        pass  # static panel - no live data source of its own to re-fetch


# --- Analytics -----------------------------------------------------------------------


class AnalyticsView(_RefreshableView):
    def __init__(self, master, *, submit_chat_message: Callable[[str], None], navigate: Callable[[str], None]) -> None:
        super().__init__(master, "Analytics")
        self._submit_chat_message = submit_chat_message
        self._navigate = navigate

        card = Card(self.body)
        card.pack(fill="x")
        SectionHeader(card, "Instagram Performance").pack(
            anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM)
        )
        ctk.CTkLabel(
            card,
            text="Ask JARVIS for reach, engagement, and historical comparisons - it reads\nreal Instagram Insights data through the existing Instagram integration.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
            text_color=theme.TEXT_SECONDARY, justify="left", anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

        actions_container = ctk.CTkFrame(card, fg_color="transparent")
        actions_container.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
        for label, message in (
            ("📈  Analyze recent Insights", "Išanalizuok mano naujausius Instagram Insights."),
            ("📊  Compare to last week", "Palygink mano šios savaitės rezultatus su praėjusia savaite."),
            ("🏆  Best performing content", "Kuris mano turinys pastaruoju metu veikė geriausiai?"),
        ):
            QuickActionButton(
                actions_container, label, command=lambda m=message: self._run(m),
            ).pack(fill="x", pady=theme.SPACE_XS)

    def _run(self, message: str) -> None:
        self._navigate("chat")
        self._submit_chat_message(message)

    def refresh(self) -> None:
        pass  # static panel - no live data source of its own to re-fetch


# --- Automations -----------------------------------------------------------------------


class AutomationsView(_RefreshableView):
    def __init__(self, master) -> None:
        super().__init__(master, "Automations")
        self.card = Card(self.body)
        self.card.pack(fill="both", expand=True)
        SectionHeader(self.card, "Scheduled Automations").pack(
            anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM)
        )
        ctk.CTkLabel(
            self.card, text="Managed via Windows Task Scheduler - shown here read-only.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_SM))
        self.list_container = ctk.CTkFrame(self.card, fg_color="transparent")
        self.list_container.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
        self.refresh()

    def refresh(self) -> None:
        for widget in self.list_container.winfo_children():
            widget.destroy()

        automations = dashboard_data.get_automations()
        if not automations:
            empty_state_label(
                self.list_container, "No JARVIS automations found on this machine.",
            ).pack(anchor="w")
            return

        for auto in automations:
            row = Card(self.list_container, fg_color=theme.BG_SURFACE)
            row.pack(fill="x", pady=4)
            inner = ctk.CTkFrame(row, fg_color="transparent")
            inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_SM)

            top = ctk.CTkFrame(inner, fg_color="transparent")
            top.pack(fill="x")
            ctk.CTkLabel(
                top, text=auto.name, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(side="left")
            status_color = theme.SUCCESS if auto.enabled else theme.TEXT_MUTED
            StatusPill(top, "ENABLED" if auto.enabled else "DISABLED", status_color).pack(side="right")

            detail_text = f"Next run: {auto.next_run or '—'}    •    Last run: {auto.last_run or 'never'}"
            ctk.CTkLabel(
                inner, text=detail_text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
