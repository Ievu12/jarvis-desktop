"""Tests for jarvis.reel_generator.caption
.generate_reel_caption_package(): confirms it calls
jarvis.instagram_ai_manager.ai_services.generate_caption()/
.generate_hashtags() directly (mocked here, not re-testing
ai_services' own internals - those already have their own test suite),
grounds the content_description in the Reel's actual script text, and
tolerates either generator failing independently (partial success)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from jarvis.reel_generator import caption as caption_mod
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript, ScriptSegment


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


def test_calls_ai_services_directly_with_script_grounded_description():
    llm = MagicMock()
    with patch.object(caption_mod.ai_services, "generate_caption", return_value={"short_caption": "x", "medium_caption": "y", "long_caption": "z"}) as mock_caption, \
         patch.object(caption_mod.ai_services, "generate_hashtags", return_value={"niche": ["#yoga"]}) as mock_hashtags:
        result = caption_mod.generate_reel_caption_package(llm, _brief(), _script())

    assert result.caption == {"short_caption": "x", "medium_caption": "y", "long_caption": "z"}
    assert result.hashtags == {"niche": ["#yoga"]}
    assert result.insufficient_data is False

    caption_call_kwargs = mock_caption.call_args.kwargs
    assert "Still hitting snooze?" in caption_call_kwargs["content_description"]
    assert caption_call_kwargs["topic"] == "morning yoga"
    mock_hashtags.assert_called_once_with(llm, "morning yoga")


def test_partial_failure_is_tolerated():
    llm = MagicMock()
    with patch.object(caption_mod.ai_services, "generate_caption", return_value=None), \
         patch.object(caption_mod.ai_services, "generate_hashtags", return_value={"niche": ["#yoga"]}):
        result = caption_mod.generate_reel_caption_package(llm, _brief(), _script())

    assert result.caption is None
    assert result.hashtags == {"niche": ["#yoga"]}
    assert result.insufficient_data is False


def test_insufficient_data_when_both_fail():
    llm = MagicMock()
    with patch.object(caption_mod.ai_services, "generate_caption", return_value=None), \
         patch.object(caption_mod.ai_services, "generate_hashtags", return_value=None):
        result = caption_mod.generate_reel_caption_package(llm, _brief(), _script())

    assert result.insufficient_data is True


def test_relies_on_ai_services_own_never_raise_contract():
    # This module adds no extra try/except of its own around
    # ai_services' calls - it relies entirely on ai_services'
    # documented "never raise, return None on failure" contract (see
    # jarvis.instagram_ai_manager.ai_services' own docstring/tests for
    # that guarantee). Documented here as a design note, not a defense:
    # if ai_services ever violated its own contract, the exception
    # would propagate rather than being silently swallowed twice.
    llm = MagicMock()
    with patch.object(caption_mod.ai_services, "generate_caption", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            caption_mod.generate_reel_caption_package(llm, _brief(), _script())
