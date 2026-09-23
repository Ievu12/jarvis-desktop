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
