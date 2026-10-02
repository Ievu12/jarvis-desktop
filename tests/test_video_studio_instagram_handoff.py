"""Tests for jarvis.video_studio.instagram_handoff.send_to_instagram_manager():
the AI Video Studio -> Instagram AI Manager hand-off. Mocks
jarvis.instagram_ai_manager.ai_services' generate_*() functions
directly (no real LLM call) - this module's own job is building the
content_description input and routing each generator's output into
jarvis.instagram_ai_manager.db, NOT the generation logic itself (that
already has its own thorough tests in
tests/test_instagram_ai_manager_ai_services.py, reused unmodified here
per this module's own "do not duplicate existing AI infrastructure"
docstring). jarvis.instagram_ai_manager.db is redirected to a per-test
tmp_path database - confirms saved content is genuinely readable back
through THAT module's own list_*() functions, proving the hand-off
lands in the real Instagram AI Manager data store, not a separate video
studio-only copy."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.instagram_ai_manager import db as ig_db
from jarvis.video_studio.instagram_handoff import send_to_instagram_manager
from jarvis.video_studio.reel import PlannedClip, ReelEditPlan


@pytest.fixture(autouse=True)
def _isolated_ig_db(tmp_path, monkeypatch):
    db_file = tmp_path / "instagram_ai_manager.db"
    monkeypatch.setattr(ig_db, "INSTAGRAM_AI_MANAGER_DB_FILE", db_file)


@pytest.fixture(autouse=True)
def _no_real_posting_time_lookup(monkeypatch):
    # analyze_posting_times() would try a real InstagramConnector - stub
    # is_configured() to False so _suggested_posting_time() short-circuits
    # to None without touching any real credential/network path.
    from jarvis.integrations.connectors.instagram import InstagramConnector

    monkeypatch.setattr(InstagramConnector, "is_configured", lambda self: False)


def _plan() -> ReelEditPlan:
    return ReelEditPlan(
        clips=[PlannedClip(0.0, 5.0, "brush teeth wrong", "The common mistake")],
        hook="You brush wrong every day", cta="Try this tonight", target_duration_seconds=30,
        style="Educational", pacing="Normal", total_duration_seconds=5.0,
        silence_removal_suggested=False, zoom_crop_suggested=False, insufficient_data=False, message=None,
    )


_TRANSCRIPT = "Did you know most people brush their teeth wrong every day? Use small circular motions."


# --- insufficient data ---------------------------------------------------------------------


def test_no_transcript_and_no_plan_is_insufficient_data():
    llm = MagicMock()
    result = send_to_instagram_manager(llm, transcript_text="", plan=None, cover_path=None)
    assert result.insufficient_data is True
    llm.send.assert_not_called()


def test_every_generator_failing_is_insufficient_data():
    llm = MagicMock()
    with patch("jarvis.video_studio.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = None
        mock_services.generate_caption.return_value = None
        mock_services.generate_cta.return_value = None
        mock_services.generate_hashtags.return_value = None
        result = send_to_instagram_manager(llm, transcript_text=_TRANSCRIPT, plan=_plan(), cover_path=None)
    assert result.insufficient_data is True


# --- successful hand-off --------------------------------------------------------------------


def test_successful_handoff_saves_into_instagram_ai_manager_db():
    llm = MagicMock()
    fake_hooks = {"curiosity": ["Did you know...?"]}
    fake_caption = {"short_caption": "s", "medium_caption": "m", "long_caption": "l"}
    fake_ctas = {"comments": ["Comment below!"]}
    fake_hashtags = {"niche": ["#dentalcare"]}

    with patch("jarvis.video_studio.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = fake_hooks
        mock_services.generate_caption.return_value = fake_caption
        mock_services.generate_cta.return_value = fake_ctas
        mock_services.generate_hashtags.return_value = fake_hashtags
        result = send_to_instagram_manager(llm, transcript_text=_TRANSCRIPT, plan=_plan(), cover_path="/tmp/cover.jpg")

    assert result.insufficient_data is False
    assert result.hook_set_id is not None
    assert result.caption_id is not None
    assert result.cta_set_id is not None
    assert result.hashtag_set_id is not None
    assert result.cover_path == "/tmp/cover.jpg"

    # Genuinely readable back through instagram_ai_manager.db's own
    # functions - proves this landed in the real, shared data store.
    saved_hooks = ig_db.list_hook_sets()
    assert len(saved_hooks) == 1
    assert saved_hooks[0].data == fake_hooks

    saved_captions = ig_db.list_captions()
    assert len(saved_captions) == 1
    assert saved_captions[0].data == fake_caption


def test_partial_failure_still_saves_what_succeeded():
    llm = MagicMock()
    with patch("jarvis.video_studio.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = {"curiosity": ["hook"]}
        mock_services.generate_caption.return_value = None  # this one fails
        mock_services.generate_cta.return_value = {"comments": ["cta"]}
        mock_services.generate_hashtags.return_value = None  # this one fails too
        result = send_to_instagram_manager(llm, transcript_text=_TRANSCRIPT, plan=_plan(), cover_path=None)

    assert result.insufficient_data is False  # some content succeeded
    assert result.hook_set_id is not None
    assert result.caption_id is None
    assert result.cta_set_id is not None
    assert result.hashtag_set_id is None
    assert len(ig_db.list_hook_sets()) == 1
    assert len(ig_db.list_captions()) == 0


def test_content_description_grounded_in_actual_plan_captions():
    llm = MagicMock()
    with patch("jarvis.video_studio.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = {"curiosity": ["h"]}
        mock_services.generate_caption.return_value = None
        mock_services.generate_cta.return_value = None
        mock_services.generate_hashtags.return_value = None
        send_to_instagram_manager(llm, transcript_text=_TRANSCRIPT, plan=_plan(), cover_path=None)

    call_args = mock_services.generate_hooks.call_args
    topic_arg = call_args[0][1]
    assert "The common mistake" in topic_arg  # the plan's own clip caption, not a generic placeholder


def test_no_plan_falls_back_to_transcript_text():
    llm = MagicMock()
    with patch("jarvis.video_studio.instagram_handoff.ai_services") as mock_services:
        mock_services.generate_hooks.return_value = {"curiosity": ["h"]}
        mock_services.generate_caption.return_value = None
        mock_services.generate_cta.return_value = None
        mock_services.generate_hashtags.return_value = None
        send_to_instagram_manager(llm, transcript_text=_TRANSCRIPT, plan=None, cover_path=None)

    call_args = mock_services.generate_hooks.call_args
    topic_arg = call_args[0][1]
    assert "brush their teeth wrong" in topic_arg
