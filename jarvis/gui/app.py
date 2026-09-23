"""JARVIS desktop window (customtkinter). Wraps the EXISTING agent/voice
stack with no changes to their behavior:

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
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from typing import Any

import customtkinter as ctk

from jarvis.config import ANTHROPIC_API_KEY, JARVIS_ROOT
from jarvis.core.agent import Agent
from jarvis.core.approval import set_outside_sandbox_handler, set_side_effect_handler
from jarvis.core.llm import LLMClient
from jarvis.core.project_notes import load_project_notes
from jarvis.core.secrets import mask_secret
from jarvis.gui.confirmation_dialog import AskUserConfirmation
from jarvis.gui.worker import (
    AgentStepResult,
    ListenTaskResult,
    SpeakTaskResult,
    run_agent_step_in_background,
    run_listen_in_background,
    run_speak_in_background,
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
_WINDOW_SIZE = "820x640"


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

        self.result_queue: "queue.Queue[Any]" = queue.Queue()
        self.voice_enabled = True
        self._history: list[dict[str, Any]] = []
        self._awaiting_reply = False

        self._build_widgets()
        self._init_backend()
        self._poll_queue()

    # --- widget construction ---------------------------------------------------

    def _build_widgets(self) -> None:
        header = ctk.CTkFrame(self.root, fg_color="transparent")
        header.pack(fill="x", padx=20, pady=(16, 4))

        title_label = ctk.CTkLabel(header, text="JARVIS", font=ctk.CTkFont(size=24, weight="bold"))
        title_label.pack(side="left")

        self.status_label = ctk.CTkLabel(header, text=_STATUS_READY, font=ctk.CTkFont(size=14))
        self.status_label.pack(side="right")

        self.transcript = ctk.CTkTextbox(self.root, font=ctk.CTkFont(size=13), wrap="word")
        self.transcript.pack(fill="both", expand=True, padx=20, pady=10)
        self.transcript.configure(state="disabled")
        self._append_transcript("jarvis", "How can I help you?")

        input_row = ctk.CTkFrame(self.root, fg_color="transparent")
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

        voice_row = ctk.CTkFrame(self.root, fg_color="transparent")
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

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

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

    def _set_status(self, status: str) -> None:
        self.status_label.configure(text=status)

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

    # --- shutdown -------------------------------------------------------------------

    def _on_close(self) -> None:
        set_side_effect_handler(None)
        set_outside_sandbox_handler(None)
        save_history(self._history)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def main() -> None:
    app = JarvisApp()
    app.run()


if __name__ == "__main__":
    main()
