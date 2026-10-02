"""Tests for jarvis.design_studio.brand_kit: the single-row Brand Kit
preferences store and apply_brand_kit()'s style-override logic.
Redirected to a per-test tmp_path database file - no test touches the
real .jarvis/design_studio.db. Confirms: an unsaved Brand Kit is
all-None/empty (never a fabricated default), save/get round-trips
every field, reset_brand_kit() clears everything, apply_brand_kit() is
a true no-op when nothing is configured, only configured fields
override the base style, and - the real legibility bug found by
hand-testing this module's own rendered output - the CTA pill's
background is NEVER set to the same color as the page background, and
its text color always contrasts against whatever CTA background ends
up in place."""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.design_studio import brand_kit as bk
from jarvis.design_studio.styles import resolve_style


@pytest.fixture(autouse=True)
def _isolated_db_file(tmp_path, monkeypatch):
    db_file = tmp_path / "design_studio.db"
    monkeypatch.setattr(bk, "DESIGN_STUDIO_DB_FILE", db_file)
    return db_file


def _kit(**overrides: Any) -> bk.BrandKit:
    defaults: dict[str, Any] = dict(
        brand_name="Glow Co", logo_path="/some/logo.png", primary_color="#FF6B9D",
        secondary_color="#2D2D2D", accent_color="#FFD93D", preferred_fonts=[],
        preferred_style="wellness", instagram_username="glowco",
    )
    defaults.update(overrides)
    return bk.BrandKit(**defaults)


# --- get/save/reset ----------------------------------------------------------------------


def test_get_brand_kit_default_is_all_none_and_not_configured(_isolated_db_file):
    kit = bk.get_brand_kit()
    assert kit.brand_name is None
    assert kit.primary_color is None
    assert kit.is_configured is False


def test_save_and_get_round_trips_every_field():
    kit = _kit()
    bk.save_brand_kit(kit)
    loaded = bk.get_brand_kit()
    assert loaded == kit


def test_save_overwrites_previous_values():
    bk.save_brand_kit(_kit(brand_name="First"))
    bk.save_brand_kit(_kit(brand_name="Second"))
    loaded = bk.get_brand_kit()
    assert loaded.brand_name == "Second"


def test_is_configured_true_with_only_one_field_set():
    bk.save_brand_kit(bk.BrandKit(
        brand_name="Just A Name", logo_path=None, primary_color=None, secondary_color=None,
        accent_color=None, preferred_fonts=[], preferred_style=None, instagram_username=None,
    ))
    assert bk.get_brand_kit().is_configured is True


def test_reset_brand_kit_clears_everything():
    bk.save_brand_kit(_kit())
    bk.reset_brand_kit()
    loaded = bk.get_brand_kit()
    assert loaded.is_configured is False
    assert loaded.brand_name is None


def test_db_file_created_on_first_save(_isolated_db_file):
    assert not _isolated_db_file.exists()
    bk.save_brand_kit(_kit())
    assert _isolated_db_file.exists()


# --- apply_brand_kit ---------------------------------------------------------------------


def test_apply_unconfigured_brand_kit_is_a_no_op():
    style = resolve_style("minimal")
    empty_kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color=None, secondary_color=None,
        accent_color=None, preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, empty_kit)
    assert result == style


def test_apply_only_overrides_configured_fields():
    style = resolve_style("minimal")
    original_body_color = style.body_color
    kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color="#123456", secondary_color=None,
        accent_color=None, preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, kit)
    assert result.background_color_1 == "#123456"
    assert result.background_color_2 == "#123456"
    assert result.body_color == original_body_color  # untouched - not configured


def test_apply_primary_color_overrides_background_and_never_the_cta():
    # Regression test for a real bug found by hand-testing this
    # module's own rendered output: setting cta_background to
    # primary_color made the CTA pill invisible against the identical-
    # colored page background - fixed by using accent/secondary color
    # for the CTA pill instead of primary_color.
    style = resolve_style("minimal")
    kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color="#FF6B9D", secondary_color=None,
        accent_color=None, preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, kit)
    assert result.background_color_1 == "#FF6B9D"
    assert result.cta_background != "#FF6B9D"  # never the same as the page background


def test_apply_accent_color_is_used_for_cta_pill():
    style = resolve_style("minimal")
    kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color="#FF6B9D", secondary_color=None,
        accent_color="#FFD93D", preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, kit)
    assert result.cta_background == "#FFD93D"


def test_apply_falls_back_to_secondary_color_for_cta_when_no_accent():
    style = resolve_style("minimal")
    kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color="#FF6B9D", secondary_color="#2D2D2D",
        accent_color=None, preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, kit)
    assert result.cta_background == "#2D2D2D"


def test_apply_secondary_color_overrides_headline():
    style = resolve_style("minimal")
    kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color=None, secondary_color="#2D2D2D",
        accent_color=None, preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, kit)
    assert result.headline_color == "#2D2D2D"


# --- CTA text contrast ---------------------------------------------------------------------


def test_cta_text_color_contrasts_against_bright_cta_background():
    style = resolve_style("minimal")
    kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color=None, secondary_color=None,
        accent_color="#FFD93D",  # bright yellow
        preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, kit)
    assert result.cta_color == "#000000"  # black text on bright yellow


def test_cta_text_color_contrasts_against_dark_cta_background():
    style = resolve_style("minimal")
    kit = bk.BrandKit(
        brand_name=None, logo_path=None, primary_color=None, secondary_color=None,
        accent_color="#1A1A1A",  # near-black
        preferred_fonts=[], preferred_style=None, instagram_username=None,
    )
    result = bk.apply_brand_kit(style, kit)
    assert result.cta_color == "#FFFFFF"  # white text on dark background


def test_readable_text_color_function_directly():
    assert bk._readable_text_color("#FFFFFF") == "#000000"
    assert bk._readable_text_color("#000000") == "#FFFFFF"
    assert bk._readable_text_color("#FFD93D") == "#000000"  # bright yellow
    assert bk._readable_text_color("#0D0D0D") == "#FFFFFF"  # near-black
