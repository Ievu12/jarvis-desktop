"""Tests for jarvis.reel_generator.cover: cover title generation
(mocked LLM) and cover rendering (real jarvis.design_studio.render
.render_design() against a per-test tmp_path - fast, deterministic,
local Pillow compositing, no mocking needed). Confirms: a valid cover
title response is parsed, malformed/empty responses return None,
render_cover() produces a 1080x1920 image, and a RenderError from the
underlying render_design() is wrapped as CoverError."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from jarvis.reel_generator import cover
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript, ScriptSegment


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


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


# --- generate_cover_text -------------------------------------------------------------------


def test_valid_response_produces_cover_text():
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "3 YOGA HABITS FOR A BETTER MORNING", "supporting_text": "Try today"})
    result = cover.generate_cover_text(llm, _brief(), _script())
    assert result is not None
    assert result.title == "3 YOGA HABITS FOR A BETTER MORNING"
    assert result.supporting_text == "Try today"


def test_missing_title_returns_none():
    llm = MagicMock()
    llm.send.return_value = _json_response({"supporting_text": "Try today"})
    assert cover.generate_cover_text(llm, _brief(), _script()) is None


def test_empty_supporting_text_is_accepted():
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "A Title", "supporting_text": ""})
    result = cover.generate_cover_text(llm, _brief(), _script())
    assert result is not None
    assert result.supporting_text == ""


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json")
    assert cover.generate_cover_text(llm, _brief(), _script()) is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert cover.generate_cover_text(llm, _brief(), _script()) is None


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "A Title", "supporting_text": ""})
    cover.generate_cover_text(llm, _brief(), _script())
    assert llm.send.call_args[0][1] == []


# --- render_cover ---------------------------------------------------------------------------


def test_render_cover_produces_1080x1920_image(tmp_path):
    cover_text = cover.CoverText(title="3 YOGA HABITS", supporting_text="Try today")
    result = cover.render_cover(_brief(), cover_text, output_path=tmp_path / "cover.jpg")
    assert result.output_path.is_file()
    with Image.open(result.output_path) as img:
        assert img.size == (1080, 1920)


def test_render_cover_applies_style_transform(tmp_path):
    from jarvis.design_studio.styles import resolve_style

    applied = []

    def _record(style):
        applied.append(style)
        return style

    cover_text = cover.CoverText(title="A Title", supporting_text="")
    cover.render_cover(_brief(), cover_text, output_path=tmp_path / "cover.jpg", style_transform=_record)
    assert applied == [resolve_style("yoga")]


def test_render_cover_wraps_render_error(tmp_path):
    from jarvis.design_studio.render import RenderError

    with patch.object(cover, "render_design", side_effect=RenderError("simulated failure")):
        with pytest.raises(cover.CoverError, match="simulated failure"):
            cover.render_cover(_brief(), cover.CoverText(title="X", supporting_text=""), output_path=tmp_path / "cover.jpg")


# --- "Regenerate Cover looks the same" bug fix -----------------------------------------------


def test_previous_titles_are_included_in_the_prompt_to_request_variety():
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "A NEW ANGLE", "supporting_text": ""})
    cover.generate_cover_text(llm, _brief(), _script(), previous_titles=("3 YOGA HABITS FOR A BETTER MORNING",))
    sent_prompt = llm.send.call_args.args[0][0]["content"]
    assert "3 YOGA HABITS FOR A BETTER MORNING" in sent_prompt
    assert "different" in sent_prompt.lower()


def test_no_previous_titles_matches_original_prompt_exactly():
    # First-time generation (no previous_titles) must produce the EXACT
    # same prompt as before this fix - no regression for the very first
    # cover a Reel ever gets.
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "3 YOGA HABITS", "supporting_text": ""})
    cover.generate_cover_text(llm, _brief(), _script())
    sent_prompt = llm.send.call_args.args[0][0]["content"]
    assert sent_prompt == f"Topic: {_brief().topic}\nScript: {_script().full_text}"


def test_attempt_zero_uses_the_briefs_own_style(tmp_path):
    # attempt=0 (the default, and every existing call site's behavior)
    # must render with brief.style unchanged - a first-time cover looks
    # identical to pre-fix behavior.
    from jarvis.design_studio.styles import resolve_style

    cover_text = cover.CoverText(title="A Title", supporting_text="")
    applied = []
    cover.render_cover(
        _brief(), cover_text, output_path=tmp_path / "cover.jpg", style_transform=lambda s: applied.append(s) or s,
        attempt=0,
    )
    assert applied == [resolve_style("yoga")]


def test_later_attempts_use_a_visually_different_style(tmp_path):
    # The real fix: REGENERATE COVER (attempt >= 1) must render with a
    # DIFFERENT DesignStyle than the Reel's own brief.style - confirms
    # "Regenerate Cover looks the same" is actually fixed, not just that
    # the function accepts a new parameter.
    from jarvis.design_studio.styles import resolve_style

    cover_text = cover.CoverText(title="A Title", supporting_text="")
    applied = []
    cover.render_cover(
        _brief(), cover_text, output_path=tmp_path / "cover.jpg", style_transform=lambda s: applied.append(s) or s,
        attempt=1,
    )
    assert applied[0] != resolve_style("yoga")


def test_consecutive_attempts_cycle_through_different_styles(tmp_path):
    from jarvis.design_studio.styles import resolve_style

    cover_text = cover.CoverText(title="A Title", supporting_text="")
    seen_styles = []
    for attempt in range(1, 5):
        applied: list = []
        cover.render_cover(
            _brief(), cover_text, output_path=tmp_path / f"cover_{attempt}.jpg",
            style_transform=lambda s: applied.append(s) or s, attempt=attempt,
        )
        seen_styles.append(applied[0])
    # At least 2 distinct styles across 4 consecutive regenerations -
    # never every attempt landing on the exact same one.
    assert len(set(seen_styles)) >= 2
    # And brief.style ("yoga") itself is never re-shown on a later attempt.
    assert resolve_style("yoga") not in seen_styles


def test_attempt_never_crashes_when_briefs_style_is_in_the_rotation(tmp_path):
    # A Reel whose own brief.style happens to already be one of the
    # rotation's own entries (e.g. "modern") must still produce a
    # DIFFERENT style on regenerate, never silently "changing" back to
    # the same one because it got skipped into itself.
    from jarvis.design_studio.styles import resolve_style

    brief = _brief(style="modern")
    cover_text = cover.CoverText(title="A Title", supporting_text="")
    applied = []
    cover.render_cover(
        brief, cover_text, output_path=tmp_path / "cover.jpg",
        style_transform=lambda s: applied.append(s) or s, attempt=1,
    )
    assert applied[0] != resolve_style("modern")


# --- generate_cover_candidates (3-cover picker - "generate 3 different cover -----------------
# variants" requirement) -----------------------------------------------------------------------


def test_generates_three_candidates_with_three_distinct_styles(tmp_path):
    llm = MagicMock()
    titles = iter([
        {"title": "3 YOGA HABITS", "supporting_text": ""},
        {"title": "MORNING RITUAL", "supporting_text": ""},
        {"title": "START YOUR DAY", "supporting_text": ""},
    ])
    llm.send.side_effect = lambda *a, **k: _json_response(next(titles))

    candidates = cover.generate_cover_candidates(llm, _brief(), _script(), output_dir=tmp_path)
    assert len(candidates) == 3
    assert [c.attempt for c in candidates] == [0, 1, 2]
    assert len({c.style_name for c in candidates}) == 3
    assert len({c.cover_text.title for c in candidates}) == 3
    for c in candidates:
        assert c.image_path.is_file()
        with Image.open(c.image_path) as img:
            assert img.size == (1080, 1920)


def test_generates_three_distinct_titles_via_previous_titles_chain():
    llm = MagicMock()
    prompts_seen = []

    def fake_send(messages, tools, **kwargs):
        prompts_seen.append(messages[0]["content"])
        n = len(prompts_seen)
        return _json_response({"title": f"TITLE {n}", "supporting_text": ""})

    llm.send.side_effect = fake_send
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        cover.generate_cover_candidates(llm, _brief(), _script(), output_dir=Path(d))

    # The 2nd and 3rd calls must each list every title generated so far.
    assert "TITLE 1" not in prompts_seen[0]
    assert "TITLE 1" in prompts_seen[1]
    assert "TITLE 1" in prompts_seen[2] and "TITLE 2" in prompts_seen[2]


def test_candidate_text_failure_is_skipped_not_fatal(tmp_path):
    llm = MagicMock()
    responses = iter([
        _json_response({"title": "GOOD ONE", "supporting_text": ""}),
        _text_response("not valid json"),
        _json_response({"title": "ANOTHER GOOD ONE", "supporting_text": ""}),
    ])
    llm.send.side_effect = lambda *a, **k: next(responses)

    candidates = cover.generate_cover_candidates(llm, _brief(), _script(), output_dir=tmp_path)
    assert len(candidates) == 2
    assert [c.attempt for c in candidates] == [0, 2]


def test_candidate_render_failure_is_skipped_not_fatal(tmp_path):
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "A TITLE", "supporting_text": ""})

    from jarvis.design_studio.render import RenderError

    call_count = [0]
    original_render_cover = cover.render_cover

    def flaky_render_cover(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 2:
            raise cover.CoverError("simulated failure")
        return original_render_cover(*args, **kwargs)

    with patch.object(cover, "render_cover", side_effect=flaky_render_cover):
        candidates = cover.generate_cover_candidates(llm, _brief(), _script(), output_dir=tmp_path)
    assert len(candidates) == 2
    assert [c.attempt for c in candidates] == [0, 2]


def test_start_attempt_continues_the_style_rotation(tmp_path):
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "A TITLE", "supporting_text": ""})

    batch1 = cover.generate_cover_candidates(llm, _brief(), _script(), output_dir=tmp_path, count=3)
    batch2 = cover.generate_cover_candidates(
        llm, _brief(), _script(), output_dir=tmp_path, count=3, start_attempt=3,
        previous_titles=tuple(c.cover_text.title for c in batch1),
    )
    assert [c.attempt for c in batch2] == [3, 4, 5]
    all_styles = {c.style_name for c in batch1} | {c.style_name for c in batch2}
    assert len(all_styles) == 6


def test_custom_count_generates_that_many_candidates(tmp_path):
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "A TITLE", "supporting_text": ""})
    candidates = cover.generate_cover_candidates(llm, _brief(), _script(), output_dir=tmp_path, count=2)
    assert len(candidates) == 2


# --- CoverEditOverrides / apply_cover_edit_overrides / render_cover_with_overrides -----------
# (EDIT COVER - "let me edit text, colors, font, position" requirement) -----------------------


def test_apply_overrides_changes_only_the_set_fields():
    original = cover.CoverText(title="Original Title", supporting_text="Original support")
    overrides = cover.CoverEditOverrides(title="New Title")
    new_text, _ = cover.apply_cover_edit_overrides(original, overrides)
    assert new_text.title == "New Title"
    assert new_text.supporting_text == "Original support"


def test_apply_overrides_with_nothing_set_is_identity():
    original = cover.CoverText(title="Original Title", supporting_text="Original support")
    new_text, transform = cover.apply_cover_edit_overrides(original, cover.CoverEditOverrides())
    assert new_text == original
    from jarvis.design_studio.styles import resolve_style

    style = resolve_style("yoga")
    assert transform(style) == style


def test_apply_overrides_changes_headline_color():
    from jarvis.design_studio.styles import resolve_style

    overrides = cover.CoverEditOverrides(headline_color="#FF0000")
    _, transform = cover.apply_cover_edit_overrides(cover.CoverText(title="T", supporting_text=""), overrides)
    style = resolve_style("yoga")
    new_style = transform(style)
    assert new_style.headline_color == "#FF0000"
    assert new_style.background_color_1 == style.background_color_1


def test_apply_overrides_changes_background_colors_and_font():
    from jarvis.design_studio.styles import resolve_style

    overrides = cover.CoverEditOverrides(
        background_color_1="#111111", background_color_2="#222222", headline_font="C:/Windows/Fonts/arial.ttf",
    )
    _, transform = cover.apply_cover_edit_overrides(cover.CoverText(title="T", supporting_text=""), overrides)
    new_style = transform(resolve_style("yoga"))
    assert new_style.background_color_1 == "#111111"
    assert new_style.background_color_2 == "#222222"
    assert new_style.headline_font == "C:/Windows/Fonts/arial.ttf"


def test_render_cover_with_overrides_top_position_produces_valid_image(tmp_path):
    cover_text = cover.CoverText(title="A Title", supporting_text="Sub")
    overrides = cover.CoverEditOverrides(text_position="top")
    result = cover.render_cover_with_overrides(_brief(), cover_text, overrides, output_path=tmp_path / "cover.jpg")
    assert result.output_path.is_file()
    with Image.open(result.output_path) as img:
        assert img.size == (1080, 1920)


def test_render_cover_with_overrides_bottom_position_produces_different_pixels(tmp_path):
    # A real, honest positional difference - "bottom" moves the title
    # into render_design()'s own pre-existing cta pill (see
    # CoverEditOverrides's own docstring) instead of the top headline
    # slot, so the two renders must be genuinely different images, not
    # just a documentation claim.
    import hashlib

    cover_text = cover.CoverText(title="A Title", supporting_text="")
    top_path = tmp_path / "top.jpg"
    bottom_path = tmp_path / "bottom.jpg"
    cover.render_cover_with_overrides(
        _brief(), cover_text, cover.CoverEditOverrides(text_position="top"), output_path=top_path,
    )
    cover.render_cover_with_overrides(
        _brief(), cover_text, cover.CoverEditOverrides(text_position="bottom"), output_path=bottom_path,
    )
    with Image.open(top_path) as a, Image.open(bottom_path) as b:
        a_bytes = a.convert("RGB").tobytes()
        b_bytes = b.convert("RGB").tobytes()
    assert hashlib.sha256(a_bytes).digest() != hashlib.sha256(b_bytes).digest()


def test_render_cover_with_overrides_default_position_is_top():
    cover_text = cover.CoverText(title="A Title", supporting_text="")
    # No text_position set at all - must match DEFAULT_TEXT_POSITION ("top").
    overrides = cover.CoverEditOverrides()
    assert (overrides.text_position or cover.DEFAULT_TEXT_POSITION) == "top"


def test_render_cover_with_overrides_wraps_render_error(tmp_path):
    from jarvis.design_studio.render import RenderError

    with patch.object(cover, "render_design", side_effect=RenderError("simulated failure")):
        with pytest.raises(cover.CoverError, match="simulated failure"):
            cover.render_cover_with_overrides(
                _brief(), cover.CoverText(title="X", supporting_text=""), cover.CoverEditOverrides(),
                output_path=tmp_path / "cover.jpg",
            )
