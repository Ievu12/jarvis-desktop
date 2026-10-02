"""Tests for jarvis.reel_generator.publish_package: the Content Package
+ Ready to Publish stage's own PublishPackage dataclass and pure
functions - GENERATED-vs-EDITED caption/hashtag protection, cover
selection, posting-time edits, the publish-approval checkpoint, and
compute_publish_status()'s own state derivation. generate_publish_package()
is the one function that calls out to real generation logic
(jarvis.reel_generator.caption.generate_reel_caption_package(), mocked
here - not re-testing that module's own internals, which already have
their own test suite) - confirms it reuses that function directly
rather than duplicating any caption/hashtag generation, and that it
never touches jarvis.reel_generator.instagram_handoff's own send
functions (this stage must never call Instagram).

Every generate_publish_package() test here ALSO mocks
instagram_handoff._suggested_posting_time() - that real function makes
a genuine Instagram Graph API network call
(analytics_services.analyze_posting_times() ->
get_recent_media_with_insights()) whenever real Instagram OAuth
credentials happen to be stored in this machine's OS keychain, and was
confirmed (during this stage's own development) to genuinely hang
running that real call from a background thread in this environment -
mocked here unconditionally so this suite never depends on, or is at
the mercy of, real Instagram connectivity/credentials/rate limits,
matching this project's own "never make an uncontrolled real external
network call in a test" discipline."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from jarvis.reel_generator import publish_package as pp
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.caption import ReelCaptionPackage
from jarvis.reel_generator.project_status import ProjectStatus
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


def _caption_package(**overrides: Any) -> ReelCaptionPackage:
    defaults: dict[str, Any] = dict(
        caption={"short_caption": "s", "medium_caption": "A great morning routine.", "long_caption": "l"},
        hashtags={"niche": ["#morningroutine"], "medium_competition": ["#yoga"], "broader": [], "branded": []},
    )
    defaults.update(overrides)
    return ReelCaptionPackage(**defaults)


# --- PublishPackage / is_ready / is_ready_to_publish --------------------------------------


def test_new_package_is_not_ready():
    package = pp.PublishPackage()
    assert package.is_ready is False
    assert package.is_ready_to_publish is False


def test_package_with_caption_and_cover_is_ready():
    package = pp.PublishPackage(caption_text="A great caption.", cover_path="/tmp/cover.jpg")
    assert package.is_ready is True


def test_package_missing_cover_is_not_ready():
    package = pp.PublishPackage(caption_text="A great caption.", cover_path=None)
    assert package.is_ready is False


def test_package_missing_caption_is_not_ready():
    package = pp.PublishPackage(caption_text="", cover_path="/tmp/cover.jpg")
    assert package.is_ready is False


def test_ready_but_not_approved_is_ready_to_publish():
    package = pp.PublishPackage(caption_text="x", cover_path="/tmp/cover.jpg", publish_approved=False)
    assert package.is_ready_to_publish is True


def test_ready_and_approved_is_not_ready_to_publish():
    # PUBLISH_APPROVED is a later, distinct state - not "still ready to publish".
    package = pp.PublishPackage(caption_text="x", cover_path="/tmp/cover.jpg", publish_approved=True)
    assert package.is_ready_to_publish is False


# --- GENERATED vs EDITED protection ---------------------------------------------------------


def test_apply_generated_fills_empty_caption_and_hashtags():
    package = pp.PublishPackage()
    result = pp.apply_generated_caption_and_hashtags(package, _caption_package())
    assert result.caption_text == "A great morning routine."
    assert "#morningroutine" in result.hashtags_text
    assert result.caption_edited is False
    assert result.hashtags_edited is False


def test_apply_generated_never_overwrites_an_edited_caption():
    package = pp.PublishPackage(caption_text="My own words.", caption_edited=True)
    result = pp.apply_generated_caption_and_hashtags(package, _caption_package())
    assert result.caption_text == "My own words."
    assert result.caption_edited is True


def test_apply_generated_never_overwrites_edited_hashtags_but_still_updates_caption():
    package = pp.PublishPackage(hashtags_text="#mytags", hashtags_edited=True)
    result = pp.apply_generated_caption_and_hashtags(package, _caption_package())
    assert result.hashtags_text == "#mytags"
    assert result.caption_text == "A great morning routine."  # caption was NOT edited, so it updates


def test_edit_caption_sets_edited_flag():
    package = pp.PublishPackage(caption_text="generated")
    result = pp.edit_caption(package, "hand-written caption")
    assert result.caption_text == "hand-written caption"
    assert result.caption_edited is True


def test_edit_hashtags_sets_edited_flag():
    package = pp.PublishPackage(hashtags_text="#generated")
    result = pp.edit_hashtags(package, "#custom #tags")
    assert result.hashtags_text == "#custom #tags"
    assert result.hashtags_edited is True


def test_edited_caption_flag_does_not_affect_hashtags_flag():
    package = pp.edit_caption(pp.PublishPackage(), "my caption")
    assert package.caption_edited is True
    assert package.hashtags_edited is False


# --- cover selection ------------------------------------------------------------------------


def test_select_cover_generated():
    package = pp.select_cover(pp.PublishPackage(), cover_path="/tmp/cover.jpg", source=pp.COVER_SOURCE_GENERATED)
    assert package.cover_path == "/tmp/cover.jpg"
    assert package.cover_source == pp.COVER_SOURCE_GENERATED
    assert package.cover_scene_number is None


def test_select_cover_reel_frame_records_scene_number():
    package = pp.select_cover(
        pp.PublishPackage(), cover_path="/tmp/scene03.jpg", source=pp.COVER_SOURCE_REEL_FRAME, scene_number=3,
    )
    assert package.cover_path == "/tmp/scene03.jpg"
    assert package.cover_source == pp.COVER_SOURCE_REEL_FRAME
    assert package.cover_scene_number == 3


# --- posting time -----------------------------------------------------------------------


def test_edit_posting_time_sets_time_and_timezone():
    package = pp.edit_posting_time(pp.PublishPackage(), posting_time="Saturday 9am", timezone="America/New_York")
    assert package.suggested_posting_time == "Saturday 9am"
    assert package.posting_timezone == "America/New_York"


def test_edit_posting_time_defaults_timezone_to_empty():
    package = pp.edit_posting_time(pp.PublishPackage(), posting_time="Saturday 9am")
    assert package.posting_timezone == ""


# --- APPROVE FOR PUBLISHING ---------------------------------------------------------------


def test_approve_for_publishing_sets_flag_and_timestamp():
    package = pp.approve_for_publishing(pp.PublishPackage(), now_iso="2026-01-01T00:00:00")
    assert package.publish_approved is True
    assert package.publish_approved_at == "2026-01-01T00:00:00"


def test_unapprove_for_publishing_clears_flag_and_timestamp():
    package = pp.approve_for_publishing(pp.PublishPackage(), now_iso="2026-01-01T00:00:00")
    package = pp.unapprove_for_publishing(package)
    assert package.publish_approved is False
    assert package.publish_approved_at is None


# --- generate_publish_package() -----------------------------------------------------------


def test_generate_publish_package_calls_existing_caption_generator_only():
    llm = MagicMock()
    with patch(
        "jarvis.reel_generator.publish_package.generate_reel_caption_package",
        return_value=_caption_package(),
    ) as mock_gen, patch.object(pp.instagram_handoff, "_suggested_posting_time", return_value=None):
        package, caption_package = pp.generate_publish_package(
            llm, brief=_brief(), script=_script(), project_id="proj1",
            existing=None, cover_path="/tmp/cover.jpg", now_iso="2026-01-01T00:00:00",
        )
    mock_gen.assert_called_once_with(llm, _brief(), _script())
    assert package.status == pp.STATUS_READY
    assert package.caption_text == "A great morning routine."
    assert package.cover_path == "/tmp/cover.jpg"
    assert package.generated_at == "2026-01-01T00:00:00"
    assert caption_package.caption is not None


def test_generate_publish_package_never_calls_instagram_handoff():
    # Module brief's own hard rule: this stage must never publish or
    # call any Instagram send function.
    llm = MagicMock()
    with patch(
        "jarvis.reel_generator.publish_package.generate_reel_caption_package",
        return_value=_caption_package(),
    ), patch.object(pp.instagram_handoff, "_suggested_posting_time", return_value=None), patch(
        "jarvis.reel_generator.instagram_handoff.send_idea_reel_to_instagram_manager",
        side_effect=AssertionError("must never be called by content package generation"),
    ), patch(
        "jarvis.reel_generator.instagram_handoff.send_footage_reel_to_instagram_manager",
        side_effect=AssertionError("must never be called by content package generation"),
    ):
        pp.generate_publish_package(
            llm, brief=_brief(), script=_script(), project_id="proj1",
            existing=None, cover_path="/tmp/cover.jpg", now_iso="2026-01-01T00:00:00",
        )


def test_generate_publish_package_preserves_existing_edits():
    llm = MagicMock()
    existing = pp.edit_caption(pp.PublishPackage(cover_path="/tmp/old.jpg"), "my hand-edited caption")
    with patch(
        "jarvis.reel_generator.publish_package.generate_reel_caption_package",
        return_value=_caption_package(),
    ), patch.object(pp.instagram_handoff, "_suggested_posting_time", return_value=None):
        package, _ = pp.generate_publish_package(
            llm, brief=_brief(), script=_script(), project_id="proj1",
            existing=existing, cover_path="/tmp/new_cover.jpg", now_iso="2026-01-01T00:00:00",
        )
    assert package.caption_text == "my hand-edited caption"
    # cover_path was already set on `existing` - a regenerate does not silently replace it.
    assert package.cover_path == "/tmp/old.jpg"


def test_generate_publish_package_sets_generated_at_once_only():
    llm = MagicMock()
    existing = pp.PublishPackage(generated_at="2025-01-01T00:00:00")
    with patch(
        "jarvis.reel_generator.publish_package.generate_reel_caption_package",
        return_value=_caption_package(),
    ), patch.object(pp.instagram_handoff, "_suggested_posting_time", return_value=None):
        package, _ = pp.generate_publish_package(
            llm, brief=_brief(), script=_script(), project_id="proj1",
            existing=existing, cover_path="/tmp/cover.jpg", now_iso="2026-06-01T00:00:00",
        )
    assert package.generated_at == "2025-01-01T00:00:00"


def test_generate_publish_package_fills_suggested_posting_time_once():
    llm = MagicMock()
    with patch(
        "jarvis.reel_generator.publish_package.generate_reel_caption_package",
        return_value=_caption_package(),
    ), patch.object(pp.instagram_handoff, "_suggested_posting_time", return_value="Tuesday around 6pm (high confidence)"):
        package, _ = pp.generate_publish_package(
            llm, brief=_brief(), script=_script(), project_id="proj1",
            existing=None, cover_path="/tmp/cover.jpg", now_iso="2026-01-01T00:00:00",
        )
    assert package.suggested_posting_time == "Tuesday around 6pm (high confidence)"


def test_generate_publish_package_does_not_overwrite_an_existing_posting_time():
    llm = MagicMock()
    existing = pp.edit_posting_time(pp.PublishPackage(), posting_time="Custom Friday 8am")
    with patch(
        "jarvis.reel_generator.publish_package.generate_reel_caption_package",
        return_value=_caption_package(),
    ), patch.object(pp.instagram_handoff, "_suggested_posting_time", return_value="Should not be used"):
        package, _ = pp.generate_publish_package(
            llm, brief=_brief(), script=_script(), project_id="proj1",
            existing=existing, cover_path="/tmp/cover.jpg", now_iso="2026-01-01T00:00:00",
        )
    assert package.suggested_posting_time == "Custom Friday 8am"


# --- compute_publish_status() ---------------------------------------------------------------


def test_status_reel_approved_when_not_yet_approved():
    status = pp.compute_publish_status(None, reel_approved=False, export_path="/tmp/reel.mp4")
    assert status == ProjectStatus.REEL_APPROVED


def test_status_reel_approved_when_export_missing():
    package = pp.PublishPackage(caption_text="x", cover_path="y", status=pp.STATUS_READY)
    status = pp.compute_publish_status(package, reel_approved=True, export_path=None)
    assert status == ProjectStatus.REEL_APPROVED


def test_status_reel_approved_when_no_package_yet():
    status = pp.compute_publish_status(None, reel_approved=True, export_path="/tmp/reel.mp4")
    assert status == ProjectStatus.REEL_APPROVED


def test_status_generating_content_package_flag():
    status = pp.compute_publish_status(
        None, reel_approved=True, export_path="/tmp/reel.mp4", is_generating_package=True,
    )
    assert status == ProjectStatus.GENERATING_CONTENT_PACKAGE


def test_status_ready_to_publish_when_package_complete():
    package = pp.PublishPackage(caption_text="x", cover_path="y", status=pp.STATUS_READY)
    status = pp.compute_publish_status(package, reel_approved=True, export_path="/tmp/reel.mp4")
    assert status == ProjectStatus.READY_TO_PUBLISH


def test_status_publish_approved_when_package_approved():
    package = pp.approve_for_publishing(
        pp.PublishPackage(caption_text="x", cover_path="y", status=pp.STATUS_READY),
        now_iso="2026-01-01T00:00:00",
    )
    status = pp.compute_publish_status(package, reel_approved=True, export_path="/tmp/reel.mp4")
    assert status == ProjectStatus.PUBLISH_APPROVED


def test_status_incomplete_package_is_generating_content_package():
    # A package that exists but isn't ready yet (e.g. caption generation
    # failed) - still meaningfully "in progress", not READY_TO_PUBLISH.
    package = pp.PublishPackage(caption_text="", cover_path=None, status=pp.STATUS_READY)
    status = pp.compute_publish_status(package, reel_approved=True, export_path="/tmp/reel.mp4")
    assert status == ProjectStatus.GENERATING_CONTENT_PACKAGE
