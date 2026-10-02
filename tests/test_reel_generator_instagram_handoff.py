"""Tests for jarvis.reel_generator.instagram_handoff: the AI Reel
Generator -> Instagram AI Manager hand-off, for both Mode B
(send_idea_reel_to_instagram_manager) and Mode A
(send_footage_reel_to_instagram_manager). Mocks
jarvis.instagram_ai_manager.ai_services' generate_*() functions
directly (no real LLM call) - same pattern as
tests/test_video_studio_instagram_handoff.py (see that file's own
docstring for the full rationale: this module's own job is building
the input and routing output into jarvis.instagram_ai_manager.db, not
the generation logic itself). jarvis.instagram_ai_manager.db is
redirected to a per-test tmp_path database - confirms saved content is
genuinely readable back through THAT module's own list_*() functions.

Confirms (Mode B): the caption/hashtags Stage 2 already generated are
forwarded AS-IS (never re-generated - no ai_services.generate_caption/
generate_hashtags call at all), only hooks/CTAs are generated fresh, an
empty script is insufficient_data before any LLM call, and partial
generator failure still saves what succeeded.

Confirms (Mode A): grounded in the plan's own hook/clip captions (never
a generic placeholder), an insufficient-data plan short-circuits before
any LLM call, and the mechanism otherwise matches
jarvis.video_studio.instagram_handoff exactly (same test shape reused
for the equivalent module)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from jarvis.instagram_ai_manager import db as ig_db
from jarvis.reel_generator import instagram_handoff
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.caption import ReelCaptionPackage
from jarvis.reel_generator.script import ReelScript, ScriptSegment
from jarvis.video_studio.reel import PlannedClip, ReelEditPlan


@pytest.fixture(autouse=True)
def _isolated_ig_db(tmp_path, monkeypatch):
    db_file = tmp_path / "instagram_ai_manager.db"
    monkeypatch.setattr(ig_db, "INSTAGRAM_AI_MANAGER_DB_FILE", db_file)


@pytest.fixture(autouse=True)
def _no_real_posting_time_lookup(monkeypatch):
    from jarvis.integrations.connectors.instagram import InstagramConnector

    monkeypatch.setattr(InstagramConnector, "is_configured", lambda self: False)


def _brief(**overrides: Any) -> ReelBrief:
    defaults: dict[str, Any] = dict(
        topic="morning yoga", audience="wellness beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=20, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _script() -> ReelScript:
    return ReelScript(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=4, text="Still hitting snooze?"),
        ScriptSegment(kind="value", start_seconds=4, end_seconds=15, text="Stretch, breathe, move."),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="Save this Reel."),
    ))


# --- Mode B: send_idea_reel_to_instagram_manager -------------------------------------------


def test_empty_script_is_insufficient_data_without_calling_llm():
    llm = MagicMock()
    empty_script = ReelScript(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=1, text=""),
        ScriptSegment(kind="value", start_seconds=1, end_seconds=2, text=""),
        ScriptSegment(kind="cta", start_seconds=2, end_seconds=3, text=""),
    ))
    package = ReelCaptionPackage(caption=None, hashtags=None)
    result = instagram_handoff.send_idea_reel_to_instagram_manager(
        llm, brief=_brief(), script=empty_script, caption_package=package, cover_path=None, export_path=None,
    )
    assert result.insufficient_data is True
    llm.send.assert_not_called()


def test_idea_reel_forwards_existing_caption_without_regenerating():
    llm = MagicMock()
    package = ReelCaptionPackage(
        caption={"short_caption": "s", "medium_caption": "m", "long_caption": "l"},
        hashtags={"niche": ["#yoga"]},
    )
    with patch("jarvis.reel_generator.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = {"curiosity": ["hook"]}
        mock_services.generate_cta.return_value = {"comments": ["cta"]}
        result = instagram_handoff.send_idea_reel_to_instagram_manager(
            llm, brief=_brief(), script=_script(), caption_package=package,
            cover_path="/tmp/cover.jpg", export_path="/tmp/reel.mp4",
        )

    # The caption/hashtags Stage 2 already generated are used as-is -
    # no generate_caption/generate_hashtags call at all.
    mock_services.generate_caption.assert_not_called()
    mock_services.generate_hashtags.assert_not_called()

    assert result.insufficient_data is False
    assert result.caption_id is not None
    assert result.hashtag_set_id is not None
    assert result.hook_set_id is not None
    assert result.cta_set_id is not None
    assert result.cover_path == "/tmp/cover.jpg"
    assert result.video_path == "/tmp/reel.mp4"

    saved_captions = ig_db.list_captions()
    assert len(saved_captions) == 1
    assert saved_captions[0].data == package.caption

    saved_hashtags = ig_db.list_hashtag_sets()
    assert len(saved_hashtags) == 1
    assert saved_hashtags[0].data == package.hashtags


def test_idea_reel_every_generator_failing_is_insufficient_data():
    llm = MagicMock()
    package = ReelCaptionPackage(caption=None, hashtags=None)
    with patch("jarvis.reel_generator.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = None
        mock_services.generate_cta.return_value = None
        result = instagram_handoff.send_idea_reel_to_instagram_manager(
            llm, brief=_brief(), script=_script(), caption_package=package, cover_path=None, export_path=None,
        )
    assert result.insufficient_data is True


def test_idea_reel_partial_failure_still_saves_what_succeeded():
    llm = MagicMock()
    package = ReelCaptionPackage(caption={"short_caption": "s", "medium_caption": "m", "long_caption": "l"}, hashtags=None)
    with patch("jarvis.reel_generator.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = None  # fails
        mock_services.generate_cta.return_value = {"comments": ["cta"]}
        result = instagram_handoff.send_idea_reel_to_instagram_manager(
            llm, brief=_brief(), script=_script(), caption_package=package, cover_path=None, export_path=None,
        )

    assert result.insufficient_data is False
    assert result.hook_set_id is None
    assert result.caption_id is not None
    assert result.cta_set_id is not None
    assert result.hashtag_set_id is None


def test_idea_reel_hook_generation_grounded_in_script_text():
    llm = MagicMock()
    package = ReelCaptionPackage(caption=None, hashtags=None)
    with patch("jarvis.reel_generator.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = {"curiosity": ["h"]}
        mock_services.generate_cta.return_value = None
        instagram_handoff.send_idea_reel_to_instagram_manager(
            llm, brief=_brief(), script=_script(), caption_package=package, cover_path=None, export_path=None,
        )
    call_args = mock_services.generate_hooks.call_args
    topic_arg = call_args[0][1]
    assert "Still hitting snooze?" in topic_arg


# --- Mode A: send_footage_reel_to_instagram_manager -----------------------------------------


def _plan(**overrides: Any) -> ReelEditPlan:
    defaults: dict[str, Any] = dict(
        clips=[PlannedClip(0.0, 4.0, "First, it boosts your energy.", "Boosts energy")],
        hook="3 yoga benefits", cta="Save this", target_duration_seconds=15, style="Educational",
        pacing="Normal", total_duration_seconds=4.0, silence_removal_suggested=False,
        zoom_crop_suggested=False, insufficient_data=False, message=None,
    )
    defaults.update(overrides)
    return ReelEditPlan(**defaults)


def test_insufficient_plan_is_insufficient_data_without_calling_llm():
    llm = MagicMock()
    empty_plan = _plan(clips=[], insufficient_data=True, message="No highlights found.")
    result = instagram_handoff.send_footage_reel_to_instagram_manager(llm, plan=empty_plan, export_path=None)
    assert result.insufficient_data is True
    llm.send.assert_not_called()


def test_footage_reel_successful_handoff_saves_into_instagram_ai_manager_db():
    llm = MagicMock()
    with patch("jarvis.reel_generator.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = {"curiosity": ["Did you know...?"]}
        mock_services.generate_caption.return_value = {"short_caption": "s", "medium_caption": "m", "long_caption": "l"}
        mock_services.generate_cta.return_value = {"comments": ["Comment below!"]}
        mock_services.generate_hashtags.return_value = {"niche": ["#yoga"]}
        result = instagram_handoff.send_footage_reel_to_instagram_manager(
            llm, plan=_plan(), export_path="/tmp/reel.mp4",
        )

    assert result.insufficient_data is False
    assert result.hook_set_id is not None
    assert result.caption_id is not None
    assert result.cta_set_id is not None
    assert result.hashtag_set_id is not None
    assert result.video_path == "/tmp/reel.mp4"

    saved_hooks = ig_db.list_hook_sets()
    assert len(saved_hooks) == 1
    assert saved_hooks[0].data == {"curiosity": ["Did you know...?"]}


def test_footage_reel_content_description_grounded_in_plan():
    llm = MagicMock()
    with patch("jarvis.reel_generator.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = {"curiosity": ["h"]}
        mock_services.generate_caption.return_value = None
        mock_services.generate_cta.return_value = None
        mock_services.generate_hashtags.return_value = None
        instagram_handoff.send_footage_reel_to_instagram_manager(llm, plan=_plan(), export_path=None)

    call_args = mock_services.generate_hooks.call_args
    topic_arg = call_args[0][1]
    assert "Boosts energy" in topic_arg  # the plan's own clip caption, not a generic placeholder
    assert "3 yoga benefits" in topic_arg  # the plan's own hook


def test_footage_reel_every_generator_failing_is_insufficient_data():
    llm = MagicMock()
    with patch("jarvis.reel_generator.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = None
        mock_services.generate_caption.return_value = None
        mock_services.generate_cta.return_value = None
        mock_services.generate_hashtags.return_value = None
        result = instagram_handoff.send_footage_reel_to_instagram_manager(llm, plan=_plan(), export_path=None)
    assert result.insufficient_data is True
