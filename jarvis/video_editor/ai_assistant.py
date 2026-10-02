"""AI creative assistant - Stage 5 of the "professional Reels editor"
plan (see jarvis.video_editor.reel_templates's own docstring for
Stage 4, the prior stage). Takes a short natural-language brief
("calm yoga reel with a soft affirmation") and asks the existing
jarvis.core.llm.LLMClient (the SAME client every other JARVIS AI
feature already uses - no new/separate paid API, per the user's own
explicit requirement) for ONE structured proposal: a motion/fade/color
effect, a caption style, an optional suggested caption line, an
optional text-template name, and a short list of sticker placements -
every one of these built ONLY from this package's own already-real
choices (jarvis.video_editor.effects.PHOTO_MOTION_CHOICES/FADE_CHOICES,
jarvis.video_editor.captions.CAPTION_POSITION_CHOICES/
CAPTION_ANIMATION_CHOICES, jarvis.video_editor.stickers
.STICKER_SHAPE_CHOICES/STICKER_ANIMATION_CHOICES,
jarvis.video_editor.text_templates.TEXT_TEMPLATE_NAMES), never a
free-form value the rest of this package doesn't already know how to
render.

Deliberately mirrors jarvis.core.commit_message's own established
shape for a one-shot, tool-free, history-free, isolated LLM call: no
tools, no multi-turn context, a dedicated system prompt (never
jarvis.core.llm.BASE_SYSTEM_PROMPT), and EVERY failure mode (missing
API key, network error, malformed/unparseable JSON, a field value
outside this package's own real choices) returns None rather than
raising - this feature is advisory only, so a slow/unavailable AI call
must never block the existing, AI-free editing flow.

Critically, and per the user's own explicit requirement ("peržiūros ir
patvirtinimo žingsnis prieš bet kokių pakeitimų pritaikymą" - a preview
and confirmation step before applying any changes): this module NEVER
applies anything to a Timeline or any GUI panel itself. It only
produces an AiReelProposal value object for the caller to show the
person; applying one (if accepted) reuses the exact same
jarvis.video_editor.reel_templates.apply_template()/
CaptionsPanel.apply_style()/StickersPanel.apply_presets() paths Stage
4's own "Apply Reel Template" action already uses and already tested -
an AI proposal and a curated ReelTemplate are applied through the
SAME mechanism, never a second one."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from jarvis.core.llm import LLMClient
from jarvis.video_editor.captions import (
    CAPTION_ANIMATION_CHOICES,
    CAPTION_POSITION_CHOICES,
    DEFAULT_CAPTION_COLOR,
    DEFAULT_CAPTION_FONT_SIZE,
    DEFAULT_CAPTION_HIGHLIGHT_COLOR,
    CaptionStyle,
)
from jarvis.video_editor.effects import FADE_CHOICES, PHOTO_MOTION_CHOICES, EffectSpec
from jarvis.video_editor.sticker_library import StickerPreset
from jarvis.video_editor.stickers import STICKER_ANIMATION_CHOICES, STICKER_SHAPE_CHOICES
from jarvis.video_editor.text_templates import TEXT_TEMPLATE_NAMES

_MAX_RESPONSE_TOKENS = 1000
# A structured proposal (one effect + one caption style + up to 3
# sticker placements + two short text fields) is small - this stays
# well under jarvis.core.commit_message's own precedent reasoning for
# why a bounded, purpose-sized budget beats the main agent's default.

_MAX_STICKER_SUGGESTIONS = 3
# A Reel is a short-form vertical video - more than a few stickers at
# once reads as cluttered regardless of what an AI suggests, so this
# cap is enforced on the PARSED result (never trusted from the model's
# own count), mirroring how jarvis.video_editor.reel_templates's own
# hand-authored templates never place more than 2.

_SYSTEM_PROMPT = (
    "You design short vertical social video (Instagram Reels/TikTok) styling "
    "proposals. You reply with ONLY a single JSON object - no markdown code "
    "fences, no explanation before or after it. Every field you choose MUST "
    "be one of the exact allowed values listed in the user's own prompt - "
    "never invent a new value, a close variant, or a value in a different "
    "case. If you are unsure, pick the closest allowed value rather than a "
    "made-up one."
)

_PROMPT_TEMPLATE = """Propose a styling bundle for this video idea: "{brief}"

