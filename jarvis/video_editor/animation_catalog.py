"""Lithuanian names and categories for the text and sticker animations
(entrance, exit, motion, scale, fade, bounce, slide), as the settings
panel offers them. Only a way of browsing: the animations themselves
are text_overlay.TEXT_ANIMATION_CHOICES and
stickers.STICKER_ANIMATION_CHOICES, unchanged, so saved projects and
the export don't depend on this module."""

from __future__ import annotations

from jarvis.video_editor.stickers import STICKER_ANIMATION_CHOICES
from jarvis.video_editor.text_overlay import TEXT_ANIMATION_CHOICES

ANIMATION_CATEGORIES: tuple[str, ...] = ("all", "entrance", "exit", "motion", "scale", "fade", "bounce", "slide")
CATEGORY_LABELS: dict[str, str] = {
    "all": "Visos",
    "entrance": "Įėjimo",
    "exit": "Išėjimo",
    "motion": "Judėjimo",
    "scale": "Mastelio",
    "fade": "Išnykimo",
    "bounce": "Šokinėjimo",
    "slide": "Slinkimo",
}

TEXT_ANIMATION_LABELS: dict[str, str] = {
    "none": "Be animacijos",
    "fade": "Atsiradimas ir išnykimas",
    "typewriter": "Rašomoji mašinėlė",
    "pop_up": "Iššokimas",
    "pop_out": "Susitraukimas pabaigoje",
    "slide_in": "Įslinkimas",
    "slide_out": "Išslinkimas",
    "bounce": "Šokinėjimas",
    "shake": "Drebėjimas",
    "glitch": "Trikdžiai (glitch)",
    "glow": "Švytėjimas",
    "zoom": "Priartėjimas",
}
_TEXT_CATEGORIES: dict[str, tuple[str, ...]] = {
    "entrance": ("fade", "typewriter", "pop_up", "slide_in"),
    "exit": ("fade", "pop_out", "slide_out"),
    "motion": ("shake", "glitch", "glow", "bounce"),
    "scale": ("pop_up", "pop_out", "zoom"),
    "fade": ("fade",),
    "bounce": ("bounce",),
    "slide": ("slide_in", "slide_out"),
}

STICKER_ANIMATION_LABELS: dict[str, str] = {
    "none": "Be animacijos",
    "pop_in": "Iššokimas",
    "fade_in_out": "Atsiradimas ir išnykimas",
    "float": "Plaukiojimas",
    "spin": "Sukimasis",
    "blink": "Mirksėjimas",
    "bounce": "Šokinėjimas",
}
_STICKER_CATEGORIES: dict[str, tuple[str, ...]] = {
    "entrance": ("pop_in", "fade_in_out"),
    "exit": ("fade_in_out",),
    "motion": ("float", "spin", "blink", "bounce"),
    "scale": ("pop_in",),
    "fade": ("fade_in_out",),
    "bounce": ("bounce",),
    "slide": ("float",),
}


def _tables(kind: str) -> tuple[tuple[str, ...], dict[str, str], dict[str, tuple[str, ...]]]:
    if kind == "text":
        return TEXT_ANIMATION_CHOICES, TEXT_ANIMATION_LABELS, _TEXT_CATEGORIES
    return STICKER_ANIMATION_CHOICES, STICKER_ANIMATION_LABELS, _STICKER_CATEGORIES


def animations_in(kind: str, category: str) -> tuple[str, ...]:
    """`kind` "text" or "sticker": the animations listed under
    `category`, always starting with "none"."""
    choices, _labels, categories = _tables(kind)
    if category == "all" or category not in categories:
        return choices
    return ("none",) + categories[category]


def animation_label(kind: str, animation: str) -> str:
    return _tables(kind)[1].get(animation, animation)


def animation_from_label(kind: str, label: str) -> str:
    for animation, name in _tables(kind)[1].items():
        if name == label:
            return animation
    return label


def category_of(kind: str, animation: str) -> str:
    """The most specific category listing `animation` ("all" for
    "none") - bounce opens under Šokinėjimo, not under Judėjimo."""
    categories = _tables(kind)[2]
    for category in _MOST_SPECIFIC_FIRST:
        if animation in categories[category]:
            return category
    return "all"


_MOST_SPECIFIC_FIRST = ("bounce", "fade", "entrance", "exit", "scale", "motion", "slide")
