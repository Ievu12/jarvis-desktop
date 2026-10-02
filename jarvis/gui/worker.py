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


@dataclass
class GenerationTaskResult:
    """Outcome of a background jarvis.instagram_ai_manager.ai_services
    (or .analytics_services) call, posted via run_generation_in_background()
    below - generic across all ~11 functions in those two modules (Reel
    ideas, hooks, captions, CTAs, hashtags, Story sequences, weekly
    plans, Reel analysis, period comparison, posting-time analysis,
    next-week recommendations) rather than one dataclass per function,
    since every one of them already returns None (or an
    analytics_services dataclass with its own insufficient_data flag)
    to signal "nothing usable came back" - this wrapper only needs to
    additionally distinguish "the call raised" (network/auth failure)
    from "it returned normally" for the UI's loading/error state.

    `source` carries whatever the caller passed to
    run_generation_in_background() as `source` - typically the panel
    instance that started the request - so a dispatcher shared by
    several panels (jarvis.gui.views.instagram_ai_manager.content_studio
    .ContentStudioView) can route each result to the correct panel by
    identity rather than by "whichever panel started something most
    recently", which would misroute results if two panels' requests are
    ever in flight at the same time."""

    value: Any
    error: str | None
    source: Any = None


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


def run_generation_in_background(
    func: Callable[[], Any], result_queue: ResultQueue, *, source: Any = None
) -> None:
    """Starts a daemon thread that calls `func` (any
    jarvis.instagram_ai_manager.ai_services/.analytics_services
    function, already bound to its arguments via a lambda/partial by
    the caller) and posts a GenerationTaskResult. Unlike
    run_callable_in_background(), this DOES catch any exception `func`
    raises - ai_services' generate_*() functions already return None on
    every documented failure and never raise by design, but this is a
    defensive backstop (e.g. a jarvis.instagram_ai_manager.db write
    failing) so a view's Generate/Save button can never leave the UI
    stuck in a loading state over an unexpected error.

    `source` is carried through unchanged onto the posted
    GenerationTaskResult.source - see that field's own docstring for why
    (routing a result back to the correct panel when several panels
    share one result queue)."""

    def _worker() -> None:
        try:
            value = func()
        except Exception as e:
            result_queue.put(GenerationTaskResult(value=None, error=str(e), source=source))
        else:
            result_queue.put(GenerationTaskResult(value=value, error=None, source=source))

    threading.Thread(target=_worker, daemon=True).start()


# --- Cancelable, progress-reporting background work (jarvis.video_editor) -------------------
#
# Additive only - every function/dataclass ABOVE this comment is
# completely untouched by this section (confirmed via `git diff
# jarvis/gui/worker.py` showing only new lines below this point). No
# existing dashboard's own use of run_generation_in_background()/
# run_callable_in_background()/etc. is affected in any way.
#
# This is new infrastructure because none of the functions above can
# express "report intermediate progress" or "let the caller cancel a
# still-running call" - every existing worker function posts exactly
# ONE terminal result and has no way to interrupt `func` once started
# (a plain Python thread cannot be forcibly killed). jarvis.video_editor
# .multisource_export.export_timeline() is the first caller that needs
# both: a real ffmpeg export can run for minutes, reports its own real
# progress via `-progress pipe:1`, and owns a real subprocess that CAN
# be terminated cleanly (unlike the thread calling it) - see that
# function's own docstring for the mechanism. The dataclasses/function
# here are a thin, GUI-thread-safe relay of that mechanism into this
# module's own already-established queue+`.after()` polling pattern,
# generic enough to reuse for any other future cancelable, progress-
# reporting call (not hardcoded to video export specifically).


@dataclass
class ProgressResult:
    """Posted repeatedly onto the result_queue while a cancelable
    background call runs, carrying a REAL, measured progress value
    (e.g. parsed from ffmpeg's own `-progress pipe:1` output) - never a
    fabricated/interpolated estimate. `source` is carried through
    unchanged, same routing convention as GenerationTaskResult.source."""

    percent: float
    source: Any = None


@dataclass
class CancelableTaskResult:
    """The terminal result of a cancelable background call - posted
    exactly once, after `ProgressResult`s (if any). Exactly one of
    `value`/`error` is set unless `cancelled` is True, in which case
    both are None (a cancellation is neither a success nor a reported
    failure - the caller asked for this outcome)."""

    value: Any
    error: str | None
    cancelled: bool
    source: Any = None


def run_cancelable_in_background(
    func: Callable[..., Any], result_queue: ResultQueue, *, cancel_event: threading.Event, source: Any = None,
    **func_kwargs: Any,
) -> threading.Thread:
    """Starts a daemon thread that calls
    `func(progress_callback=<posts ProgressResult>, cancel_event=cancel_event, **func_kwargs)`
    and posts a CancelableTaskResult when done. `func` is expected to
    accept both `progress_callback` and `cancel_event` keyword
    arguments and to own whatever real, interruptible resource (e.g. a
    subprocess.Popen) actually makes cancellation possible - this
    function itself has no way to kill a Python thread; it only relays
    `cancel_event`'s state into `func` and relays `func`'s own progress/
    outcome back onto `result_queue`, exactly as
    jarvis.video_editor.multisource_export.export_timeline()'s own
    contract expects (see that function's docstring).

    Distinguishes "func raised MultiSourceExportError/any other
    exception because cancel_event was set" from "func raised for a
    genuine, unrelated failure" by checking `cancel_event.is_set()`
    AFTER an exception is caught - if it's set, the result is reported
    as `cancelled=True` (not as an error the UI should show as a
    failure), regardless of the exact exception text func happened to
    raise; this keeps this module's own contract independent of any
    specific caller's exception message wording.

    Returns the started Thread (callers don't need it for anything
    today, but it's useful for tests asserting the thread actually
    started/finished, same as jarvis.gui.worker's own other
    run_*_in_background() functions could have returned but didn't
    need to until now)."""

    def _post_progress(percent: float) -> None:
        result_queue.put(ProgressResult(percent=percent, source=source))

    def _worker() -> None:
        try:
            value = func(progress_callback=_post_progress, cancel_event=cancel_event, **func_kwargs)
        except Exception as e:
            if cancel_event.is_set():
                result_queue.put(CancelableTaskResult(value=None, error=None, cancelled=True, source=source))
            else:
                result_queue.put(CancelableTaskResult(value=None, error=str(e), cancelled=False, source=source))
        else:
            result_queue.put(CancelableTaskResult(value=value, error=None, cancelled=False, source=source))

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return thread
