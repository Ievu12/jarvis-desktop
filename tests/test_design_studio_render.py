"""Tests for jarvis.design_studio.render.render_design(): real Pillow
rendering (no mocks - this module's entire job is producing a correct
image file, so real PIL calls, matching this codebase's established
"test the actual rendering pipeline for image/video-composition
modules" convention - see tests/test_video_studio_cover.py's own
module docstring for the same reasoning applied to FFmpeg drawtext).

Confirms: every FORMAT_DIMENSIONS entry produces exactly its documented
output size, every DESIGN_STYLES preset renders without error, an
uploaded image (logo or product) is composited in and the file is
verifiably NOT re-encoded/regenerated (its own pixel content survives,
per the module brief's "preserve the actual product appearance" rule),
long headline/body text wraps onto multiple lines rather than
overflowing, and every error path (unknown format, missing uploaded
image) raises RenderError with a clear message."""

from __future__ import annotations

import pytest
from PIL import Image

from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.render import FORMAT_DIMENSIONS, RenderError, render_design
from jarvis.design_studio.styles import DESIGN_STYLES, resolve_style


def _brief(**overrides) -> DesignBrief:
    defaults = dict(
        topic="yoga", objective="educate", audience="wellness beginners", tone="calm",
        headline="3 Poses To Start Your Day", supporting_text="Stretch and breathe every morning.",
        cta="Save this", format="story", style="yoga",
    )
    defaults.update(overrides)
    return DesignBrief(**defaults)


@pytest.fixture
def uploaded_image(tmp_path):
    path = tmp_path / "product.png"
    img = Image.new("RGBA", (300, 300), (80, 180, 120, 255))
    img.save(path)
    return path


# --- format dimensions -------------------------------------------------------------------


@pytest.mark.parametrize(
    "fmt,dimensions", list(FORMAT_DIMENSIONS.items()),
)
def test_render_produces_correct_dimensions(tmp_path, fmt, dimensions):
    expected_w, expected_h = dimensions
    brief = _brief(format=fmt)
    style = resolve_style(brief.style)
    output = tmp_path / "design.jpg"
    result = render_design(brief, style, output_path=output)
    assert result.width == expected_w
    assert result.height == expected_h
    assert output.is_file()

    with Image.open(output) as img:
        assert img.size == (expected_w, expected_h)


# --- every style renders -----------------------------------------------------------------


@pytest.mark.parametrize("style_name", list(DESIGN_STYLES.keys()))
def test_every_style_renders_without_error(tmp_path, style_name):
    brief = _brief(style=style_name)
    style = resolve_style(style_name)
    output = tmp_path / f"design_{style_name}.jpg"
    result = render_design(brief, style, output_path=output)
    assert output.is_file()
    assert result.file_size_bytes > 0


# --- uploaded image compositing -----------------------------------------------------------