Reply with ONLY this exact JSON shape (all fields required):
{{
  "motion": one of {motion_choices},
  "motion_intensity": a number from 1.0 to 1.5,
  "fade": one of {fade_choices},
  "fade_seconds": a number from 0.1 to 2.0,
  "brightness": a number from -0.3 to 0.3,
  "contrast": a number from 0.8 to 1.3,
  "saturation": a number from 0.7 to 1.3,
  "caption_position": one of {caption_position_choices},
  "caption_animation": one of {caption_animation_choices},
  "caption_color": a CSS color name or hex string like "white" or "#FFD166",
  "caption_highlight_color": a CSS color name or hex string,
  "text_template_name": one of {text_template_choices}, or null,
  "suggested_caption_text": a short (under 80 character) example caption line in the SAME language as the brief, or "",
  "music_mood_suggestion": a short (under 60 character) description of a fitting music mood/style (this is a text SUGGESTION only - no music file is chosen or applied automatically), or "",
  "stickers": a list of 0 to 3 objects, each {{"shape": one of {sticker_shape_choices}, "animation": one of {sticker_animation_choices}, "x_fraction": a number 0.0-1.0, "y_fraction": a number 0.0-1.0}}
}}"""


class AiAssistantError(Exception):
    """Raised ONLY by propose_reel_style() when the person explicitly
    wants to know WHY a proposal failed (e.g. a GUI "Retry" action
    showing the real reason) - propose_reel_style() itself still never
    raises this on its own; see that function's own docstring for why
    it returns None on every failure instead. Kept as a local,
    non-subclassed exception per this package's established
    per-module isolation convention."""


@dataclass(frozen=True)
class StickerSuggestion:
    shape: str
    animation: str
    x_fraction: float
    y_fraction: float

    def to_preset(self) -> StickerPreset:
        return StickerPreset(
            shape=self.shape, custom_path=None, x_fraction=self.x_fraction, y_fraction=self.y_fraction,
            size_fraction=0.12, rotation_degrees=0.0, opacity=1.0, animation=self.animation,
        )


@dataclass(frozen=True)
class AiReelProposal:
    """A complete, ALREADY-VALIDATED styling proposal - every field is
    guaranteed (by _parse_proposal() below) to be a real, existing
    choice this package already knows how to render, so the GUI can
    show/apply this directly without re-validating AI output itself.
    Built entirely from jarvis.video_editor.effects.EffectSpec/
    jarvis.video_editor.captions.CaptionStyle (the SAME dataclasses
    jarvis.video_editor.reel_templates.ReelTemplate already uses) -
    this is not a parallel/competing representation."""

    effect: EffectSpec
    caption_style: CaptionStyle
    text_template_name: str | None
    suggested_caption_text: str
    music_mood_suggestion: str
    stickers: tuple[StickerSuggestion, ...] = field(default_factory=tuple)


def _build_prompt(brief: str) -> str:
    return _PROMPT_TEMPLATE.format(
        brief=brief,
        motion_choices=list(PHOTO_MOTION_CHOICES),
        fade_choices=list(FADE_CHOICES),
        caption_position_choices=list(CAPTION_POSITION_CHOICES),
        caption_animation_choices=list(CAPTION_ANIMATION_CHOICES),
        text_template_choices=list(TEXT_TEMPLATE_NAMES),
        sticker_shape_choices=list(STICKER_SHAPE_CHOICES),
        sticker_animation_choices=list(STICKER_ANIMATION_CHOICES),
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _coerce_float(data: dict, key: str, default: float, low: float, high: float) -> float:
    raw = data.get(key, default)
    try:
        return _clamp(float(raw), low, high)
    except (TypeError, ValueError):
        return default


def _coerce_choice(data: dict, key: str, choices: tuple[str, ...], default: str) -> str:
    raw = data.get(key)
    return raw if isinstance(raw, str) and raw in choices else default


def _parse_sticker(raw: object) -> StickerSuggestion | None:
    if not isinstance(raw, dict):
        return None
    shape = raw.get("shape")
    animation = raw.get("animation")
    if shape not in STICKER_SHAPE_CHOICES or animation not in STICKER_ANIMATION_CHOICES:
        return None
    return StickerSuggestion(
        shape=shape, animation=animation,
        x_fraction=_coerce_float(raw, "x_fraction", 0.5, 0.0, 1.0),
        y_fraction=_coerce_float(raw, "y_fraction", 0.5, 0.0, 1.0),
    )


def _parse_proposal(raw_text: str) -> AiReelProposal | None:
    """Parses and FULLY validates the model's own JSON response - every
    field is coerced into this package's own real, existing range/
    choice, with a sane default substituted for anything missing,
    malformed, or outside that range (never trusting the model's own
    claimed value as-is, same discipline jarvis.video_editor.timeline
    .Timeline.validate()'s own "describe problems, don't throw"
    convention aims for, except here a bad field is silently replaced
    with a safe default rather than surfaced as a problem - a styling
    suggestion with one odd field defaulted is still useful; a raised
    error over one bad field would throw away an otherwise-good
    proposal entirely). Returns None only if `raw_text` isn't even
    parseable as a JSON object at all."""
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    effect = EffectSpec(
        motion=_coerce_choice(data, "motion", PHOTO_MOTION_CHOICES, "none"),
        motion_intensity=_coerce_float(data, "motion_intensity", 1.2, 1.0, 1.5),
        fade=_coerce_choice(data, "fade", FADE_CHOICES, "none"),
        fade_seconds=_coerce_float(data, "fade_seconds", 0.5, 0.1, 2.0),
        brightness=_coerce_float(data, "brightness", 0.0, -0.3, 0.3),
        contrast=_coerce_float(data, "contrast", 1.0, 0.8, 1.3),
        saturation=_coerce_float(data, "saturation", 1.0, 0.7, 1.3),
    )

    caption_color = data.get("caption_color")
    highlight_color = data.get("caption_highlight_color")
    caption_style = CaptionStyle(
        font_size=DEFAULT_CAPTION_FONT_SIZE,
        color=caption_color if isinstance(caption_color, str) and caption_color.strip() else DEFAULT_CAPTION_COLOR,
        highlight_color=highlight_color if isinstance(highlight_color, str) and highlight_color.strip() else DEFAULT_CAPTION_HIGHLIGHT_COLOR,
        position=_coerce_choice(data, "caption_position", CAPTION_POSITION_CHOICES, "bottom"),
        animation=_coerce_choice(data, "caption_animation", CAPTION_ANIMATION_CHOICES, "word_by_word"),
    )

    text_template_name = data.get("text_template_name")
    if text_template_name not in TEXT_TEMPLATE_NAMES:
        text_template_name = None

    suggested_text = data.get("suggested_caption_text")
    suggested_text = suggested_text.strip()[:120] if isinstance(suggested_text, str) else ""

    music_mood = data.get("music_mood_suggestion")
    music_mood = music_mood.strip()[:120] if isinstance(music_mood, str) else ""

    raw_stickers = data.get("stickers")
    stickers: list[StickerSuggestion] = []
    if isinstance(raw_stickers, list):
        for raw in raw_stickers[: _MAX_STICKER_SUGGESTIONS]:
            parsed = _parse_sticker(raw)
            if parsed is not None:
                stickers.append(parsed)

    return AiReelProposal(
        effect=effect, caption_style=caption_style, text_template_name=text_template_name,
        suggested_caption_text=suggested_text, music_mood_suggestion=music_mood, stickers=tuple(stickers),
    )


def propose_reel_style(llm: LLMClient, brief: str) -> AiReelProposal | None:
    """Asks the LLM for one styling proposal matching `brief` - returns
    None (never raises) for an empty brief, any LLM call failure
    (network/auth/rate-limit/SDK error), or a response that isn't even
    parseable as JSON, so a caller always has a simple "no suggestion
    available, fall back to manual editing or a hand-picked
    ReelTemplate" path, exactly mirroring
    jarvis.core.commit_message.suggest_commit_message()'s own
    established contract. The returned AiReelProposal, if any, is
    ALWAYS fully valid (every field already coerced into this
    package's own real choices by _parse_proposal() above) - nothing
    further needs validating before showing it to the person as a
    preview."""
    if not brief or not brief.strip():
        return None
    try:
        response = llm.send(
            [{"role": "user", "content": _build_prompt(brief.strip())}],
            [], max_tokens=_MAX_RESPONSE_TOKENS, system=_SYSTEM_PROMPT,
        )
    except Exception:
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return None
    return _parse_proposal(text)
