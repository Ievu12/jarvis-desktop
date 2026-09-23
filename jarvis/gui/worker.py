"""Background-thread execution for the GUI: Agent.step() (a real
Anthropic API call plus any tool execution) and the voice module's
listen_once()/speak() all block for a noticeable time and must never run
on Tkinter's main/UI thread, or the whole window freezes until they
return. Every long-running call from jarvis.gui.app goes through one of
the functions here, each spawning a daemon thread and reporting its
outcome back to the UI thread via a thread-safe queue.Queue - customtkinter
(like all Tkinter) widgets may only be touched from the main thread, so
jarvis.gui.app polls this queue with `.after()` rather than updating
widgets directly from a background thread.

This module contains no JARVIS logic of its own - it only calls
jarvis.core.agent.Agent.step(), jarvis.voice.speech_to_text.listen_once(),
and jarvis.voice.text_to_speech.speak(), exactly as jarvis.voice.voice_loop
and jarvis.cli.main already do for the terminal paths. Nothing here
changes what any tool/connector (Instagram included) is allowed to do.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, TypeAlias

from jarvis.core.agent import Agent, StepInterrupted
from jarvis.voice.speech_to_text import ListenResult, listen_once
from jarvis.voice.text_to_speech import SpeakResult, speak


@dataclass
class AgentStepResult:
    """Outcome of a background Agent.step() call, posted onto the GUI's
    result queue. Exactly one of `reply`/`error` is set."""

    reply: str | None
    error: str | None


@dataclass
class ListenTaskResult:
    """Outcome of a background listen_once() call."""

    listen_result: ListenResult


@dataclass
class SpeakTaskResult:
    """Outcome of a background speak() call."""

    speak_result: SpeakResult


@dataclass
class UpdateCheckTaskResult:
    """Outcome of a background jarvis.gui.updater.check_for_update()
    call, posted for jarvis.gui.app to update its Settings window and
    optionally trigger a download."""

    check_result: Any  # jarvis.gui.updater.UpdateCheckResult
    silent: bool


@dataclass
class UpdateDownloadTaskResult:
    """Outcome of a background jarvis.gui.updater.download_update()
    call. Exactly one of `zip_path`/`error` is set."""

    zip_path: Any  # pathlib.Path | None
    error: str | None
    auto_install: bool


@dataclass
class UpdateInstallTaskResult:
    """Outcome of a background jarvis.gui.updater.install_update() (plus
    verify_executable_starts()/rollback_update() as needed) call."""

    success: bool
    error: str | None


ResultQueue: TypeAlias = "queue.Queue[Any]"


def run_agent_step_in_background(
    agent: Agent,
    history: list[dict[str, Any]],
    user_input: str,
    result_queue: ResultQueue,
) -> None:
    """Starts a daemon thread that calls agent.step(history, user_input)
    and posts an AgentStepResult onto `result_queue` when done. Mutates
    `history` in place exactly as Agent.step() always does - the caller
    (jarvis.gui.app) must not touch `history` from the UI thread while
    this is in flight, the same constraint jarvis.voice.voice_loop's
    single-threaded loop satisfies implicitly by never overlapping calls.
    """

    def _worker() -> None:
        try:
            reply = agent.step(history, user_input)
        except StepInterrupted as e:
            result_queue.put(AgentStepResult(reply=None, error=str(e) or "Veiksmas atšauktas."))
        except Exception as e:
            result_queue.put(AgentStepResult(reply=None, error=str(e)))
        else:
            result_queue.put(AgentStepResult(reply=reply, error=None))

    threading.Thread(target=_worker, daemon=True).start()


def run_listen_in_background(result_queue: ResultQueue) -> None:
    """Starts a daemon thread that calls listen_once() (blocks on the
    microphone until speech is detected or it times out - see that
    function's own docstring) and posts a ListenTaskResult when done."""

    def _worker() -> None:
        result_queue.put(ListenTaskResult(listen_result=listen_once()))

    threading.Thread(target=_worker, daemon=True).start()


def run_speak_in_background(text: str, result_queue: ResultQueue) -> None:
    """Starts a daemon thread that calls speak(text) and posts a
    SpeakTaskResult when done."""

    def _worker() -> None:
        result_queue.put(SpeakTaskResult(speak_result=speak(text)))

    threading.Thread(target=_worker, daemon=True).start()


def run_update_check_in_background(result_queue: ResultQueue, *, silent: bool) -> None:
    """Starts a daemon thread that calls
    jarvis.gui.updater.check_for_update() and posts an
    UpdateCheckTaskResult when done. `silent` is carried through
    unchanged so the UI thread knows whether this check was the
    startup auto-check (no "up to date" toast needed) or a person
    clicking "Check for updates" (which should say something either
    way)."""
    from jarvis.gui.updater import check_for_update

    def _worker() -> None:
        result_queue.put(UpdateCheckTaskResult(check_result=check_for_update(), silent=silent))

    threading.Thread(target=_worker, daemon=True).start()


def run_update_download_in_background(
    check_result: Any, destination_dir, result_queue: ResultQueue, *, auto_install: bool
) -> None:
    """Starts a daemon thread that calls
    jarvis.gui.updater.download_update() (which itself verifies the
    SHA256 checksum before returning - see that function's docstring)
    and posts an UpdateDownloadTaskResult when done."""
    from jarvis.gui.updater import UpdateError, download_update

    def _worker() -> None:
        try:
            zip_path = download_update(check_result, destination_dir=destination_dir)
        except UpdateError as e:
            result_queue.put(
                UpdateDownloadTaskResult(zip_path=None, error=str(e), auto_install=auto_install)
            )
        else:
            result_queue.put(
                UpdateDownloadTaskResult(zip_path=zip_path, error=None, auto_install=auto_install)
            )

    threading.Thread(target=_worker, daemon=True).start()


def run_update_install_in_background(zip_path, install_dir, result_queue: ResultQueue) -> None:
    """Starts a daemon thread that installs an already-checksum-verified
    update (jarvis.gui.updater.install_update()), verifies the newly
    installed executable can start (verify_executable_starts()), and
    rolls back (rollback_update()) if it can't - posting an
    UpdateInstallTaskResult with the final outcome."""
    from jarvis.gui.updater import UpdateError, install_update, rollback_update, verify_executable_starts

    def _worker() -> None:
        try:
            exe_path = install_update(zip_path, install_dir=install_dir)
        except UpdateError as e:
            result_queue.put(UpdateInstallTaskResult(success=False, error=str(e)))
            return

        if verify_executable_starts(exe_path):
            result_queue.put(UpdateInstallTaskResult(success=True, error=None))
            return

        rollback_update(install_dir=install_dir)
        result_queue.put(
            UpdateInstallTaskResult(
                success=False,
                error="Naujos versijos paleidimas nepavyko - grąžinta ankstesnė versija.",
            )
        )

    threading.Thread(target=_worker, daemon=True).start()


def run_callable_in_background(
    func: Callable[[], Any], result_queue: ResultQueue, *, wrap: Callable[[Any], Any]
) -> None:
    """A generic escape hatch for any other blocking call the GUI needs
    to run off the main thread (e.g. a confirmation dialog's own
    supporting lookup) - wraps `func`'s return value with `wrap` before
    posting it, so the caller controls the posted object's shape without
    this module needing a bespoke dataclass per use site. Exceptions are
    not caught here - callers needing failure handling should catch
    inside `func` itself, keeping this helper's contract simple."""

    def _worker() -> None:
        result_queue.put(wrap(func()))

    threading.Thread(target=_worker, daemon=True).start()