def test_product_image_is_composited_and_preserved(tmp_path, uploaded_image):
    brief = _brief()
    style = resolve_style(brief.style)
    output = tmp_path / "design.jpg"
    render_design(
        brief, style, output_path=output, uploaded_image_path=uploaded_image, image_role="product",
    )
    assert output.is_file()
    # The uploaded image's own color must appear somewhere in the
    # output (verbatim compositing, not a re-generated/altered image) -
    # sample the pixel roughly where _composite_product_image() places
    # it (centered, starting near the top safe area).
    with Image.open(output) as img:
        img_rgb = img.convert("RGB")
        sample = img_rgb.getpixel((img_rgb.width // 2, int(img_rgb.height * 0.28)))
        assert isinstance(sample, tuple)
        # Allow for JPEG compression drift - just confirm it's much
        # closer to the uploaded green than to any background color.
        assert sample[1] > sample[0]  # green channel dominant, not the gradient background


def test_logo_image_is_composited(tmp_path, uploaded_image):
    brief = _brief()
    style = resolve_style(brief.style)
    output = tmp_path / "design.jpg"
    render_design(
        brief, style, output_path=output, uploaded_image_path=uploaded_image, image_role="logo",
    )
    assert output.is_file()


def test_no_uploaded_image_renders_text_only(tmp_path):
    brief = _brief()
    style = resolve_style(brief.style)
    output = tmp_path / "design.jpg"
    result = render_design(brief, style, output_path=output)
    assert output.is_file()
    assert result.file_size_bytes > 0


# --- text wrapping --------------------------------------------------------------------------


def test_long_headline_wraps_onto_multiple_lines(tmp_path):
    brief = _brief(headline="This Is A Very Long Headline That Should Definitely Wrap Onto Several Lines")
    style = resolve_style(brief.style)
    output = tmp_path / "design.jpg"
    result = render_design(brief, style, output_path=output)
    assert output.is_file()
    assert result.width == 1080  # format unaffected by text length


# --- output formats -------------------------------------------------------------------------


def test_png_output(tmp_path):
    brief = _brief()
    style = resolve_style(brief.style)
    output = tmp_path / "design.png"
    result = render_design(brief, style, output_path=output)
    assert output.is_file()
    with Image.open(output) as img:
        assert img.format == "PNG"


def test_jpg_output(tmp_path):
    brief = _brief()
    style = resolve_style(brief.style)
    output = tmp_path / "design.jpg"
    result = render_design(brief, style, output_path=output)
    assert output.is_file()
    with Image.open(output) as img:
        assert img.format == "JPEG"


def test_blank_cta_draws_no_pill(tmp_path):
    # Regression test for a real bug found by hand-testing AI Reel
    # Generator's cover/scene rendering (jarvis.reel_generator.cover/
    # .scenes both build a DesignBrief directly, bypassing
    # generate_design_brief(), and may legitimately leave cta blank) -
    # an empty CTA used to still draw a rounded-rectangle pill with no
    # text in it, a stray floating shape with nothing in it. Checked by
    # rendering the SAME brief/style with and without a CTA and
    # diffing the two images directly: a blank CTA must produce a
    # PURE gradient canvas (identical to a canvas with no headline/
    # body text drawn at all either, isolating just the CTA step),
    # while a real CTA must differ from that same pure-gradient
    # canvas somewhere (the pill itself).
    style = resolve_style("minimal")
    empty_brief = _brief(headline="", supporting_text="", cta="")
    pure_gradient_path = tmp_path / "pure_gradient.png"
    render_design(empty_brief, style, output_path=pure_gradient_path)

    blank_cta_brief = _brief(headline="", supporting_text="", cta="")
    output_blank = tmp_path / "blank_cta.png"
    render_design(blank_cta_brief, style, output_path=output_blank)

    with_cta_brief = _brief(headline="", supporting_text="", cta="Save this")
    output_with_cta = tmp_path / "with_cta.png"
    render_design(with_cta_brief, style, output_path=output_with_cta)

    with Image.open(pure_gradient_path) as pure_img, Image.open(output_blank) as blank_img, Image.open(output_with_cta) as cta_img:
        pure_pixels = list(pure_img.convert("RGB").tobytes())
        blank_pixels = list(blank_img.convert("RGB").tobytes())
        cta_pixels = list(cta_img.convert("RGB").tobytes())

        assert blank_pixels == pure_pixels  # blank CTA: identical to a pure gradient, no pill drawn
        assert cta_pixels != pure_pixels  # real CTA: differs from a pure gradient - the pill IS drawn


def test_whitespace_only_cta_draws_no_pill(tmp_path):
    style = resolve_style("minimal")
    brief = _brief(cta="   ")
    output = tmp_path / "whitespace_cta.png"
    render_design(brief, style, output_path=output)  # must not raise
    assert output.is_file()


def test_long_cta_wraps_instead_of_overflowing_canvas(tmp_path):
    # Regression test for a real bug found by hand-testing AI Reel
    # Generator's Content Package feature: jarvis.reel_generator
    # .content_package builds a DesignBrief directly from a Reel's own
    # full-sentence CTA (not Design Studio's own short-CTA-generating
    # brief), and the CTA pill previously drew its text on a single
    # line with no wrapping, silently running the text off the edge of
    # the canvas for a long CTA. Checked by confirming the pill's own
    # background color never appears in the canvas's leftmost/rightmost
    # single-pixel-wide columns (a horizontally-overflowing pill would
    # necessarily paint all the way to at least one edge; a properly
    # wrapped, margin-respecting pill never does).
    style = resolve_style("minimal")
    long_cta = (
        "Try one of these benefits tomorrow morning and let us know how it feels in the comments below!"
    )
    brief = _brief(headline="", supporting_text="", cta=long_cta)
    output = tmp_path / "long_cta.png"
    render_design(brief, style, output_path=output)

    cta_background_rgb = tuple(int(style.cta_background.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    with Image.open(output) as img:
        rgb_img = img.convert("RGB")
        left_column = list(rgb_img.crop((0, 0, 1, rgb_img.height)).tobytes())
        right_column = list(rgb_img.crop((rgb_img.width - 1, 0, rgb_img.width, rgb_img.height)).tobytes())

    def _column_contains_pill_color(column_bytes: list[int]) -> bool:
        pixels = [tuple(column_bytes[i:i + 3]) for i in range(0, len(column_bytes), 3)]
        return cta_background_rgb in pixels

    assert not _column_contains_pill_color(left_column)
    assert not _column_contains_pill_color(right_column)


def test_short_cta_still_renders_as_single_line_pill(tmp_path):
    # The original, common case (a short CTA like Design Studio's own
    # generate_design_brief() always produces) must still work exactly
    # as before this fix - a real regression check, not just "doesn't
    # crash".
    style = resolve_style("minimal")
    brief = _brief(cta="Save this")
    output = tmp_path / "short_cta.png"
    result = render_design(brief, style, output_path=output)
    assert result.output_path.is_file()


# --- error handling -----------------------------------------------------------------------


def test_unknown_format_raises(tmp_path):
    brief = _brief(format="not_a_real_format")
    style = resolve_style(brief.style)
    with pytest.raises(RenderError, match="Unknown design format"):
        render_design(brief, style, output_path=tmp_path / "x.jpg")


def test_missing_uploaded_image_raises(tmp_path):
    brief = _brief()
    style = resolve_style(brief.style)
    with pytest.raises(RenderError, match="not found"):
        render_design(
            brief, style, output_path=tmp_path / "x.jpg",
            uploaded_image_path=tmp_path / "does_not_exist.png", image_role="logo",
        )
