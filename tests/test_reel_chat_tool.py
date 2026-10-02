"""Tests for jarvis.tools.reel_chat.CreateReelDraftTool: the agent-
facing tool creating a real AI Reel Generator project (brief + script
only, module brief's own "STEP 1 - AI CONCEPT" stage) from a chat
message, then signaling the GUI to navigate to it via
jarvis.tools.reel_navigation. Real jarvis.reel_generator.storage/.db
calls throughout (redirected to a per-test tmp_path), LLM calls mocked
(no real API call in these tests).

Confirms: a real project is created and saved with a real brief/
script, the navigation signal is set only after a genuine success (or
a brief-only partial success), an empty/missing idea is rejected
without creating anything, a brief-generation failure returns a clear
error and does NOT request navigation, a script-generation failure
still requests navigation (a usable draft exists even without a
script), and the tool never raises for any of these cases."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.reel_generator import db, storage
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript, ScriptSegment
from jarvis.tools import reel_chat, reel_navigation
from jarvis.tools.reel_chat import CreateReelDraftTool


@pytest.fixture(autouse=True)
def _clear_pending_navigation():
    """reel_navigation's own _pending_project_id is a plain module-level
    global shared by every test in this process (see that module's own
    docstring) - a real, hand-observed flake: if an earlier assert in
    one of this file's own tests fails BEFORE that test reaches its own
    consume_navigation_request() call, the pending request is left set
    and leaks into a LATER test here or in tests/test_reel_navigation.py
    (which asserts "no request" as its own starting condition). Cleared
    unconditionally before/after every test in this file so a failure
    partway through one test never poisons a later, unrelated test."""
    reel_navigation.consume_navigation_request()
    yield
    reel_navigation.consume_navigation_request()


def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "reel_generator.db"
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(reel_chat.db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(reel_chat.storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)


def _fake_brief(**overrides) -> ReelBrief:
    defaults = dict(
        topic="morning yoga", audience="wellness beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=20, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _fake_script() -> ReelScript:
    return ReelScript(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=5, text="Still hitting snooze?"),
        ScriptSegment(kind="value", start_seconds=5, end_seconds=15, text="Stretch. Breathe. Move."),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="Save this Reel."),
    ))


def test_empty_idea_is_rejected_without_creating_a_project(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    result = CreateReelDraftTool().run(idea="   ")
    assert result.ok is False
    assert db.list_projects() == []


def test_missing_idea_argument_is_rejected(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    result = CreateReelDraftTool().run()
    assert result.ok is False


def test_no_anthropic_api_key_returns_clear_error(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    with patch.object(reel_chat, "LLMClient", side_effect=RuntimeError("ANTHROPIC_API_KEY is not set.")):
        result = CreateReelDraftTool().run(idea="a Reel about morning yoga")
    assert result.ok is False
    assert "ANTHROPIC_API_KEY" in result.output
    assert db.list_projects() == []


def test_successful_creation_saves_real_project_and_requests_navigation(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    brief = _fake_brief()
    script = _fake_script()
    with patch.object(reel_chat, "LLMClient", return_value=MagicMock()), \
         patch.object(reel_chat, "generate_reel_brief", return_value=brief), \
         patch.object(reel_chat, "generate_reel_script", return_value=script):
        result = CreateReelDraftTool().run(idea="Sukurk Reel apie rytinę jogą")

    assert result.ok is True
    assert "morning yoga" in result.output

    records = db.list_projects()
    assert len(records) == 1
    assert records[0].brief_data is not None
    assert records[0].script_data is not None
    assert records[0].script_approved is False  # module brief's own hard gate - never pre-approved

    pending = reel_navigation.consume_navigation_request()
    assert pending == records[0].id


def test_brief_failure_returns_error_and_does_not_request_navigation(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    with patch.object(reel_chat, "LLMClient", return_value=MagicMock()), \
         patch.object(reel_chat, "generate_reel_brief", return_value=None):
        result = CreateReelDraftTool().run(idea="a Reel about morning yoga")

    assert result.ok is False
    assert reel_navigation.consume_navigation_request() is None


def test_script_failure_still_requests_navigation_to_the_brief_only_draft(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    brief = _fake_brief()
    with patch.object(reel_chat, "LLMClient", return_value=MagicMock()), \
         patch.object(reel_chat, "generate_reel_brief", return_value=brief), \
         patch.object(reel_chat, "generate_reel_script", return_value=None):
        result = CreateReelDraftTool().run(idea="a Reel about morning yoga")

    assert result.ok is True  # a usable (brief-only) draft exists
    records = db.list_projects()
    assert len(records) == 1
    assert records[0].brief_data is not None
    assert records[0].script_data is None

    pending = reel_navigation.consume_navigation_request()
    assert pending == records[0].id


def test_lithuanian_idea_is_detected_and_passed_through(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    brief = _fake_brief(language="lt")
    script = _fake_script()
    captured = {}

    def fake_generate_brief(llm, request_text, *, language, **kwargs):
        captured["request_text"] = request_text
        captured["language"] = language
        return brief

    with patch.object(reel_chat, "LLMClient", return_value=MagicMock()), \
         patch.object(reel_chat, "generate_reel_brief", side_effect=fake_generate_brief), \
         patch.object(reel_chat, "generate_reel_script", return_value=script):
        CreateReelDraftTool().run(idea="Sukurk Reel apie rytinę jogą")

    assert captured["language"] == "lt"
    assert captured["request_text"] == "Sukurk Reel apie rytinę jogą"


def test_english_idea_is_detected_as_english(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    brief = _fake_brief()
    script = _fake_script()
    captured = {}

    def fake_generate_brief(llm, request_text, *, language, **kwargs):
        captured["language"] = language
        return brief

    with patch.object(reel_chat, "LLMClient", return_value=MagicMock()), \
         patch.object(reel_chat, "generate_reel_brief", side_effect=fake_generate_brief), \
         patch.object(reel_chat, "generate_reel_script", return_value=script):
        CreateReelDraftTool().run(idea="Create a Reel about morning yoga")

    assert captured["language"] == "en"


def test_storage_error_returns_clear_message_not_raise(tmp_path, monkeypatch):
    _isolated_storage(tmp_path, monkeypatch)
    with patch.object(reel_chat.storage, "create_project", side_effect=reel_chat.storage.StorageError("disk full")):
        result = CreateReelDraftTool().run(idea="a Reel about morning yoga")
    assert result.ok is False
    assert "disk full" in result.output


def test_tool_never_calls_storyboard_scene_or_export_functions():
    # This tool must never go beyond brief+script - confirms it imports
    # nothing from jarvis.reel_generator.storyboard/.scenes/.scene_render/
    # .export (the actual downstream generation modules), matching its
    # own docstring's "deliberately does NOT go any further than
    # brief+script" guarantee.
    import jarvis.tools.reel_chat as module

    forbidden_names = {
        "generate_storyboard", "generate_visual_plan", "render_all_scenes",
        "render_all_story_scenes", "export_reel_video",
    }
    module_attrs = set(dir(module))
    assert not (forbidden_names & module_attrs)
