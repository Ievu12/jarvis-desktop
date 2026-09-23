"""GUI replacement for the terminal y/N approval prompt
(jarvis.core.approval.confirm_side_effect/confirm_outside_sandbox), used
only while jarvis.gui.app is running - the terminal REPL (jarvis.cli.main)
is untouched and keeps using its original input()-based prompt (see
jarvis.core.approval's module docstring for how the two coexist).

The approval call always originates on the background thread running
Agent.step() (see jarvis.gui.worker), but a Tkinter dialog may only be
created/shown on the main thread. AskUserConfirmation bridges the two:
the background thread calls .request(description), which schedules the
actual dialog on the main thread via `root.after(0, ...)` and then
blocks (threading.Event.wait()) until the person clicks Allow/Deny in
that dialog - at which point the main thread sets the result and the
background thread's .wait() returns. This mirrors the same
queue/after() bridging pattern jarvis.gui.worker uses for Agent.step()
results, applied to a synchronous yes/no instead of an async result.
"""

from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk


class AskUserConfirmation:
    """Constructed once with the Tk root, then passed as the handler to
    jarvis.core.approval.set_side_effect_handler()/
    set_outside_sandbox_handler(). Callable with a plain description
    string (for set_side_effect_handler) or usable via
    `for_path()` (for set_outside_sandbox_handler, which passes a Path).
    """

    def __init__(self, root: ctk.CTk) -> None:
        self._root = root

    def __call__(self, description: str) -> bool:
        """Matches confirm_side_effect's handler signature: a plain
        description string in, bool approved out."""
        return self._show_and_wait(
            title="JARVIS - patvirtinimas",
            message=f"JARVIS ruošiasi:\n\n{description}\n\nTęsti?",
        )

    def for_path(self, resolved_path) -> bool:
        """Matches confirm_outside_sandbox's handler signature: a Path
        in, bool approved out. A bound method (self.for_path), not
        __call__, since the two handlers have different argument
        meanings (a free-text description vs. a filesystem path) and
        set_outside_sandbox_handler needs a callable taking a Path."""
        return self._show_and_wait(
            title="JARVIS - prieiga už projekto ribų",
            message=(
                f"Prašoma prieiga UŽ JARVIS projekto katalogo ribų:\n\n{resolved_path}\n\n"
                "Tai vienkartinė išimtis - neįsimenama kitiems kartams.\nLeisti šį kartą?"
            ),
        )

    def _show_and_wait(self, *, title: str, message: str) -> bool:
        result_ready = threading.Event()
        result_box: dict[str, bool] = {}

        def _show_dialog() -> None:
            dialog = ctk.CTkToplevel(self._root)
            dialog.title(title)
            dialog.geometry("480x220")
            dialog.resizable(False, False)
            dialog.attributes("-topmost", True)
            dialog.grab_set()  # modal: blocks interaction with the main window

            label = ctk.CTkLabel(dialog, text=message, wraplength=440, justify="left")
            label.pack(padx=20, pady=(20, 10), fill="both", expand=True)

            button_frame = ctk.CTkFrame(dialog, fg_color="transparent")
            button_frame.pack(pady=(0, 20))

            def _respond(approved: bool) -> None:
                result_box["approved"] = approved
                dialog.grab_release()
                dialog.destroy()
                result_ready.set()

            deny_button = ctk.CTkButton(
                button_frame, text="Atmesti", fg_color="#8b2b2b", hover_color="#6e2222",
                command=lambda: _respond(False),
            )
            deny_button.pack(side="left", padx=10)

            allow_button = ctk.CTkButton(
                button_frame, text="Leisti", command=lambda: _respond(True),
            )
            allow_button.pack(side="left", padx=10)

            # Closing the window (the [X] button) is treated as a denial,
            # never as leaving the background thread blocked forever.
            dialog.protocol("WM_DELETE_WINDOW", lambda: _respond(False))

        self._root.after(0, _show_dialog)
        result_ready.wait()
        return result_box.get("approved", False)
