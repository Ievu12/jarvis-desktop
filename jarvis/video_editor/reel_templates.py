"""Ready-made Reels templates - Stage 4 of the "professional Reels
editor" plan (see jarvis.video_editor.timeline_history's own docstring
for Stage 3, the prior stage this session continued from).

A ReelTemplate is a curated BUNDLE of the style/placement mechanisms
this package already built, never a new mechanism of its own:
- `aspect_ratio` + a default per-item `EffectSpec` (Ken Burns motion/
  fade/color) -> applied onto an existing Timeline's own items via
  apply_template() below, which NEVER touches clip in/out points,
  ordering, or which media is used (that stays the person's own
  editing choice - a template only ever supplies STYLE defaults, same
  "style only, never timing/placement" rule
  jarvis.video_editor.sticker_library.StickerPreset and
  jarvis.video_editor.text_templates.TextTemplate already establish).
- a `CaptionStyle` (font/color/position/animation for transcribed
  subtitles).
- a `text_template_name` (looked up via
  jarvis.video_editor.text_templates.get_text_template() by the GUI -
  this module only stores the NAME, never a second copy of that
  dataclass, so the two modules' own curated data can never drift).
- `sticker_presets` (tuple of jarvis.video_editor.sticker_library
  .StickerPreset - style/placement only, no timing; the GUI assigns
  real start/end seconds when it actually places one via
  StickerPreset.to_sticker_instance()).
- `suggested_caption_text` (one short, honestly-labeled example line
  for a "daily affirmations"/"quotes" style template, shown as a
  starting point the person edits - NEVER auto-inserted into an
  export; see apply_template()'s own docstring for why this field is
  never applied automatically).

Every template here is real, hand-authored data - no AI call, no
external asset fetch (the user's own prior "Nekurk fiktyvių išorinių
GIF bibliotekos nuorodų" instruction for the sticker library stage
applies with equal force here: every sticker_presets entry below uses
only the already-existing, locally-rendered built-in shapes from
jarvis.video_editor.stickers, never an invented external link)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from jarvis.video_editor.captions import CaptionStyle
from jarvis.video_editor.effects import EffectSpec
from jarvis.video_editor.sticker_library import StickerPreset
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, apply_effect_to_every_item

@dataclass(frozen=True)
class ReelTemplate:
    name: str
    category: str
    description: str
    aspect_ratio: str = "9:16"
    default_effect: EffectSpec = field(default_factory=EffectSpec)
    caption_style: CaptionStyle = field(default_factory=CaptionStyle)
    text_template_name: str | None = None
    sticker_presets: tuple[StickerPreset, ...] = field(default_factory=tuple)
    suggested_caption_text: str = ""


TEMPLATE_CATEGORY_CHOICES: tuple[str, ...] = (
    "yoga_meditation", "cosmetics_ads", "autumn_winter",
    "daily_affirmations", "quotes_reflections", "instagram_reels",
)
TEMPLATE_CATEGORY_LABELS: dict[str, str] = {
    "yoga_meditation": "🧘 Yoga & Meditation",
    "cosmetics_ads": "💄 Cosmetics Ads",
    "autumn_winter": "🍂 Autumn & Winter",
    "daily_affirmations": "☀️ Daily Affirmations",
    "quotes_reflections": "📖 Quotes & Reflections",
    "instagram_reels": "📱 Instagram Stories/Reels",
}


REEL_TEMPLATES: tuple[ReelTemplate, ...] = (
    ReelTemplate(
        name="Calm Flow",
        category="yoga_meditation",
        description="Slow zoom, soft fade, gentle word-by-word captions - for yoga/meditation clips.",
        default_effect=EffectSpec(motion="zoom_in", motion_intensity=1.15, fade="fade_both", fade_seconds=0.8, saturation=0.9),
        caption_style=CaptionStyle(font_size=56, color="white", position="bottom", animation="word_by_word", background=False, outline_width=1),
        text_template_name="Soft Zoom",
        sticker_presets=(
            StickerPreset(shape="om_circle", custom_path=None, x_fraction=0.85, y_fraction=0.12, size_fraction=0.1, rotation_degrees=0.0, opacity=0.85, animation="fade_in_out"),
        ),
    ),
    ReelTemplate(
        name="Mindful Pause",
        category="yoga_meditation",
        description="Static frame, slow fade, centered affirmation-style text - for breathing/stillness moments.",
        default_effect=EffectSpec(motion="none", fade="fade_both", fade_seconds=1.0),
        caption_style=CaptionStyle(font_size=52, color="white", position="center", animation="none", background=True),
        text_template_name="Clean Title",
        sticker_presets=(
            StickerPreset(shape="lotus", custom_path=None, x_fraction=0.5, y_fraction=0.85, size_fraction=0.12, rotation_degrees=0.0, opacity=0.9, animation="fade_in_out"),
        ),
    ),
    ReelTemplate(
        name="Glow Up",
        category="cosmetics_ads",
        description="Punchy zoom-in, bright color boost, bold pop-up captions - for beauty/cosmetics product shots.",
        default_effect=EffectSpec(motion="zoom_in", motion_intensity=1.4, fade="fade_in", fade_seconds=0.3, brightness=0.05, contrast=1.15, saturation=1.2),
        caption_style=CaptionStyle(font_size=72, color="white", highlight_color="#FF6FA8", position="bottom", animation="pop", background=True),
        text_template_name="Bold Reveal",
        sticker_presets=(
            StickerPreset(shape="lipstick", custom_path=None, x_fraction=0.82, y_fraction=0.15, size_fraction=0.13, rotation_degrees=15.0, opacity=1.0, animation="pop_in"),
            StickerPreset(shape="sparkle", custom_path=None, x_fraction=0.15, y_fraction=0.2, size_fraction=0.08, rotation_degrees=0.0, opacity=1.0, animation="blink"),
        ),
    ),
    ReelTemplate(
        name="Beauty Shelf",
        category="cosmetics_ads",
        description="Slow pan across product, elegant slide-in captions - for flat-lay/shelf cosmetics shots.",
        default_effect=EffectSpec(motion="pan_right", motion_intensity=1.2, fade="fade_in", fade_seconds=0.4, contrast=1.1, saturation=1.1),
        caption_style=CaptionStyle(font_size=60, color="white", position="bottom", animation="slide", background=True),
        text_template_name="Elegant Slide",
        sticker_presets=(
            StickerPreset(shape="mirror", custom_path=None, x_fraction=0.85, y_fraction=0.85, size_fraction=0.12, rotation_degrees=0.0, opacity=0.9, animation="pop_in"),
        ),
    ),
    ReelTemplate(
        name="Cozy Autumn",
        category="autumn_winter",
        description="Warm color grade, gentle pan, falling-leaf stickers - for seasonal autumn content.",
        default_effect=EffectSpec(motion="pan_down", motion_intensity=1.2, fade="fade_both", fade_seconds=0.5, brightness=0.02, contrast=1.05, saturation=1.1),
        caption_style=CaptionStyle(font_size=58, color="#FFE8C2", position="bottom", animation="word_by_word", background=True),
        text_template_name="Clean Title",
        sticker_presets=(
            StickerPreset(shape="autumn_leaf", custom_path=None, x_fraction=0.15, y_fraction=0.15, size_fraction=0.12, rotation_degrees=-20.0, opacity=0.95, animation="fade_in_out"),
            StickerPreset(shape="pumpkin", custom_path=None, x_fraction=0.85, y_fraction=0.85, size_fraction=0.14, rotation_degrees=0.0, opacity=1.0, animation="pop_in"),
        ),
    ),
    ReelTemplate(
        name="Winter Frost",
        category="autumn_winter",
        description="Cool color grade, slow zoom, snowflake stickers - for seasonal winter content.",
        default_effect=EffectSpec(motion="zoom_out", motion_intensity=1.15, fade="fade_both", fade_seconds=0.6, brightness=0.03, saturation=0.9),
        caption_style=CaptionStyle(font_size=58, color="#E6F2FF", position="bottom", animation="word_by_word", background=True),
        text_template_name="Soft Zoom",
        sticker_presets=(
            StickerPreset(shape="snowflake", custom_path=None, x_fraction=0.15, y_fraction=0.15, size_fraction=0.1, rotation_degrees=0.0, opacity=0.9, animation="blink"),
            StickerPreset(shape="mitten", custom_path=None, x_fraction=0.85, y_fraction=0.85, size_fraction=0.13, rotation_degrees=0.0, opacity=1.0, animation="pop_in"),
        ),
    ),
    ReelTemplate(
        name="Morning Mantra",
        category="daily_affirmations",
        description="Bright, centered, bouncy text reveal - for a short daily affirmation line.",
        default_effect=EffectSpec(motion="zoom_in", motion_intensity=1.1, fade="fade_in", fade_seconds=0.4, brightness=0.04),
        caption_style=CaptionStyle(font_size=66, color="white", position="center", animation="pop", background=False, outline_width=2),
        text_template_name="Energetic Bounce",
        suggested_caption_text="Today I choose peace and progress.",
        sticker_presets=(
            StickerPreset(shape="sun", custom_path=None, x_fraction=0.85, y_fraction=0.15, size_fraction=0.12, rotation_degrees=0.0, opacity=1.0, animation="pop_in"),
        ),
    ),
    ReelTemplate(
        name="Quiet Reflection",
        category="quotes_reflections",
        description="Minimal motion, elegant typewriter reveal - for a longer quote or reflection line.",
        default_effect=EffectSpec(motion="none", fade="fade_both", fade_seconds=0.8, saturation=0.95),
        caption_style=CaptionStyle(font_size=50, color="white", position="center", animation="none", background=False, outline_width=1, shadow_offset=2),
        text_template_name="Typewriter Note",
        suggested_caption_text="\"The quieter you become, the more you can hear.\"",
    ),
    ReelTemplate(
        name="Bold Quote",
        category="quotes_reflections",
        description="High-contrast background, large karaoke-highlighted quote text.",
        default_effect=EffectSpec(motion="zoom_in", motion_intensity=1.1, fade="fade_in", fade_seconds=0.3, contrast=1.1),
        caption_style=CaptionStyle(font_size=70, color="white", highlight_color="#FFD166", position="center", animation="karaoke", background=True),
        text_template_name="Bold Reveal",
        suggested_caption_text="Small steps every day lead to big change.",
    ),
    ReelTemplate(
        name="Story Highlight",
        category="instagram_reels",
        description="General-purpose Instagram Stories/Reels look - balanced zoom, clean bottom captions.",
        aspect_ratio="9:16",
        default_effect=EffectSpec(motion="zoom_in", motion_intensity=1.2, fade="fade_in", fade_seconds=0.3),
        caption_style=CaptionStyle(font_size=62, color="white", position="bottom", animation="word_by_word", background=True),
        text_template_name="Clean Title",
    ),
    ReelTemplate(
        name="Square Feed Post",
        category="instagram_reels",
        description="1:1 feed-post framing with centered, high-energy captions.",
        aspect_ratio="1:1",
        default_effect=EffectSpec(motion="zoom_in", motion_intensity=1.15, fade="fade_in", fade_seconds=0.3),
        caption_style=CaptionStyle(font_size=58, color="white", position="center", animation="pop", background=True),
        text_template_name="Bold Reveal",
    ),
)

TEMPLATE_NAMES: tuple[str, ...] = tuple(t.name for t in REEL_TEMPLATES)
_TEMPLATES_BY_NAME: dict[str, ReelTemplate] = {t.name: t for t in REEL_TEMPLATES}


def get_reel_template(name: str) -> ReelTemplate | None:
    return _TEMPLATES_BY_NAME.get(name)


def templates_in_category(category: str) -> tuple[ReelTemplate, ...]:
    return tuple(t for t in REEL_TEMPLATES if t.category == category)


def apply_template(timeline: Timeline, template: ReelTemplate) -> Timeline:
    """Returns a NEW Timeline with `aspect_ratio` and every item's own
    `effect` replaced by the template's defaults - clip/still
    identity, order, in/out trim points, speed, and transitions are
    NEVER touched (a template only ever supplies STYLE defaults, per
    this module's own docstring). The template's caption_style/
    text_template_name/sticker_presets/suggested_caption_text are
    deliberately NOT applied here - those are handed back separately
    by the GUI's own "Apply Template" action to the CaptionsPanel/
    TextOverlayPanel/StickersPanel, which already have their own real
    validate()-before-commit paths; silently writing into three
    unrelated panels' own internal state from inside this one pure
    function would bypass that validation and couple this module to
    every GUI panel's own internals. Never raises - an empty timeline
    is returned unchanged (nothing to apply an effect to)."""
    with_effect = apply_effect_to_every_item(timeline, template.default_effect)
    return dataclasses.replace(with_effect, aspect_ratio=template.aspect_ratio)
