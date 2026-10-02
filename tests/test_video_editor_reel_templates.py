"""Tests for jarvis.video_editor.reel_templates: pure curated-data
lookups plus apply_template()'s own Timeline transformation - no
ffmpeg/GUI needed anywhere in this file."""

from __future__ import annotations

from jarvis.video_editor.captions import CAPTION_ANIMATION_CHOICES, CAPTION_POSITION_CHOICES
from jarvis.video_editor.reel_templates import (
    REEL_TEMPLATES,
    TEMPLATE_CATEGORY_CHOICES,
    TEMPLATE_CATEGORY_LABELS,
    TEMPLATE_NAMES,
    apply_template,
    get_reel_template,
    templates_in_category,
)
from jarvis.video_editor.stickers import STICKER_ANIMATION_CHOICES, STICKER_SHAPE_CHOICES
from jarvis.video_editor.text_templates import TEXT_TEMPLATE_NAMES
from jarvis.video_editor.timeline import ASPECT_RATIO_CHOICES, Timeline, TimelineClip, TimelineStill


def test_every_template_name_is_unique():
    names = [t.name for t in REEL_TEMPLATES]
    assert len(names) == len(set(names))


def test_template_names_tuple_matches_templates():
    assert TEMPLATE_NAMES == tuple(t.name for t in REEL_TEMPLATES)


def test_get_reel_template_returns_the_matching_template():
    template = get_reel_template("Glow Up")
    assert template is not None
    assert template.category == "cosmetics_ads"


def test_get_reel_template_returns_none_for_unknown_name():
    assert get_reel_template("Does Not Exist") is None


def test_every_category_has_a_label():
    for category in TEMPLATE_CATEGORY_CHOICES:
        assert category in TEMPLATE_CATEGORY_LABELS
        assert TEMPLATE_CATEGORY_LABELS[category]


def test_every_requested_category_has_at_least_one_template():
    # The user's own Stage 4 ask named these 6 categories explicitly.
    required = (
        "yoga_meditation", "cosmetics_ads", "autumn_winter",
        "daily_affirmations", "quotes_reflections", "instagram_reels",
    )
    for category in required:
        assert len(templates_in_category(category)) >= 1, category


def test_templates_in_category_only_returns_that_category():
    for template in templates_in_category("cosmetics_ads"):
        assert template.category == "cosmetics_ads"


def test_templates_in_category_returns_empty_for_unknown_category():
    assert templates_in_category("not_a_real_category") == ()


def test_every_template_has_a_valid_aspect_ratio():
    for template in REEL_TEMPLATES:
        assert template.aspect_ratio in ASPECT_RATIO_CHOICES


def test_every_template_default_effect_validates_cleanly():
    for template in REEL_TEMPLATES:
        assert template.default_effect.validate() == [], template.name


def test_every_template_caption_style_uses_real_choices():
    for template in REEL_TEMPLATES:
        assert template.caption_style.position in CAPTION_POSITION_CHOICES
        assert template.caption_style.animation in CAPTION_ANIMATION_CHOICES


def test_every_template_text_template_name_resolves_if_set():
    for template in REEL_TEMPLATES:
        if template.text_template_name is not None:
            assert template.text_template_name in TEXT_TEMPLATE_NAMES


def test_every_template_sticker_preset_uses_real_shapes_and_animations():
    for template in REEL_TEMPLATES:
        for preset in template.sticker_presets:
            assert preset.shape in STICKER_SHAPE_CHOICES
            assert preset.animation in STICKER_ANIMATION_CHOICES


def test_every_category_choice_is_used_by_at_least_one_template():
    used = {t.category for t in REEL_TEMPLATES}
    assert used == set(TEMPLATE_CATEGORY_CHOICES)


def test_apply_template_sets_the_aspect_ratio():
    template = get_reel_template("Square Feed Post")
    timeline = Timeline(items=(TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),))
    result = apply_template(timeline, template)
    assert result.aspect_ratio == "1:1"


def test_apply_template_sets_every_items_effect():
    template = get_reel_template("Glow Up")
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=3.0),
    ))
    result = apply_template(timeline, template)
    assert all(item.effect == template.default_effect for item in result.items)


def test_apply_template_never_touches_clip_identity_order_or_trim_points():
    template = get_reel_template("Cozy Autumn")
    clip = TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=1.5, source_out_seconds=4.5, speed_factor=1.5)
    timeline = Timeline(items=(clip,))
    result = apply_template(timeline, template)
    result_clip = result.items[0]
    assert result_clip.clip_id == clip.clip_id
    assert result_clip.media_item_id == clip.media_item_id
    assert result_clip.source_in_seconds == clip.source_in_seconds
    assert result_clip.source_out_seconds == clip.source_out_seconds
    assert result_clip.speed_factor == clip.speed_factor


def test_apply_template_on_an_empty_timeline_never_raises():
    template = get_reel_template("Calm Flow")
    result = apply_template(Timeline(), template)
    assert result.items == ()
    assert result.aspect_ratio == template.aspect_ratio


def test_apply_template_result_still_validates_cleanly():
    template = get_reel_template("Morning Mantra")
    timeline = Timeline(items=(TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),))
    result = apply_template(timeline, template)
    assert result.validate() == []
