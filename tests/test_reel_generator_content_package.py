"""Tests for jarvis.reel_generator.content_package
.generate_content_package(): uses REAL jarvis.design_studio.render
.render_design() (Pillow, no external service, fast/deterministic) - no
mocking needed for the rendering pipeline itself. Confirms: all 5
package pieces render successfully by default with the correct
per-piece jarvis.design_studio.render.FORMAT_DIMENSIONS, every piece
shares the SAME headline/style text (consistent visual identity - the
module's own phrase), the Story CTA piece has no supporting text (CTA-
forward by design), one piece's failure doesn't block the others, a
subset of `pieces` can be requested, output files are named by piece,
and style_transform (the Brand Kit hook) is applied to every piece."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PIL import Image

from jarvis.design_studio.render import FORMAT_DIMENSIONS, RenderError
from jarvis.design_studio.styles import resolve_style
from jarvis.reel_generator.content_package import (
    PACKAGE_PIECES,
    _PIECE_TO_FORMAT,
    generate_content_package,
)


def test_all_pieces_render_successfully_by_default(tmp_path):
    package = generate_content_package(
        headline="3 Yoga Habits", supporting_text="Start today", cta="Save this",
        style="yoga", output_dir=tmp_path,
    )
    assert len(package.pieces) == len(PACKAGE_PIECES)
    assert package.all_succeeded is True
    for piece in package.pieces:
        assert piece.render_result is not None
        assert piece.render_result.output_path.is_file()


def test_each_piece_has_correct_format_dimensions(tmp_path):
    package = generate_content_package(
        headline="3 Yoga Habits", supporting_text="Start today", cta="Save this",
        style="yoga", output_dir=tmp_path,
    )
    for piece in package.pieces:
        assert piece.render_result is not None
        expected_w, expected_h = FORMAT_DIMENSIONS[_PIECE_TO_FORMAT[piece.piece]]
        with Image.open(piece.render_result.output_path) as img:
            assert img.size == (expected_w, expected_h)


def test_output_files_are_named_by_piece(tmp_path):
    package = generate_content_package(
        headline="Test", supporting_text="Test body", cta="Save this", style="minimal", output_dir=tmp_path,
    )
    for piece in package.pieces:
        assert piece.render_result is not None
        assert piece.render_result.output_path.name == f"{piece.piece}.jpg"


def test_story_cta_piece_has_no_supporting_text_drawn(tmp_path):
    # Compare the story_cta render against a pure-gradient render of the
    # same headline+cta+empty supporting_text (matching
    # test_design_studio_render.py's own "diff against a pure gradient"
    # regression-test technique) - confirms no supporting_text band is
    # drawn, i.e. the CTA card is genuinely CTA-forward, not just
    # "happens to look empty in this specific case".
    package = generate_content_package(
        headline="Test", supporting_text="This should not appear on the Story CTA card",
        cta="Save this", style="minimal", output_dir=tmp_path, pieces=("story_cta",),
    )
    story_cta_piece = package.pieces[0]
    assert story_cta_piece.render_result is not None

    from jarvis.design_studio.brief import DesignBrief
    from jarvis.design_studio.render import render_design

    reference_brief = DesignBrief(
        topic="Test", objective="promote", audience="", tone="", headline="Test",
        supporting_text="", cta="Save this", format="story", style="minimal",
    )
    reference_path = tmp_path / "reference.jpg"
    render_design(reference_brief, resolve_style("minimal"), output_path=reference_path)

    with Image.open(story_cta_piece.render_result.output_path) as actual_img, Image.open(reference_path) as reference_img:
        assert list(actual_img.convert("RGB").tobytes()) == list(reference_img.convert("RGB").tobytes())


def test_all_pieces_share_the_same_headline_text(tmp_path):
    # Pixel-identical headline rendering across formats isn't
    # guaranteed (different canvas sizes wrap differently) - instead,
    # confirm every piece was built from the SAME DesignBrief.headline
    # by checking each piece's own brief construction directly via the
    # private helper, which is what generate_content_package() actually
    # calls per piece.
    from jarvis.reel_generator.content_package import _piece_brief

    for piece_name in PACKAGE_PIECES:
        brief = _piece_brief(
            headline="3 Yoga Habits", supporting_text="Start today", cta="Save this",
            style="yoga", piece=piece_name,
        )
        assert brief.headline == "3 Yoga Habits"
        assert brief.style == "yoga"


def test_requesting_a_subset_of_pieces(tmp_path):
    package = generate_content_package(
        headline="Test", supporting_text="Body", cta="Save this", style="minimal",
        output_dir=tmp_path, pieces=("cover", "post"),
    )
    assert len(package.pieces) == 2
    assert {p.piece for p in package.pieces} == {"cover", "post"}


def test_one_piece_failure_does_not_block_the_others(tmp_path):
    from jarvis.reel_generator import content_package as cp_mod

    original_render_design = cp_mod.render_design
    call_count = {"n": 0}

    def _flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RenderError("simulated failure")
        return original_render_design(*args, **kwargs)

    with patch.object(cp_mod, "render_design", side_effect=_flaky):
        package = generate_content_package(
            headline="Test", supporting_text="Body", cta="Save this", style="minimal", output_dir=tmp_path,
        )

    failed = [p for p in package.pieces if p.error is not None]
    succeeded = [p for p in package.pieces if p.error is None]
    assert len(failed) == 1
    assert len(succeeded) == len(PACKAGE_PIECES) - 1
    assert package.all_succeeded is False
    assert package.any_succeeded is True
    error_message = failed[0].error
    assert error_message is not None
    assert "simulated failure" in error_message


def test_style_transform_is_applied_to_every_piece(tmp_path):
    applied_styles = []

    def _record_and_pass_through(style):
        applied_styles.append(style)
        return style

    generate_content_package(
        headline="Test", supporting_text="Body", cta="Save this", style="yoga",
        output_dir=tmp_path, style_transform=_record_and_pass_through,
    )
    assert len(applied_styles) == len(PACKAGE_PIECES)
    assert all(s == resolve_style("yoga") for s in applied_styles)


def test_piece_label_property():
    from jarvis.reel_generator.content_package import PackagePiece

    piece = PackagePiece(piece="story_promotion", render_result=None, error=None)
    assert piece.label == "Story Promotion"
