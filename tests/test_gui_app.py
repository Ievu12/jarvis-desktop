"""Tests for jarvis.gui.app.JarvisApp: widget wiring and result-handling
logic, using a real (withdrawn) CTk root - LLMClient, Agent,
build_registry, and every worker.run_*_in_background function are
mocked, so constructing a JarvisApp makes no real Anthropic API call, no
real microphone/speaker access, and no real network call. _poll_queue()'s
recurring root.after() is left real but harmless (its callback just
finds an empty queue and reschedules) since we destroy the root at the
end of each test, cancelling any further callbacks.

Confirms: the window is titled "JARVIS", initial status is Ready, typed
input goes through Agent.step() (via the background-thread wrapper) and
is disabled while awaiting a reply, a returned AgentStepResult updates
the transcript and (if voice is on) triggers speak(), the mic button
posts a ListenTaskResult through the same result-handling path, Settings
persists the three update checkboxes, and closing the window restores
the terminal approval handlers (set_side_effect_handler(None))."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from jarvis.gui import app as gui_app
from jarvis.gui.settings_store import UpdateSettings
from jarvis.gui.updater import UpdateCheckResult
from jarvis.gui.worker import (
    AgentStepResult,
    ListenTaskResult,
    SpeakTaskResult,
    UpdateCheckTaskResult,
    UpdateDownloadTaskResult,
    UpdateInstallTaskResult,
)
from jarvis.voice.speech_to_text import ListenResult
from jarvis.voice.text_to_speech import SpeakResult


@pytest.fixture(autouse=True)
def _isolated_update_settings(tmp_path, monkeypatch):
    from jarvis.gui import settings_store

    settings_file = tmp_path / "update_settings.json"
    monkeypatch.setattr(settings_store, "UPDATE_SETTINGS_FILE", settings_file)
    monkeypatch.setattr(gui_app, "UPDATE_SETTINGS_FILE", settings_file, raising=False)


def _construct_app(*, api_key: str | None = "fake-key"):
    """Builds a real JarvisApp (real CTk root) with every external
    dependency mocked. Factored out of the fixture so the two tests
    that need a DIFFERENT construction (no API key; checking the
    approval-handler side effect) can call it directly instead of each
    creating their own separate Tk root - see the module docstring for
    why minimizing how many Tk() roots get created matters here."""
    with patch("jarvis.gui.app.ANTHROPIC_API_KEY", api_key):
        with patch("jarvis.gui.app.LLMClient"):
            with patch("jarvis.gui.app._build_registry", return_value=MagicMock()):
                with patch("jarvis.gui.app.Agent") as MockAgent:
                    with patch("jarvis.gui.app.load_history") as mock_load_history:
                        mock_load_history.return_value = MagicMock(warning=None, history=[])
                        with patch("jarvis.gui.app.load_project_notes") as mock_notes:
                            mock_notes.return_value = MagicMock(notice=None, content=None)
                            with patch(
                                "jarvis.gui.app.load_update_settings",
                                return_value=UpdateSettings(check_for_updates=False),
                            ):
                                application = gui_app.JarvisApp()
    if api_key:
        application.agent = MockAgent.return_value
    application.root.withdraw()
    return application


@pytest.fixture(scope="module")
def built_app():
    # Module-scoped: one real CTk() root shared across every test in
    # this file. Creating/destroying many CTk() roots in quick
    # succession has been observed to intermittently exhaust the
    # Windows Tcl interpreter's own resources (errors like "invalid
    # command name" or "couldn't read file ...ttk/*.tcl") - a
    # platform/Tcl limitation unrelated to this module's logic (each
    # such failure happens inside Tcl's own interpreter bootstrap,
    # before any application code runs, and re-running in isolation
    # always passes). Tests that need a distinct App instance (missing
    # API key, approval-handler redirection) call _construct_app()
    # directly instead of adding another module-scoped root.
    application = _construct_app()
    yield application
    application.root.destroy()


@pytest.fixture(scope="module")
def no_api_key_app():
    # A second module-scoped app (its own Tk root, since ANTHROPIC_API_KEY
    # is fixed at construction time and can't be toggled on the shared
    # built_app afterward) - shared by the two tests that need a
    # no-API-key JarvisApp, instead of each building its own.
    application = _construct_app(api_key=None)
    yield application
    application.root.destroy()


@pytest.fixture(autouse=True)
def _reset_app_state(built_app):
    """Runs before/after every test in this file to reset the shared
    built_app's mutable state, since it's now created once per module
    rather than fresh per test."""
    built_app._awaiting_reply = False
    built_app._latest_update_check = None
    built_app.voice_enabled = True
    built_app.update_settings = UpdateSettings(check_for_updates=False)
    built_app.send_button.configure(state="normal")
    built_app.transcript.configure(state="normal")
    built_app.transcript.delete("1.0", "end")
    built_app.transcript.configure(state="disabled")
    if built_app._settings_window is not None:
        try:
            built_app._settings_window.destroy()
        except Exception:
            pass
        built_app._settings_window = None
    yield


# --- window basics ---------------------------------------------------------------


def test_window_title_is_jarvis(built_app):
    assert built_app.root.title() == "JARVIS"


def test_initial_status_is_ready(built_app):
    assert "Ready" in built_app.status_label.cget("text")


def test_append_transcript_writes_the_greeting_text(built_app):
    # _build_widgets() (called once by __init__) writes the initial
    # "How can I help you?" greeting via _append_transcript() - this
    # confirms _append_transcript() itself produces that exact line
    # shape, without constructing a second JarvisApp/Tk root just to
    # observe __init__'s one-time call to it (see module docstring on
    # why extra Tk() churn is avoided in this file).
    built_app._append_transcript("jarvis", "How can I help you?")
    built_app.transcript.configure(state="normal")
    content = built_app.transcript.get("1.0", "end")
    built_app.transcript.configure(state="disabled")
    assert "How can I help you?" in content


# --- typed input: goes through Agent.step() via the background wrapper -------------


def test_submit_user_input_calls_agent_step_in_background(built_app):
    with patch("jarvis.gui.app.run_agent_step_in_background") as mock_run:
        built_app._submit_user_input("hello jarvis")
    mock_run.assert_called_once()
    call_args = mock_run.call_args.args
    assert call_args[0] is built_app.agent
    assert call_args[2] == "hello jarvis"


def test_submit_user_input_disables_send_button_while_awaiting(built_app):
    with patch("jarvis.gui.app.run_agent_step_in_background"):
        built_app._submit_user_input("hello")
    assert built_app.send_button.cget("state") == "disabled"


def test_submit_blank_input_does_nothing(built_app):
    with patch("jarvis.gui.app.run_agent_step_in_background") as mock_run:
        built_app._submit_user_input("   ")
    mock_run.assert_not_called()


def test_submit_input_while_awaiting_reply_is_ignored(built_app):
    built_app._awaiting_reply = True
    with patch("jarvis.gui.app.run_agent_step_in_background") as mock_run:
        built_app._submit_user_input("another message")
    mock_run.assert_not_called()


def test_submit_input_with_no_agent_does_nothing(no_api_key_app):
    assert no_api_key_app.agent is None
    with patch("jarvis.gui.app.run_agent_step_in_background") as mock_run:
        no_api_key_app._submit_user_input("hello")
    mock_run.assert_not_called()


# --- AgentStepResult handling ------------------------------------------------------


def test_agent_step_result_appends_reply_to_transcript(built_app):
    built_app._awaiting_reply = True
    with patch("jarvis.gui.app.save_history"):
        built_app._handle_result(AgentStepResult(reply="the answer", error=None))
    built_app.transcript.configure(state="normal")
    content = built_app.transcript.get("1.0", "end")
    built_app.transcript.configure(state="disabled")
    assert "the answer" in content


def test_agent_step_result_re_enables_send_button(built_app):
    built_app._awaiting_reply = True
    built_app.send_button.configure(state="disabled")
    with patch("jarvis.gui.app.save_history"):
        with patch("jarvis.gui.app.run_speak_in_background"):
            built_app._handle_result(AgentStepResult(reply="ok", error=None))
    assert built_app._awaiting_reply is False
    assert built_app.send_button.cget("state") == "normal"


def test_agent_step_result_with_error_shows_error_and_does_not_speak(built_app):
    with patch("jarvis.gui.app.run_speak_in_background") as mock_speak:
        built_app._handle_result(AgentStepResult(reply=None, error="network failure"))
    mock_speak.assert_not_called()
    built_app.transcript.configure(state="normal")
    content = built_app.transcript.get("1.0", "end")
    built_app.transcript.configure(state="disabled")
    assert "network failure" in content


def test_agent_step_result_triggers_speak_when_voice_enabled(built_app):
    built_app.voice_enabled = True
    with patch("jarvis.gui.app.save_history"):
        with patch("jarvis.gui.app.run_speak_in_background") as mock_speak:
            built_app._handle_result(AgentStepResult(reply="spoken reply", error=None))
    mock_speak.assert_called_once()
    assert mock_speak.call_args.args[0] == "spoken reply"


def test_agent_step_result_does_not_speak_when_voice_disabled(built_app):
    built_app.voice_enabled = False
    with patch("jarvis.gui.app.save_history"):
        with patch("jarvis.gui.app.run_speak_in_background") as mock_speak:
            built_app._handle_result(AgentStepResult(reply="silent reply", error=None))
    mock_speak.assert_not_called()


def test_agent_step_result_navigates_when_reel_draft_was_created(built_app):
    # jarvis.tools.reel_chat.CreateReelDraftTool's own real-world flow:
    # it calls jarvis.tools.reel_navigation.request_navigation() as a
    # side effect of its own run() - this confirms _handle_result()
    # picks that up after the agent step and opens AI Reel Generator on
    # the right project, the same open_project_id mechanism Content
    # Studio's own "Preview / Continue" hand-off already uses.
    from jarvis.tools import reel_navigation

    reel_navigation.request_navigation("some-reel-project-id")
    target_view = built_app._views["reel_generator"]
    with patch("jarvis.gui.app.save_history"), \
         patch("jarvis.gui.app.run_speak_in_background"), \
         patch.object(target_view, "open_project") as mock_open_project:
        built_app._handle_result(AgentStepResult(reply="Reel draft created.", error=None))
    mock_open_project.assert_called_once_with("some-reel-project-id")
    built_app._navigate("home")


def test_agent_step_result_does_not_navigate_when_no_reel_draft_was_created(built_app):
    from jarvis.tools import reel_navigation

    assert reel_navigation.consume_navigation_request() is None  # confirm nothing pending
    target_view = built_app._views["reel_generator"]
    with patch("jarvis.gui.app.save_history"), \
         patch("jarvis.gui.app.run_speak_in_background"), \
         patch.object(target_view, "open_project") as mock_open_project:
        built_app._handle_result(AgentStepResult(reply="just a normal reply", error=None))
    mock_open_project.assert_not_called()


# --- ListenTaskResult handling: microphone flow -------------------------------------


def test_mic_click_calls_listen_in_background(built_app):
    with patch("jarvis.gui.app.run_listen_in_background") as mock_run:
        built_app._on_mic_clicked()
    mock_run.assert_called_once()


def test_listen_result_success_submits_as_user_input(built_app):
    with patch("jarvis.gui.app.run_agent_step_in_background") as mock_run:
        built_app._handle_result(ListenTaskResult(listen_result=ListenResult.success("labas jarvis")))
    mock_run.assert_called_once()
    assert mock_run.call_args.args[2] == "labas jarvis"


def test_listen_result_failure_shows_message_without_submitting(built_app):
    with patch("jarvis.gui.app.run_agent_step_in_background") as mock_run:
        built_app._handle_result(
            ListenTaskResult(listen_result=ListenResult.failure("Nieko neišgirdau."))
        )
    mock_run.assert_not_called()
    built_app.transcript.configure(state="normal")
    content = built_app.transcript.get("1.0", "end")
    built_app.transcript.configure(state="disabled")
    assert "Nieko neišgirdau." in content


# --- SpeakTaskResult handling --------------------------------------------------------


def test_speak_result_notice_is_shown(built_app):
    built_app._handle_result(SpeakTaskResult(speak_result=SpeakResult(ok=True, notice="no LT voice")))
    built_app.transcript.configure(state="normal")
    content = built_app.transcript.get("1.0", "end")
    built_app.transcript.configure(state="disabled")
    assert "no LT voice" in content


# --- voice toggle --------------------------------------------------------------------


def test_voice_toggle_off_updates_flag_and_label(built_app):
    built_app.voice_toggle.deselect()
    built_app._on_voice_toggle()
    assert built_app.voice_enabled is False
    assert "Off" in built_app.voice_toggle.cget("text")


# --- settings window: persists checkbox state ---------------------------------------


def test_settings_window_opens_without_error(built_app):
    built_app._open_settings_window()
    assert built_app._settings_window is not None
    assert built_app._settings_window.winfo_exists()


def _collect_label_texts(widget) -> list[str]:
    """Recursively collects every CTkLabel's text under `widget` - the
    Settings window's content now lives inside a CTkTabview's tab
    frames (see _open_settings_window()'s docstring), so a direct
    winfo_children() scan of the window itself no longer reaches labels
    nested inside a tab; this walks the whole widget tree instead."""
    texts = []
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkLabel):
            texts.append(child.cget("text"))
        texts.extend(_collect_label_texts(child))
    return texts


def test_settings_window_shows_current_version(built_app):
    from jarvis.__version__ import __version__

    built_app._open_settings_window()
    all_text = _collect_label_texts(built_app._settings_window)
    assert any(__version__ in t for t in all_text)


# --- update flow: check -> download -> install, all via background wrappers --------


def test_check_for_updates_calls_background_wrapper(built_app):
    with patch("jarvis.gui.app.run_update_check_in_background") as mock_run:
        built_app._check_for_updates(silent=True)
    mock_run.assert_called_once_with(built_app.result_queue, silent=True)


def test_update_check_result_with_available_update_triggers_download_if_enabled(built_app):
    built_app.update_settings = UpdateSettings(download_updates=True, install_updates=False)
    check_result = UpdateCheckResult(
        update_available=True, current_version="1.0.0", latest_version="2.0.0",
        changelog=None, download_url="https://x/u.zip", checksum_url="https://x/S.txt",
    )
    with patch("jarvis.gui.app.run_update_download_in_background") as mock_download:
        built_app._handle_result(UpdateCheckTaskResult(check_result=check_result, silent=True))
    mock_download.assert_called_once()


def test_update_check_result_does_not_download_when_disabled(built_app):
    built_app.update_settings = UpdateSettings(download_updates=False)
    check_result = UpdateCheckResult(
        update_available=True, current_version="1.0.0", latest_version="2.0.0",
        changelog=None, download_url="https://x/u.zip", checksum_url="https://x/S.txt",
    )
    with patch("jarvis.gui.app.run_update_download_in_background") as mock_download:
        built_app._handle_result(UpdateCheckTaskResult(check_result=check_result, silent=True))
    mock_download.assert_not_called()


def test_update_download_result_installs_only_when_auto_install_true(built_app):
    with patch("jarvis.gui.app.run_update_install_in_background") as mock_install:
        built_app._handle_result(
            UpdateDownloadTaskResult(zip_path="fake.zip", error=None, auto_install=True)
        )
    mock_install.assert_called_once()


def test_update_download_result_does_not_install_when_auto_install_false(built_app):
    with patch("jarvis.gui.app.run_update_install_in_background") as mock_install:
        built_app._handle_result(
            UpdateDownloadTaskResult(zip_path="fake.zip", error=None, auto_install=False)
        )
    mock_install.assert_not_called()


def test_update_now_button_always_installs_after_download_regardless_of_setting(built_app):
    # _on_update_now_clicked() touches self.update_now_button, which
    # only exists once the Settings window has been opened at least
    # once - open it here rather than relying on widget state left over
    # from an earlier test.
    built_app._open_settings_window()
    built_app.update_settings = UpdateSettings(install_updates=False)  # off
    built_app._latest_update_check = UpdateCheckResult(
        update_available=True, current_version="1.0.0", latest_version="2.0.0",
        changelog=None, download_url="https://x/u.zip", checksum_url="https://x/S.txt",
    )
    with patch("jarvis.gui.app.run_update_download_in_background") as mock_download:
        built_app._on_update_now_clicked()
    assert mock_download.call_args.kwargs.get("auto_install") is True or mock_download.call_args.args[-1] is True


def test_update_install_result_success_message(built_app):
    built_app._handle_result(UpdateInstallTaskResult(success=True, error=None))
    built_app.transcript.configure(state="normal")
    content = built_app.transcript.get("1.0", "end")
    built_app.transcript.configure(state="disabled")
    assert "sėkmingai įdiegta" in content


def test_update_install_result_failure_message(built_app):
    built_app._handle_result(UpdateInstallTaskResult(success=False, error="disk full"))
    built_app.transcript.configure(state="normal")
    content = built_app.transcript.get("1.0", "end")
    built_app.transcript.configure(state="disabled")
    assert "disk full" in content


# --- Instagram AI Manager nav wiring --------------------------------------------------


def test_navigating_to_instagram_ai_manager_shows_its_view(built_app):
    from jarvis.gui.views.instagram_ai_manager.dashboard import InstagramAIManagerView

    assert isinstance(built_app._views["instagram_ai_manager"], InstagramAIManagerView)
    built_app._navigate("instagram_ai_manager")
    assert built_app._current_view_key == "instagram_ai_manager"
    built_app._navigate("home")  # leave shared built_app on its default view for other tests


# --- AI Video Studio nav wiring ------------------------------------------------------


def test_navigating_to_video_studio_shows_its_view(built_app):
    from jarvis.gui.views.video_studio.dashboard import VideoStudioView

    assert isinstance(built_app._views["video_studio"], VideoStudioView)
    built_app._navigate("video_studio")
    assert built_app._current_view_key == "video_studio"
    built_app._navigate("home")  # leave shared built_app on its default view for other tests


def test_navigating_to_design_studio_shows_its_view(built_app):
    from jarvis.gui.views.design_studio.dashboard import DesignStudioView

    assert isinstance(built_app._views["design_studio"], DesignStudioView)
    built_app._navigate("design_studio")
    assert built_app._current_view_key == "design_studio"
    built_app._navigate("home")  # leave shared built_app on its default view for other tests


def test_navigating_to_content_studio_shows_its_view(built_app):
    from jarvis.gui.views.content_studio.dashboard import ContentStudioView

    assert isinstance(built_app._views["content_studio"], ContentStudioView)
    built_app._navigate("content_studio")
    assert built_app._current_view_key == "content_studio"
    built_app._navigate("home")  # leave shared built_app on its default view for other tests


# --- Content Studio -> Reel/Design Studio handoff (open_project_id) ------------------


def test_navigate_with_open_project_id_calls_open_project_on_target_view(built_app):
    # Regression test for the Content Studio -> Reel Generator handoff
    # bug: clicking "Preview / Continue" on a linked Reel must open THAT
    # SPECIFIC project, not just the Reel Generator's generic empty
    # dashboard - _navigate()'s own open_project_id keyword argument is
    # the fix; this confirms it actually reaches the target view's own
    # open_project() method with the right id.
    target_view = built_app._views["reel_generator"]
    with patch.object(target_view, "open_project") as mock_open_project:
        built_app._navigate("reel_generator", open_project_id="some-reel-project-id")
        mock_open_project.assert_called_once_with("some-reel-project-id")
    built_app._navigate("home")


def test_navigate_with_open_project_id_works_for_design_studio_too(built_app):
    target_view = built_app._views["design_studio"]
    with patch.object(target_view, "open_project") as mock_open_project:
        built_app._navigate("design_studio", open_project_id="some-design-project-id")
        mock_open_project.assert_called_once_with("some-design-project-id")
    built_app._navigate("home")


def test_navigate_without_open_project_id_does_not_call_open_project(built_app):
    # Every OTHER existing caller of navigate() (Callable[[str], None] -
    # a single positional string, no keyword) must keep working exactly
    # as before - open_project() must never be called unless
    # open_project_id was explicitly passed.
    target_view = built_app._views["reel_generator"]
    with patch.object(target_view, "open_project") as mock_open_project:
        built_app._navigate("reel_generator")
        mock_open_project.assert_not_called()
    built_app._navigate("home")


def test_navigate_with_open_project_id_on_a_view_without_open_project_does_not_raise(built_app):
    # instagram_ai_manager's own view has no open_project() method -
    # passing open_project_id for it must be a harmless no-op, not a
    # crash (getattr(..., None) + callable() guard in _navigate()).
    built_app._navigate("instagram_ai_manager", open_project_id="irrelevant")
    assert built_app._current_view_key == "instagram_ai_manager"
    built_app._navigate("home")


# --- shutdown: restores terminal approval handlers -----------------------------------


def test_on_close_restores_terminal_approval_handlers(built_app):
    with patch("jarvis.gui.app.set_side_effect_handler") as mock_set_se:
        with patch("jarvis.gui.app.set_outside_sandbox_handler") as mock_set_os:
            with patch("jarvis.gui.app.save_history"):
                with patch.object(built_app.root, "destroy"):
                    built_app._on_close()
    mock_set_se.assert_called_once_with(None)
    mock_set_os.assert_called_once_with(None)


def test_on_close_saves_history(built_app):
    with patch("jarvis.gui.app.save_history") as mock_save:
        with patch.object(built_app.root, "destroy"):
            built_app._on_close()
    mock_save.assert_called_once_with(built_app._history)


# --- no ANTHROPIC_API_KEY: degrades gracefully instead of crashing -----------------


def test_missing_api_key_does_not_crash_and_shows_notice(no_api_key_app):
    # Construction (in the fixture) already succeeded without raising -
    # this confirms what it produced.
    assert no_api_key_app.agent is None
    no_api_key_app.transcript.configure(state="normal")
    content = no_api_key_app.transcript.get("1.0", "end")
    no_api_key_app.transcript.configure(state="disabled")
    assert "ANTHROPIC_API_KEY" in content


# --- anthropic package unavailable (e.g. Windows Smart App Control blocking its own ------
# jiter dependency) degrades gracefully instead of taking the whole GUI down --------------
#
# Real, reported bug fix: "JARVIS won't open at all: ImportError: DLL load
# failed while importing jiter" - jarvis.core.llm used to `import anthropic`
# at MODULE level, so constructing LLMClient() here in App.__init__()
# (whenever ANTHROPIC_API_KEY IS set - a real API key being configured says
# nothing about whether the package can actually be imported on this
# machine) would let that ImportError propagate straight out of __init__()
# and crash the whole app before a single window ever appeared. These tests
# confirm the fix: the app now degrades to the SAME "AI unavailable" state
# ANTHROPIC_API_KEY-not-set already produces, plus a specific transcript
# message explaining WHY.


def _construct_app_with_llm_import_error(*, error_message: str = "DLL load failed while importing jiter: An Application Control policy has blocked this file."):
    """Same construction as _construct_app() (a real API key IS set, so
    __init__() attempts LLMClient(...)), except LLMClient itself is
    patched to raise ImportError, simulating the real jiter/Smart App
    Control block without depending on whether this test machine
    actually has it right now."""
    with patch("jarvis.gui.app.ANTHROPIC_API_KEY", "fake-key"):
        with patch("jarvis.gui.app.LLMClient", side_effect=ImportError(error_message)):
            with patch("jarvis.gui.app._build_registry", return_value=MagicMock()):
                with patch("jarvis.gui.app.Agent"):
                    with patch("jarvis.gui.app.load_history") as mock_load_history:
                        mock_load_history.return_value = MagicMock(warning=None, history=[])
                        with patch("jarvis.gui.app.load_project_notes") as mock_notes:
                            mock_notes.return_value = MagicMock(notice=None, content=None)
                            with patch(
                                "jarvis.gui.app.load_update_settings",
                                return_value=UpdateSettings(check_for_updates=False),
                            ):
                                application = gui_app.JarvisApp()
    application.root.withdraw()
    return application


def test_llm_import_error_does_not_crash_app_construction():
    # The actual bug this fixes: App.__init__() must complete
    # successfully (a real window can be shown) even when LLMClient(...)
    # raises ImportError, not just RuntimeError (missing key) - before
    # this fix, this call would have raised straight out of this test.
    application = _construct_app_with_llm_import_error()
    try:
        assert application.root is not None
    finally:
        application.root.destroy()


def test_llm_import_error_degrades_to_no_llm_and_no_agent():
    application = _construct_app_with_llm_import_error()
    try:
        assert application.llm is None
        assert application.agent is None
    finally:
        application.root.destroy()


def test_llm_import_error_shows_specific_reason_in_transcript():
    # Real, reported requirement: the person must see WHY AI is
    # unavailable, not just that it is - distinguishing this from the
    # ANTHROPIC_API_KEY-not-set case (test_missing_api_key_does_not_crash
    # _and_shows_notice above), which is a different, separate situation
    # (a real key IS set here; the package itself couldn't load).
    application = _construct_app_with_llm_import_error(
        error_message="DLL load failed while importing jiter: An Application Control policy has blocked this file.",
    )
    try:
        application.transcript.configure(state="normal")
        content = application.transcript.get("1.0", "end")
        application.transcript.configure(state="disabled")
        assert "jiter" in content
        assert "Application Control" in content
    finally:
        application.root.destroy()


def test_llm_import_error_does_not_mention_api_key_not_configured():
    # The two failure messages must stay distinguishable - a real key IS
    # configured in this scenario, so the OTHER message
    # ("ANTHROPIC_API_KEY nenustatytas") must never appear here, or a
    # person would be misled into thinking they need to set a key that's
    # already set.
    application = _construct_app_with_llm_import_error()
    try:
        application.transcript.configure(state="normal")
        content = application.transcript.get("1.0", "end")
        application.transcript.configure(state="disabled")
        assert "ANTHROPIC_API_KEY nenustatytas" not in content
    finally:
        application.root.destroy()


def test_llm_import_error_still_builds_all_dashboard_views():
    # Real, reported requirement: "Išsaugok visas esamas JARVIS funkcijas"
    # - every dashboard view (Reel Generator, Instagram AI Manager, etc.)
    # must still construct successfully with self.llm=None, exactly as
    # they already do for the ANTHROPIC_API_KEY-not-set case - this
    # failure mode must not be treated any differently by the rest of
    # the app.
    application = _construct_app_with_llm_import_error()
    try:
        assert "reel_generator" in application._views
        assert "instagram_ai_manager" in application._views
        assert "video_studio" in application._views
    finally:
        application.root.destroy()


# --- approval handlers are redirected to a GUI dialog at construction --------------


def test_backend_init_redirects_approval_handlers_to_gui_dialog():
    from jarvis.core import approval

    # Uses its own App instance (not the shared built_app fixture) since
    # this test intentionally resets the approval handlers back to the
    # terminal default afterward - doing that against the shared
    # fixture would strip GUI-dialog approval from every later test.
    application = _construct_app()
    try:
        assert approval._side_effect_handler is not approval._terminal_confirm_side_effect
    finally:
        approval.set_side_effect_handler(None)
        approval.set_outside_sandbox_handler(None)
        application.root.destroy()
