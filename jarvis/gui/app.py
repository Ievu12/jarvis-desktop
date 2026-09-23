"""JARVIS desktop window (customtkinter): a multi-panel dashboard shell
(sidebar navigation + Home/Chat/Tasks/Instagram/Gmail/Stripe/Content/
Analytics/Automations/Settings views - see jarvis.gui.sidebar and
jarvis.gui.views) wrapping the EXISTING agent/voice stack with no
changes to their behavior:

  - jarvis.core.agent.Agent.step() for every text/voice turn - the same
    call jarvis.cli.main._run_agent_turn() and jarvis.voice.voice_loop
    already make. Tool approval still goes through
    jarvis.core.approval.confirm_side_effect()/confirm_outside_sandbox() -
    this window only swaps their PROMPT (a dialog instead of terminal
    input()) via set_side_effect_handler()/set_outside_sandbox_handler(),
    never bypasses them. A write/delete action still always asks first.
  - jarvis.voice.speech_to_text.listen_once() for the microphone button.
  - jarvis.voice.text_to_speech.speak() for spoken replies, unchanged
    (still tries a local Lithuanian voice, then Azure Neural TTS, then
    falls back with a notice - see that module's docstring).

Every blocking call runs on a background thread via jarvis.gui.worker;
this module only ever touches widgets from the main thread, polling a
queue.Queue with `.after()` for results - see jarvis.gui.worker's
docstring for why. jarvis.cli.main (the terminal REPL) is not imported
here and is completely unaffected by this window running.

WHY chat/voice/update state still lives directly on JarvisApp (as
self.transcript, self.send_button, self.mic_button, self.status_label,
self.voice_toggle, self._history, self._submit_user_input(), the
Settings popup, etc.) rather than each becoming its own view class like
Home/Tasks/Instagram/etc: this is the exact, already-tested surface
tests/test_gui_app.py and others assert against directly. The redesign
adds a sidebar + swappable dashboard panels AROUND this unchanged core
- the "Chat" nav item shows the very same widgets this module has
always owned, not a reimplementation. New panels (jarvis.gui.views.*)
read data only through jarvis.gui.dashboard_data or call back into this
class's existing submit_chat_message()/_submit_user_input(), never a
new/duplicated path into the agent, an integration, or a tool.
"""

from __future__ import annotations

import queue
import sys
import threading
import tkinter as tk
from datetime import datetime
from typing import Any

import customtkinter as ctk

from jarvis.__version__ import __version__
from jarvis.config import ANTHROPIC_API_KEY, JARVIS_ROOT
from jarvis.core.agent import Agent
from jarvis.core.approval import set_outside_sandbox_handler, set_side_effect_handler
from jarvis.core.llm import LLMClient
from jarvis.core.project_notes import load_project_notes
from jarvis.core.secrets import mask_secret
from jarvis.gui import theme
from jarvis.gui.confirmation_dialog import AskUserConfirmation
from jarvis.gui.settings_store import UpdateSettings, load_update_settings, save_update_settings
from jarvis.gui.sidebar import Sidebar
from jarvis.gui.updater import UpdateCheckResult, default_download_dir
from jarvis.gui.views.home import HomeView
from jarvis.gui.views.simple_panels import (
    AnalyticsView,
    AutomationsView,
    ContentView,
    GmailView,
    InstagramView,
    StripeView,
    TasksView,
)
from jarvis.gui.worker import (
    AgentStepResult,
    ListenTaskResult,
    SpeakTaskResult,
    UpdateCheckTaskResult,
    UpdateDownloadTaskResult,
    UpdateInstallTaskResult,
    run_agent_step_in_background,
    run_listen_in_background,
    run_speak_in_background,
    run_update_check_in_background,
    run_update_download_in_background,
    run_update_install_in_background,
)
from jarvis.session.store import load_history, save_history

# How often (ms) the main thread polls the background-result queue - see
# jarvis.gui.worker's module docstring for why polling (not a direct
# callback from the worker thread) is required with Tkinter.
_QUEUE_POLL_INTERVAL_MS = 100

_STATUS_READY = "🟢 Ready"
_STATUS_LISTENING = "🎙️ Listening..."
_STATUS_THINKING = "🧠 Thinking..."
_STATUS_SPEAKING = "🔊 Speaking..."

