"""Tests for jarvis.gui.worker: background-thread wrappers around
Agent.step()/listen_once()/speak() and the update check/download/install
functions, each posting a result dataclass onto a queue.Queue. Agent,
listen_once, speak, and the updater functions are all mocked - no real
Anthropic API call, no real microphone/speaker, no real network access.
Confirms: each run_*_in_background() function returns immediately
(doesn't block the calling thread) and the expected result eventually
appears on the queue, an exception from Agent.step() is caught and
reported as an error (never raised into the background thread
uncaught), and StepInterrupted is handled distinctly from a plain
exception."""

from __future__ import annotations

import queue
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

from jarvis.core.agent import StepInterrupted
from jarvis.gui import worker
from jarvis.voice.speech_to_text import ListenResult
from jarvis.voice.text_to_speech import SpeakResult

_POLL_TIMEOUT_SECONDS = 2.0


def _wait_for_result(q: "queue.Queue"):
    deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            return q.get(timeout=0.05)
        except queue.Empty:
            continue
    raise AssertionError("No result posted to queue within timeout")


# --- run_agent_step_in_background() ---------------------------------------------


def test_run_agent_step_in_background_posts_successful_reply():
    agent = MagicMock()
    agent.step.return_value = "the reply"
    result_queue: "queue.Queue" = queue.Queue()
    history: list[dict] = []

    worker.run_agent_step_in_background(agent, history, "hello", result_queue)
    result = _wait_for_result(result_queue)

    assert isinstance(result, worker.AgentStepResult)
    assert result.reply == "the reply"
    assert result.error is None


def test_run_agent_step_in_background_does_not_block_caller():
    agent = MagicMock()
    agent.step.side_effect = lambda *a, **k: time.sleep(0.3) or "done"
    result_queue: "queue.Queue" = queue.Queue()

    started_at = time.monotonic()
    worker.run_agent_step_in_background(agent, [], "hello", result_queue)
    elapsed = time.monotonic() - started_at

    assert elapsed < 0.1  # returned immediately, didn't wait for the 0.3s sleep


def test_run_agent_step_in_background_catches_step_interrupted():
    agent = MagicMock()
    agent.step.side_effect = StepInterrupted("cancelled mid-turn")
    result_queue: "queue.Queue" = queue.Queue()

    worker.run_agent_step_in_background(agent, [], "hello", result_queue)
    result = _wait_for_result(result_queue)

    assert isinstance(result, worker.AgentStepResult)
    assert result.reply is None
    assert result.error is not None


def test_run_agent_step_in_background_catches_arbitrary_exception():
    agent = MagicMock()
    agent.step.side_effect = RuntimeError("network error")
    result_queue: "queue.Queue" = queue.Queue()

    worker.run_agent_step_in_background(agent, [], "hello", result_queue)
    result = _wait_for_result(result_queue)

    assert result.error == "network error"


# --- run_listen_in_background() --------------------------------------------------


def test_run_listen_in_background_posts_listen_result():
    result_queue: "queue.Queue" = queue.Queue()
    with patch("jarvis.gui.worker.listen_once", return_value=ListenResult.success("labas")):
        worker.run_listen_in_background(result_queue)
    result = _wait_for_result(result_queue)
    assert isinstance(result, worker.ListenTaskResult)
    assert result.listen_result.text == "labas"


# --- run_speak_in_background() ---------------------------------------------------


def test_run_speak_in_background_posts_speak_result():
    result_queue: "queue.Queue" = queue.Queue()
    with patch("jarvis.gui.worker.speak", return_value=SpeakResult(ok=True, notice=None)):
        worker.run_speak_in_background("hello", result_queue)
    result = _wait_for_result(result_queue)
    assert isinstance(result, worker.SpeakTaskResult)
    assert result.speak_result.ok is True


# --- run_update_check_in_background() --------------------------------------------


def test_run_update_check_in_background_posts_result_with_silent_flag():
    result_queue: "queue.Queue" = queue.Queue()
    fake_result = MagicMock(update_available=True)
    with patch("jarvis.gui.updater.check_for_update", return_value=fake_result):
        worker.run_update_check_in_background(result_queue, silent=True)
    result = _wait_for_result(result_queue)
    assert isinstance(result, worker.UpdateCheckTaskResult)
    assert result.silent is True
    assert result.check_result is fake_result


# --- run_update_download_in_background() -----------------------------------------


def test_run_update_download_in_background_posts_success(tmp_path):
    result_queue: "queue.Queue" = queue.Queue()
    fake_zip = tmp_path / "update.zip"
    with patch("jarvis.gui.updater.download_update", return_value=fake_zip):
        worker.run_update_download_in_background(
            MagicMock(), tmp_path, result_queue, auto_install=True
        )
    result = _wait_for_result(result_queue)
    assert isinstance(result, worker.UpdateDownloadTaskResult)
    assert result.zip_path == fake_zip
    assert result.error is None
    assert result.auto_install is True


def test_run_update_download_in_background_posts_update_error_message(tmp_path):
    from jarvis.gui.updater import UpdateError

    result_queue: "queue.Queue" = queue.Queue()
    with patch("jarvis.gui.updater.download_update", side_effect=UpdateError("checksum mismatch")):
        worker.run_update_download_in_background(
            MagicMock(), tmp_path, result_queue, auto_install=False
        )
    result = _wait_for_result(result_queue)
    assert result.zip_path is None
    assert result.error == "checksum mismatch"


# --- run_update_install_in_background() -------------------------------------------


def test_run_update_install_in_background_posts_success_when_verified(tmp_path):
    result_queue: "queue.Queue" = queue.Queue()
    exe_path = tmp_path / "JARVIS.exe"
    with patch("jarvis.gui.updater.install_update", return_value=exe_path):
        with patch("jarvis.gui.updater.verify_executable_starts", return_value=True):
            worker.run_update_install_in_background(tmp_path / "u.zip", tmp_path, result_queue)
    result = _wait_for_result(result_queue)
    assert isinstance(result, worker.UpdateInstallTaskResult)
    assert result.success is True


def test_run_update_install_in_background_rolls_back_when_verification_fails(tmp_path):
    result_queue: "queue.Queue" = queue.Queue()
    exe_path = tmp_path / "JARVIS.exe"
    with patch("jarvis.gui.updater.install_update", return_value=exe_path):
        with patch("jarvis.gui.updater.verify_executable_starts", return_value=False):
            with patch("jarvis.gui.updater.rollback_update") as mock_rollback:
                worker.run_update_install_in_background(tmp_path / "u.zip", tmp_path, result_queue)
    result = _wait_for_result(result_queue)
    assert result.success is False
    mock_rollback.assert_called_once_with(install_dir=tmp_path)


def test_run_update_install_in_background_posts_error_on_install_failure(tmp_path):
    from jarvis.gui.updater import UpdateError

    result_queue: "queue.Queue" = queue.Queue()
    with patch("jarvis.gui.updater.install_update", side_effect=UpdateError("disk full")):
        worker.run_update_install_in_background(tmp_path / "u.zip", tmp_path, result_queue)
    result = _wait_for_result(result_queue)
    assert result.success is False
    assert result.error == "disk full"