_WINDOW_TITLE = "JARVIS"
_WINDOW_SIZE = "1180x760"
_MIN_WINDOW_SIZE = (900, 600)


def _build_registry():
    # Imported lazily (inside a function, not at module load time) so
    # importing jarvis.gui.app never has the side effect of importing
    # jarvis.cli.main (and everything it in turn imports) unless a
    # JarvisApp is actually constructed - keeps this module's import
    # graph minimal for tests that only check GUI wiring.
    from jarvis.cli.main import build_registry

    return build_registry()


class JarvisApp:
    def __init__(self) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.root = ctk.CTk()
        self.root.title(_WINDOW_TITLE)
        self.root.geometry(_WINDOW_SIZE)
        self.root.minsize(*_MIN_WINDOW_SIZE)
        self.root.configure(fg_color=theme.BG_PRIMARY)

        self.result_queue: "queue.Queue[Any]" = queue.Queue()
        self.voice_enabled = True
        self._history: list[dict[str, Any]] = []
        self._awaiting_reply = False
        self.update_settings: UpdateSettings = load_update_settings()
        self._latest_update_check: UpdateCheckResult | None = None
        self._settings_window: ctk.CTkToplevel | None = None
        self._views: dict[str, Any] = {}  # ctk frame subclasses (Any: no single common CTk frame base type across widget kinds)
        self._current_view_key = "home"

        self._build_widgets()
        self._init_backend()
        self._poll_queue()

        if self.update_settings.check_for_updates:
            self._check_for_updates(silent=True)

    # --- widget construction ---------------------------------------------------

    def _build_widgets(self) -> None:
        # Shell layout: a fixed-width Sidebar on the left, a scrollable
        # content area on the right that swaps between dashboard panels
        # (see _navigate()). The Chat panel's own widgets
        # (self.transcript/self.mic_button/self.send_button/etc.) are
        # built by _build_chat_view() below into their own container
        # frame, which is simply one of the panels this shell shows/
        # hides - their construction and every attribute name is
        # otherwise unchanged from before this redesign.
        self.sidebar = Sidebar(self.root, on_navigate=self._navigate)
        self.sidebar.pack(side="left", fill="y")

        self.content_area = ctk.CTkFrame(self.root, fg_color=theme.BG_PRIMARY, corner_radius=0)
        self.content_area.pack(side="left", fill="both", expand=True)

        self._build_chat_view()
        self._build_dashboard_views()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._navigate("home")

    def _build_chat_view(self) -> None:
        """Builds the Chat panel - the exact widget set/behavior this
        window has always had (transcript, mic button, text entry, send
        button, voice toggle), now packed into its own container frame
        (self._chat_container) instead of directly into self.root, so
        the sidebar can show/hide it as one panel among several. No
        widget's construction, attribute name, or event wiring changed."""
        self._chat_container = ctk.CTkFrame(self.content_area, fg_color="transparent")

        header = ctk.CTkFrame(self._chat_container, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 4))

        title_label = ctk.CTkLabel(
            header, text="JARVIS", font=ctk.CTkFont(family=theme.FONT_FAMILY, size=24, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
        )
        title_label.pack(side="left")

        self.status_label = ctk.CTkLabel(header, text=_STATUS_READY, font=ctk.CTkFont(size=14))
        self.status_label.pack(side="right")

        self.settings_button = ctk.CTkButton(
            header, text="⚙️ Settings", width=90, command=self._open_settings_window,
        )
        self.settings_button.pack(side="right", padx=(0, 12))

        self.transcript = ctk.CTkTextbox(
            self._chat_container, font=ctk.CTkFont(size=13), wrap="word",
            fg_color=theme.BG_CARD, corner_radius=theme.RADIUS_CARD,
        )
        self.transcript.pack(fill="both", expand=True, padx=20, pady=10)
        self.transcript.configure(state="disabled")
        self._append_transcript("jarvis", "How can I help you?")

        input_row = ctk.CTkFrame(self._chat_container, fg_color="transparent")
        input_row.pack(fill="x", padx=20, pady=(0, 8))

        self.mic_button = ctk.CTkButton(
            input_row, text="🎙️", width=48, command=self._on_mic_clicked,
        )
        self.mic_button.pack(side="left", padx=(0, 8))

        self.input_entry = ctk.CTkEntry(input_row, placeholder_text="Type a command...")
        self.input_entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.input_entry.bind("<Return>", lambda _event: self._on_send_clicked())

        self.send_button = ctk.CTkButton(input_row, text="Send", width=72, command=self._on_send_clicked)
        self.send_button.pack(side="left")

        voice_row = ctk.CTkFrame(self._chat_container, fg_color="transparent")
        voice_row.pack(fill="x", padx=20, pady=(0, 16))

        self.voice_toggle = ctk.CTkSwitch(
            voice_row, text="Voice On", command=self._on_voice_toggle,
        )
        self.voice_toggle.select()  # voice replies on by default
        self.voice_toggle.pack(side="left")

        self.api_key_label = ctk.CTkLabel(
            voice_row, text=f"API key: {mask_secret(ANTHROPIC_API_KEY)}",
            font=ctk.CTkFont(size=11), text_color="gray",
        )
        self.api_key_label.pack(side="right")

        self._views["chat"] = self._chat_container

    def _build_dashboard_views(self) -> None:
        """Constructs the remaining dashboard panels (Home + the
        read-only/quick-action views in jarvis.gui.views) up front -
        each is cheap to build (small widget trees, no network I/O at
        construction beyond what jarvis.gui.dashboard_data's already-
        fast local reads do) - and stores them in self._views, keyed by
        the same nav keys jarvis.gui.sidebar.NAV_ITEMS uses, for
        _navigate() to show/hide. Settings has no panel here - it opens
        the existing _open_settings_window() popup instead (see
        _navigate())."""
        self._views["home"] = HomeView(
            self.content_area, submit_chat_message=self.submit_chat_message, navigate=self._navigate,
        )
        self._views["tasks"] = TasksView(self.content_area)
        self._views["instagram"] = InstagramView(
            self.content_area, submit_chat_message=self.submit_chat_message, navigate=self._navigate,
        )
        self._views["gmail"] = GmailView(
            self.content_area, submit_chat_message=self.submit_chat_message, navigate=self._navigate,
        )
        self._views["stripe"] = StripeView(
            self.content_area, submit_chat_message=self.submit_chat_message, navigate=self._navigate,
        )
        self._views["content"] = ContentView(
            self.content_area, submit_chat_message=self.submit_chat_message, navigate=self._navigate,
        )
        self._views["analytics"] = AnalyticsView(
            self.content_area, submit_chat_message=self.submit_chat_message, navigate=self._navigate,
        )
        self._views["automations"] = AutomationsView(self.content_area)

    # --- navigation ----------------------------------------------------------------------

    def _navigate(self, key: str) -> None:
        """Switches the visible dashboard panel. 'settings' is handled
        specially: it opens the existing Settings popup
        (_open_settings_window(), unchanged from before this redesign)
        rather than becoming a panel, and does not change which panel
        is behind it - see that method's own docstring for why."""
        if key == "settings":
            self._open_settings_window()
            self.sidebar.set_active(self._current_view_key)
            return

        view = self._views.get(key)
        if view is None:
            return

        current = self._views.get(self._current_view_key)
        if current is not None:
            current.pack_forget()

        view.pack(fill="both", expand=True)
        self._current_view_key = key
        self.sidebar.set_active(key)

        # Re-fetch this panel's data on every visit (not just at
        # construction) so it never goes stale across a long session -
        # each view's refresh() only re-reads jarvis.gui.dashboard_data
        # (or, for Chat/static panels, does nothing - see each view's
        # own refresh()).
        refresh = getattr(view, "refresh", None)
        if callable(refresh):
            refresh()

    def submit_chat_message(self, text: str) -> None:
        """Public entry point for any view (Home's Quick Actions,
        Instagram/Gmail/Stripe/Content/Analytics action buttons) to send
        a message through the SAME path the Chat panel's Send
        button/microphone already use - this is the one and only way a
        view triggers agent activity; no view calls Agent.step() or a
        connector/tool directly. Thin public wrapper around the
        existing _submit_user_input(), which stays private since it's
        also called internally (e.g. from a transcribed voice result)."""
        self._submit_user_input(text)

    # --- backend initialization --------------------------------------------------

    def _init_backend(self) -> None:
        # Redirect tool-approval prompts to a GUI dialog for the lifetime
        # of this window - restored to the terminal default on close
        # (_on_close), so a later terminal REPL in the same process
        # (there isn't one today, but this keeps the contract honest)
        # would get its own input()-based prompt back.
        confirmation = AskUserConfirmation(self.root)
        set_side_effect_handler(confirmation)
        set_outside_sandbox_handler(confirmation.for_path)

        if not ANTHROPIC_API_KEY:
            self._append_transcript(
                "jarvis",
                "ANTHROPIC_API_KEY nenustatytas - JARVIS negalės atsakyti, kol jis "
                "nebus sukonfigūruotas aplinkos kintamuoju.",
            )
            self.agent = None
            return

        notes_result = load_project_notes(JARVIS_ROOT)
        if notes_result.notice:
            self._append_transcript("jarvis", f"[notice] {notes_result.notice}")

        llm = LLMClient(project_notes=notes_result.content)
        registry = _build_registry()
        self.agent = Agent(llm, registry)

        load_result = load_history()
        if load_result.warning:
            self._append_transcript("jarvis", f"[warning] {load_result.warning}")
        self._history = load_result.history

    # --- transcript helpers -------------------------------------------------------

    def _append_transcript(self, speaker: str, text: str) -> None:
        self.transcript.configure(state="normal")
        timestamp = datetime.now().strftime("%H:%M")
        prefix = "you>" if speaker == "you" else "jarvis>"
        self.transcript.insert("end", f"[{timestamp}] {prefix} {text}\n\n")
        self.transcript.configure(state="disabled")
        self.transcript.see("end")

    # Maps this method's existing emoji-labeled status strings (used
    # verbatim by self.status_label, unchanged) to the sidebar's
    # ONLINE/THINKING/WORKING/WAITING/ERROR vocabulary from the
    # redesign brief - kept as a lookup here rather than changing what
    # _set_status writes to status_label itself, since existing tests
    # may depend on those exact strings.
    _SIDEBAR_STATUS_MAP = {
        _STATUS_READY: "ONLINE",
        _STATUS_LISTENING: "WORKING",
        _STATUS_THINKING: "THINKING",
        _STATUS_SPEAKING: "WORKING",
    }

    def _set_status(self, status: str) -> None:
        self.status_label.configure(text=status)
        if hasattr(self, "sidebar"):
            self.sidebar.set_status(self._SIDEBAR_STATUS_MAP.get(status, "ONLINE"))

    # --- sending a turn (typed or transcribed) -------------------------------------

    def _submit_user_input(self, text: str) -> None:
        if not text.strip() or self._awaiting_reply or self.agent is None:
            return
        self._append_transcript("you", text)
        self._set_status(_STATUS_THINKING)
        self._awaiting_reply = True
        self.send_button.configure(state="disabled")
        run_agent_step_in_background(self.agent, self._history, text, self.result_queue)

    def _on_send_clicked(self) -> None:
        text = self.input_entry.get()
        self.input_entry.delete(0, "end")
        self._submit_user_input(text)

    # --- microphone button -------------------------------------------------------

    def _on_mic_clicked(self) -> None:
        if self._awaiting_reply:
            return
        self._set_status(_STATUS_LISTENING)
        self.mic_button.configure(state="disabled")
        run_listen_in_background(self.result_queue)

    # --- voice on/off toggle -------------------------------------------------------

    def _on_voice_toggle(self) -> None:
        self.voice_enabled = bool(self.voice_toggle.get())
        self.voice_toggle.configure(text="Voice On" if self.voice_enabled else "Voice Off")

    # --- background-result polling -------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                result = self.result_queue.get_nowait()
                self._handle_result(result)
        except queue.Empty:
            pass
        self.root.after(_QUEUE_POLL_INTERVAL_MS, self._poll_queue)

    def _handle_result(self, result: Any) -> None:
        if isinstance(result, AgentStepResult):
            self._awaiting_reply = False
            self.send_button.configure(state="normal")
            if result.error:
                self._append_transcript("jarvis", f"[error] {result.error}")
                self._set_status(_STATUS_READY)
                # Briefly flash the sidebar's ERROR state (additive -
                # does not change status_label's own text, which stays
                # _STATUS_READY exactly as before this redesign) so an
                # error is visible in the persistent status indicator
                # too, not only the transcript.
                if hasattr(self, "sidebar"):
                    self.sidebar.set_status("ERROR")
                return
            reply = result.reply or ""
            self._append_transcript("jarvis", reply)
            save_history(self._history)
            if self.voice_enabled and reply.strip():
                self._set_status(_STATUS_SPEAKING)
                run_speak_in_background(reply, self.result_queue)
            else:
                self._set_status(_STATUS_READY)
        elif isinstance(result, ListenTaskResult):
            self.mic_button.configure(state="normal")
            listen_result = result.listen_result
            if not listen_result.ok:
                self._append_transcript("jarvis", listen_result.error or "Nepavyko atpažinti kalbos.")
                self._set_status(_STATUS_READY)
                return
            self._set_status(_STATUS_READY)
            self._submit_user_input(listen_result.text or "")
        elif isinstance(result, SpeakTaskResult):
            if result.speak_result.notice:
                self._append_transcript("jarvis", f"[voice] {result.speak_result.notice}")
            self._set_status(_STATUS_READY)
        elif isinstance(result, UpdateCheckTaskResult):
            self._on_update_check_done(result)
        elif isinstance(result, UpdateDownloadTaskResult):
            self._on_update_download_done(result)
        elif isinstance(result, UpdateInstallTaskResult):
            self._on_update_install_done(result)

    def _on_update_check_done(self, result: UpdateCheckTaskResult) -> None:
        check_result = result.check_result
        self._latest_update_check = check_result
        if not result.silent and hasattr(self, "check_updates_button") and self.check_updates_button.winfo_exists():
            self.check_updates_button.configure(state="normal")
        self._refresh_settings_status()

        if check_result.error and not result.silent:
            self._append_transcript("jarvis", f"[update] {check_result.error}")
            return
        if not check_result.update_available:
            if not result.silent:
                self._append_transcript("jarvis", "[update] JARVIS jau naujausios versijos.")
            return

        self._append_transcript(
            "jarvis",
            f"[update] Nauja JARVIS versija prieinama: v{check_result.current_version} → "
            f"v{check_result.latest_version}",
        )
        if self.update_settings.download_updates:
            self._download_update(check_result, auto_install=self.update_settings.install_updates)

    def _on_update_download_done(self, result: UpdateDownloadTaskResult) -> None:
        if result.error:
            self._append_transcript("jarvis", f"[update] Atsisiuntimas nepavyko: {result.error}")
            return
        self._append_transcript(
            "jarvis", f"[update] Nauja versija atsisiųsta ir patikrinta (checksum OK)."
        )
        if result.auto_install:
            self._install_update(result.zip_path)
        else:
            self._append_transcript(
                "jarvis",
                "[update] Paspausk \"Update now\" Settings lange, kad įdiegtum atsisiųstą versiją.",
            )

    def _on_update_install_done(self, result: UpdateInstallTaskResult) -> None:
        if hasattr(self, "update_now_button") and self.update_now_button.winfo_exists():
            self.update_now_button.configure(state="normal")
        if result.success:
            self._append_transcript(
                "jarvis", "[update] Nauja versija sėkmingai įdiegta. Paleisk JARVIS iš naujo."
            )
        else:
            self._append_transcript("jarvis", f"[update] Diegimas nepavyko: {result.error}")

    # --- settings window --------------------------------------------------------------

    def _open_settings_window(self) -> None:
        """Opens the Settings popup. The Updates tab's widgets/behavior
        below (self.check_updates_button, self.update_now_button,
        self.settings_status_label, the three checkboxes) are BYTE-FOR-
        BYTE unchanged from before this redesign - only now placed
        inside a CTkTabview alongside informational-only tabs for the
        remaining sections the redesign brief asks for (General,
        Appearance, Notifications, Integrations, Automations, AI,
        Security). Those extra tabs show live, read-only status via
        jarvis.gui.dashboard_data where relevant (Integrations,
        Automations) - never new configurable state, since there is no
        existing settings model for those tabs to persist against yet.
        A remained popup (not a sidebar panel) specifically so the
        existing tests that open/inspect it via
        self._settings_window/self._open_settings_window() keep working
        unmodified."""
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.focus()
            return

        window = ctk.CTkToplevel(self.root)
        self._settings_window = window
        window.title("JARVIS Settings")
        window.geometry("560x480")
        window.resizable(False, False)
        window.attributes("-topmost", True)
        window.configure(fg_color=theme.BG_PRIMARY)

        tabs = ctk.CTkTabview(
            window, fg_color=theme.BG_SURFACE, segmented_button_selected_color=theme.ACCENT_PRIMARY,
            segmented_button_selected_hover_color=theme.ACCENT_PRIMARY_HOVER,
        )
        tabs.pack(fill="both", expand=True, padx=16, pady=16)

        for tab_name in ("Updates", "General", "Appearance", "Notifications", "Integrations", "Automations", "AI", "Security"):
            tabs.add(tab_name)

        self._build_updates_tab(tabs.tab("Updates"))
        self._build_placeholder_tab(tabs.tab("General"), "General settings will appear here in a future update.")
        self._build_placeholder_tab(tabs.tab("Appearance"), "Appearance settings will appear here in a future update.")
        self._build_placeholder_tab(tabs.tab("Notifications"), "Notification settings will appear here in a future update.")
        self._build_integrations_tab(tabs.tab("Integrations"))
        self._build_automations_tab(tabs.tab("Automations"))
        self._build_placeholder_tab(tabs.tab("AI"), "AI/model settings will appear here in a future update.")
        self._build_placeholder_tab(tabs.tab("Security"), "Security settings will appear here in a future update.")

    def _build_updates_tab(self, tab) -> None:
        ctk.CTkLabel(
            tab, text="Updates", font=ctk.CTkFont(size=16, weight="bold"),
        ).pack(anchor="w", padx=20, pady=(20, 8))

        check_var = tk.BooleanVar(value=self.update_settings.check_for_updates)
        download_var = tk.BooleanVar(value=self.update_settings.download_updates)
        install_var = tk.BooleanVar(value=self.update_settings.install_updates)

        def _persist() -> None:
            self.update_settings = UpdateSettings(
                check_for_updates=check_var.get(),
                download_updates=download_var.get(),
                install_updates=install_var.get(),
            )
            save_update_settings(self.update_settings)

        ctk.CTkCheckBox(
            tab, text="Automatically check for updates", variable=check_var, command=_persist,
        ).pack(anchor="w", padx=24, pady=4)
        ctk.CTkCheckBox(
            tab, text="Automatically download updates", variable=download_var, command=_persist,
        ).pack(anchor="w", padx=24, pady=4)
        ctk.CTkCheckBox(
            tab, text="Automatically install updates", variable=install_var, command=_persist,
        ).pack(anchor="w", padx=24, pady=4)

        ctk.CTkLabel(
            tab, text=f"Current version: JARVIS v{__version__}", font=ctk.CTkFont(size=12),
        ).pack(anchor="w", padx=20, pady=(16, 4))

        self.settings_status_label = ctk.CTkLabel(tab, text="", font=ctk.CTkFont(size=12), text_color="gray")
        self.settings_status_label.pack(anchor="w", padx=20, pady=(0, 8))

        button_row = ctk.CTkFrame(tab, fg_color="transparent")
        button_row.pack(anchor="w", padx=16, pady=8)

        self.check_updates_button = ctk.CTkButton(
            button_row, text="Check for updates", command=lambda: self._check_for_updates(silent=False),
        )
        self.check_updates_button.pack(side="left", padx=4)

        self.update_now_button = ctk.CTkButton(
            button_row, text="Update now", command=self._on_update_now_clicked, state="disabled",
        )
        self.update_now_button.pack(side="left", padx=4)

        self._refresh_settings_status()

    def _build_placeholder_tab(self, tab, message: str) -> None:
        ctk.CTkLabel(
            tab, text=message, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
            text_color=theme.TEXT_MUTED, wraplength=480, justify="left",
        ).pack(anchor="w", padx=20, pady=20)

    def _build_integrations_tab(self, tab) -> None:
        from jarvis.gui import dashboard_data

        for status in dashboard_data.get_integration_statuses():
            row = ctk.CTkFrame(tab, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=4)
            ctk.CTkLabel(
                row, text=status.service_name.replace("_", " ").title(),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.TEXT_PRIMARY, width=140, anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=status.status.value.replace("_", " ").upper(),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left")

    def _build_automations_tab(self, tab) -> None:
        from jarvis.gui import dashboard_data

        automations = dashboard_data.get_automations()
        if not automations:
            self._build_placeholder_tab(tab, "No JARVIS automations found on this machine.")
            return
        for auto in automations:
            row = ctk.CTkFrame(tab, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=4)
            ctk.CTkLabel(
                row, text=auto.name, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(anchor="w")
            ctk.CTkLabel(
                row, text=f"Next run: {auto.next_run or '—'}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")

    def _refresh_settings_status(self) -> None:
        if not hasattr(self, "settings_status_label") or not self.settings_status_label.winfo_exists():
            return
        result = self._latest_update_check
        if result is None:
            self.settings_status_label.configure(text="")
        elif result.error:
            self.settings_status_label.configure(text=result.error)
        elif result.update_available:
            self.settings_status_label.configure(
                text=f"Update available: v{result.current_version} → v{result.latest_version}"
            )
            if hasattr(self, "update_now_button") and self.update_now_button.winfo_exists():
                self.update_now_button.configure(state="normal")
        else:
            self.settings_status_label.configure(text="JARVIS is up to date.")

    # --- update checking/downloading/installing -----------------------------------------
    #
    # All three steps (check/download/install) run on background threads
    # via jarvis.gui.worker and report back through self.result_queue,
    # handled in _handle_result() below alongside AgentStepResult/
    # ListenTaskResult/SpeakTaskResult - the same polling pattern, not a
    # separate mechanism. install_update() only ever runs when
    # update_settings.install_updates is explicitly True OR the person
    # clicked "Update now" themselves (_on_update_now_clicked) - never
    # silently as a side effect of the startup auto-check alone.

    def _check_for_updates(self, *, silent: bool) -> None:
        if not silent and hasattr(self, "check_updates_button"):
            self.check_updates_button.configure(state="disabled")
        run_update_check_in_background(self.result_queue, silent=silent)

    def _download_update(self, result: UpdateCheckResult, *, auto_install: bool) -> None:
        download_dir = default_download_dir(JARVIS_ROOT)
        run_update_download_in_background(
            result, download_dir, self.result_queue, auto_install=auto_install
        )

    def _on_update_now_clicked(self) -> None:
        if self._latest_update_check and self._latest_update_check.update_available:
            if hasattr(self, "update_now_button"):
                self.update_now_button.configure(state="disabled")
            # Explicit person-initiated click always installs once
            # downloaded, regardless of the "Automatically install"
            # setting - that setting only governs the SILENT/automatic
            # path (_handle_result's UpdateDownloadTaskResult branch).
            self._download_update(self._latest_update_check, auto_install=True)

    def _install_update(self, zip_path) -> None:
        run_update_install_in_background(zip_path, JARVIS_ROOT, self.result_queue)

    # --- shutdown -------------------------------------------------------------------

    def _on_close(self) -> None:
        set_side_effect_handler(None)
        set_outside_sandbox_handler(None)
        save_history(self._history)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    # '--verify-startup' is used only by jarvis.gui.updater
    # .verify_executable_starts() right after installing a new version:
    # constructs the window (proving imports/init work - the same code
    # path a normal launch takes) and exits 0 immediately instead of
    # entering mainloop(), so the updater's post-install check finishes
    # in well under a second rather than waiting for a person to close
    # a window it never needed to show. Any exception during __init__
    # here propagates as a non-zero exit, exactly what the updater
    # checks for to decide whether to roll back.
    if len(sys.argv) > 1 and sys.argv[1] == "--verify-startup":
        app = JarvisApp()
        app.root.withdraw()
        app.root.destroy()
        sys.exit(0)

    # Diagnostic-only: prints where this build resolves JARVIS_ROOT to
    # and exits, without ever constructing a window - used to confirm
    # (for a PyInstaller-packaged JARVIS.exe specifically) that the
    # sandbox/user-data root resolves to the executable's own directory,
    # not some internal PyInstaller temp/_internal path. Not part of
    # the documented CLI surface; exists for build verification only.
    if len(sys.argv) > 1 and sys.argv[1] == "--print-jarvis-root":
        from jarvis.config import JARVIS_DATA_DIR

        print(f"JARVIS_ROOT={JARVIS_ROOT}")
        print(f"JARVIS_DATA_DIR={JARVIS_DATA_DIR}")
        sys.exit(0)

    app = JarvisApp()
    app.run()


if __name__ == "__main__":
    main()
