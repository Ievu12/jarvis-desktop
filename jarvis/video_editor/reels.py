"""Instagram Reels mode: the data behind the Reels layers drawn on top of
the timeline - big animated word-by-word subtitles first (later stages
add pop-up text cards, picture-in-picture inserts and sound effects to
ReelsLayers).

Every Reels layer is drawn by ONE Pillow renderer
(jarvis.video_editor.reels_render) for both the live preview and the
export (jarvis.video_editor.reels_export turns the same drawing into a
transparent overlay video that ffmpeg puts on top of the timeline), so
the exported MP4 matches the preview by construction instead of by two
implementations agreeing.

Sizes are pixels of the 1080-wide export canvas (1080 x 1920 for 9:16)
and positions are fractions of the canvas, the same conventions
text_overlay.TextOverlay and stickers.StickerInstance use. Times are
seconds of the assembled timeline."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Literal

from jarvis.video_editor import text_render

REELS_CANVAS_WIDTH = 1080
REELS_CANVAS_HEIGHT = 1920
REELS_ASPECT_RATIO = "9:16"

# --- subtitles -----------------------------------------------------------------------------

PhraseAnimation = Literal["none", "pop", "fade", "slide_up", "zoom"]
PHRASE_ANIMATION_CHOICES: tuple[PhraseAnimation, ...] = ("pop", "fade", "slide_up", "zoom", "none")
PHRASE_ANIMATION_LABELS: dict[str, str] = {
    "pop": "Iššokimas",
    "fade": "Atsiradimas",
    "slide_up": "Įslinkimas iš apačios",
    "zoom": "Priartėjimas",
    "none": "Be animacijos",
}

WordReveal = Literal["phrase", "word", "word_pop"]
WORD_REVEAL_CHOICES: tuple[WordReveal, ...] = ("phrase", "word", "word_pop")
WORD_REVEAL_LABELS: dict[str, str] = {
    "phrase": "Visa frazė iš karto",
    "word": "Žodis po žodžio",
    "word_pop": "Žodis po žodžio su iššokimu",
}

ActiveEffect = Literal["none", "color", "box", "scale", "bounce", "underline"]
ACTIVE_EFFECT_CHOICES: tuple[ActiveEffect, ...] = ("color", "box", "scale", "bounce", "underline", "none")
ACTIVE_EFFECT_LABELS: dict[str, str] = {
    "color": "Kita spalva",
    "box": "Spalvotas fonas",
    "scale": "Padidėja",
    "bounce": "Pašoka",
    "underline": "Pabraukimas",
    "none": "Neišryškinti",
}
"""How the word being spoken right now stands out (karaoke-style
highlighting while speaking)."""

PhraseBackground = Literal["none", "box", "pill"]
PHRASE_BACKGROUND_CHOICES: tuple[PhraseBackground, ...] = ("none", "box", "pill")
PHRASE_BACKGROUND_LABELS: dict[str, str] = {"none": "Be fono", "box": "Stačiakampis", "pill": "Apvalus"}

POSITION_Y_FRACTIONS: dict[str, float] = {"top": 0.2, "center": 0.5, "bottom": 0.74}
POSITION_LABELS: dict[str, str] = {"top": "Viršuje", "center": "Centre", "bottom": "Apačioje"}


@dataclass(frozen=True)
class ReelsWord:
    """One spoken word with its own time window (from speech recognition
    or typed by hand)."""

    text: str
    start_seconds: float
    end_seconds: float
    emphasized: bool = False
    """A key word: drawn in the style's highlight color (and a bit bigger)."""
    line_break_after: bool = False
    """Start a new phrase after this word."""


@dataclass(frozen=True)
class ReelsCaptionStyle:
    font: str = "impact"
    """A text_render.FONT_CHOICES key."""
    custom_font_path: str = ""
    """Your own .ttf/.otf file; used instead of `font` when it exists."""
    font_size: int = 92
    weight: int = 0
    """Extra boldness 0-3 (thickens the letters with their own color)."""
    uppercase: bool = True
    text_color: str = "white"
    highlight_color: str = "#FFD400"
    """Key (emphasized) words."""
    emphasis_scale: float = 1.12
    active_effect: ActiveEffect = "color"
    active_color: str = "#3DFF6E"
    """The word being spoken (effects "color" and "underline")."""
    box_color: str = "#7B2FF7"
    """Behind the word being spoken (effect "box")."""
    outline_width: int = 7
    outline_color: str = "black"
    shadow_offset: int = 5
    shadow_color: str = "black@0.6"
    shadow_blur: int = 6
    background: PhraseBackground = "none"
    background_color: str = "black@0.45"
    y_fraction: float = 0.74
    """Vertical center of the subtitle block (0 = top, 1 = bottom)."""
    max_width_fraction: float = 0.86
    max_words: int = 3
    """At most this many words on screen at once."""
    animation: PhraseAnimation = "pop"
    reveal: WordReveal = "phrase"
    animation_speed: float = 1.0
    phrase_gap_seconds: float = 0.6
    """A pause this long in speech starts a new phrase."""
    hold_seconds: float = 0.4
    """How long a phrase stays after its last word if the next one isn't starting."""

    def validate(self) -> list[str]:
        problems: list[str] = []
        if not 16 <= self.font_size <= 300:
            problems.append("Šrifto dydis turi būti nuo 16 iki 300.")
        if not 1 <= self.max_words <= 12:
            problems.append("Žodžių skaičius frazėje turi būti nuo 1 iki 12.")
        if not 0.0 <= self.y_fraction <= 1.0:
            problems.append("Vieta turi būti tarp 0 ir 1.")
        if self.animation not in PHRASE_ANIMATION_CHOICES:
            problems.append(f"Nežinoma animacija: {self.animation!r}.")
        if self.reveal not in WORD_REVEAL_CHOICES:
            problems.append(f"Nežinomas rodymo būdas: {self.reveal!r}.")
        if self.active_effect not in ACTIVE_EFFECT_CHOICES:
            problems.append(f"Nežinomas išryškinimas: {self.active_effect!r}.")
        if self.animation_speed <= 0:
            problems.append("Animacijos greitis turi būti didesnis už 0.")
        return problems

    @property
    def font_file(self) -> str:
        return resolve_font_file(self.font, self.custom_font_path)


def resolve_font_file(font: str, custom_font_path: str = "") -> str:
    from pathlib import Path

    if custom_font_path and Path(custom_font_path).is_file():
        return custom_font_path
    return text_render.resolve_font_file(font, text_render._FALLBACK_FONT_FILES[0])


REELS_CAPTION_PRESETS: dict[str, dict] = {
    "Reels klasika": {},
    "Hormozi": {
        "font": "impact", "active_effect": "box", "box_color": "#7B2FF7", "highlight_color": "#FFD400",
        "reveal": "phrase", "animation": "pop", "max_words": 3,
    },
    "Karaoke": {
        "font": "arial_bold", "uppercase": False, "active_effect": "color", "active_color": "#FFD400",
        "highlight_color": "#FF4FA3", "max_words": 5, "animation": "fade", "outline_width": 5,
    },
    "Žodis po žodžio": {"reveal": "word_pop", "animation": "none", "max_words": 4, "active_effect": "scale"},
    "Minimalistinis": {
        "font": "segoe", "uppercase": False, "font_size": 70, "outline_width": 0, "shadow_offset": 3,
        "shadow_blur": 10, "active_effect": "underline", "active_color": "white", "highlight_color": "#9EE7FF",
        "animation": "fade", "max_words": 5,
    },
    "Neoninis": {
        "font": "verdana", "text_color": "white", "outline_color": "#FF2BD6", "outline_width": 4,
        "shadow_color": "#FF2BD6@0.8", "shadow_offset": 0, "shadow_blur": 14, "active_effect": "color",
        "active_color": "#00F0FF", "highlight_color": "#00F0FF", "animation": "zoom",
    },
    "Su fonu": {
        "font": "arial_bold", "uppercase": False, "outline_width": 0, "shadow_offset": 0, "background": "pill",
        "background_color": "black@0.6", "active_effect": "color", "active_color": "#FFD400", "max_words": 5,
        "animation": "slide_up",
    },
    "Šokinėjantis": {"active_effect": "bounce", "reveal": "word", "animation": "none", "highlight_color": "#FF7A00"},
}
_STYLE_LOOK_FIELDS = tuple(
    f.name for f in dataclasses.fields(ReelsCaptionStyle) if f.name not in ("y_fraction", "custom_font_path")
)


def apply_caption_preset(style: ReelsCaptionStyle, name: str) -> ReelsCaptionStyle:
    """`style` with preset `name`'s look - starting from the default look
    so nothing of a previous preset stays behind. Keeps the position and
    your own font file."""
    baseline = ReelsCaptionStyle()
    changes = {name_: getattr(baseline, name_) for name_ in _STYLE_LOOK_FIELDS}
    changes.update(REELS_CAPTION_PRESETS[name])
    return dataclasses.replace(style, **changes)


@dataclass(frozen=True)
class ReelsCaptions:
    words: tuple[ReelsWord, ...] = ()
    style: ReelsCaptionStyle = field(default_factory=ReelsCaptionStyle)
    visible: bool = True


@dataclass(frozen=True)
class Phrase:
    """Words shown on screen together. `first`/`last` index ReelsCaptions.words."""

    first: int
    last: int
    start_seconds: float
    end_seconds: float
    """When the phrase leaves the screen (after its hold)."""


_SENTENCE_END = re.compile(r"[.!?…]$")


def build_phrases(words: tuple[ReelsWord, ...] | list[ReelsWord], style: ReelsCaptionStyle) -> list[Phrase]:
    """Splits words into on-screen phrases: at most `style.max_words`
    words, a new phrase after a sentence end, a forced line break or a
    pause of `style.phrase_gap_seconds`. A phrase stays until the next
    one starts or `style.hold_seconds` after its last word."""
    groups: list[tuple[int, int]] = []
    start = 0
    for index in range(len(words)):
        word = words[index]
        is_last = index == len(words) - 1
        split = is_last
        if not is_last:
            following = words[index + 1]
            split = (
                index - start + 1 >= style.max_words
                or word.line_break_after
                or bool(_SENTENCE_END.search(word.text.strip()))
                or following.start_seconds - word.end_seconds >= style.phrase_gap_seconds
            )
        if split:
            groups.append((start, index))
            start = index + 1

    phrases: list[Phrase] = []
    for n, (first, last) in enumerate(groups):
        begin = words[first].start_seconds
        end = words[last].end_seconds + style.hold_seconds
        if n + 1 < len(groups):
            end = min(end, words[groups[n + 1][0]].start_seconds)
        phrases.append(Phrase(first, last, begin, max(end, words[last].end_seconds)))
    return phrases


def phrase_at(phrases: list[Phrase], t: float) -> Phrase | None:
    for phrase in phrases:
        if phrase.start_seconds <= t < phrase.end_seconds:
            return phrase
    return None


def words_from_timings(timings, *, offset_seconds: float = 0.0) -> tuple[ReelsWord, ...]:
    """ReelsWords from speech recognition results (captions.WordTiming:
    anything with text/start_seconds/end_seconds)."""
    words: list[ReelsWord] = []
    for timing in timings:
        text = timing.text.strip()
        if not text:
            continue
        words.append(ReelsWord(
            text=text, start_seconds=round(timing.start_seconds + offset_seconds, 3),
            end_seconds=round(max(timing.end_seconds, timing.start_seconds + 0.05) + offset_seconds, 3),
        ))
    return tuple(words)


def words_from_text(text: str, *, start_seconds: float, end_seconds: float) -> tuple[ReelsWord, ...]:
    """Typed text spread over [start, end], each word getting time in
    proportion to its length (for subtitles typed by hand)."""
    tokens = text.split()
    if not tokens or end_seconds <= start_seconds:
        return ()
    weights = [len(token) + 2 for token in tokens]
    total = sum(weights)
    span = end_seconds - start_seconds
    words: list[ReelsWord] = []
    cursor = start_seconds
    for token, weight in zip(tokens, weights):
        length = span * weight / total
        words.append(ReelsWord(token, round(cursor, 3), round(cursor + length, 3)))
        cursor += length
    return tuple(words)


def retext_phrase(
    words: tuple[ReelsWord, ...], phrase: Phrase, new_text: str,
) -> tuple[ReelsWord, ...]:
    """Replaces the words of `phrase` with `new_text`. Same number of
    words: each keeps its timing and key-word mark (fixing a misheard
    word). Otherwise the new words share the phrase's spoken time."""
    old = words[phrase.first:phrase.last + 1]
    tokens = new_text.split()
    if len(tokens) == len(old):
        replaced = tuple(dataclasses.replace(w, text=token) for w, token in zip(old, tokens))
    else:
        replaced = words_from_text(new_text, start_seconds=old[0].start_seconds, end_seconds=old[-1].end_seconds)
        if replaced and old[-1].line_break_after:
            replaced = replaced[:-1] + (dataclasses.replace(replaced[-1], line_break_after=True),)
    return words[:phrase.first] + replaced + words[phrase.last + 1:]


def shift_phrase(words: tuple[ReelsWord, ...], phrase: Phrase, delta: float) -> tuple[ReelsWord, ...]:
    """Moves a phrase's words by `delta` seconds (never before 0)."""
    delta = max(delta, -words[phrase.first].start_seconds)
    moved = tuple(
        dataclasses.replace(w, start_seconds=round(w.start_seconds + delta, 3), end_seconds=round(w.end_seconds + delta, 3))
        for w in words[phrase.first:phrase.last + 1]
    )
    return words[:phrase.first] + moved + words[phrase.last + 1:]


def retime_phrase(
    words: tuple[ReelsWord, ...], phrase: Phrase, new_start: float, new_end: float,
) -> tuple[ReelsWord, ...]:
    """Stretches a phrase's words to fill [new_start, new_end], keeping
    each word's share of the phrase."""
    old_start = words[phrase.first].start_seconds
    old_end = words[phrase.last].end_seconds
    new_start = max(0.0, new_start)
    if new_end - new_start < 0.1 or old_end <= old_start:
        return words
    factor = (new_end - new_start) / (old_end - old_start)

    def remap(t: float) -> float:
        return round(new_start + (t - old_start) * factor, 3)

    stretched = tuple(
        dataclasses.replace(w, start_seconds=remap(w.start_seconds), end_seconds=remap(w.end_seconds))
        for w in words[phrase.first:phrase.last + 1]
    )
    return words[:phrase.first] + stretched + words[phrase.last + 1:]


def set_emphasis(words: tuple[ReelsWord, ...], index: int, emphasized: bool) -> tuple[ReelsWord, ...]:
    return words[:index] + (dataclasses.replace(words[index], emphasized=emphasized),) + words[index + 1:]


def normalize_word(text: str) -> str:
    return re.sub(r"[^\w]", "", text.lower())


def emphasize_matching(words: tuple[ReelsWord, ...], keywords: list[str]) -> tuple[ReelsWord, ...]:
    """Marks every word matching one of `keywords` (case and punctuation
    ignored; a keyword also matches longer forms of the word, so
    "automatizacij" finds "automatizacija" and "automatizacijos")."""
    wanted = [normalize_word(k) for k in keywords if normalize_word(k)]
    if not wanted:
        return words
    result = []
    for word in words:
        plain = normalize_word(word.text)
        hit = any(plain == k or (len(k) >= 4 and plain.startswith(k)) for k in wanted)
        result.append(dataclasses.replace(word, emphasized=True) if hit and not word.emphasized else word)
    return tuple(result)


# --- all Reels layers --------------------------------------------------------------------------


@dataclass(frozen=True)
class ReelsLayers:
    """Everything the Reels mode draws on top of the timeline."""

    captions: ReelsCaptions | None = None

    @property
    def is_empty(self) -> bool:
        return self.captions is None or not self.captions.words or not self.captions.visible


def to_dict(layers: ReelsLayers) -> dict:
    return dataclasses.asdict(layers)


def from_dict(data: dict | None) -> ReelsLayers | None:
    """The inverse of to_dict(); tolerant of fields added or removed
    by later versions (unknown keys are dropped, missing ones default)."""
    if not data:
        return None
    captions = None
    captions_data = data.get("captions")
    if captions_data:
        captions = ReelsCaptions(
            words=tuple(_build(ReelsWord, w) for w in captions_data.get("words") or ()),
            style=_build(ReelsCaptionStyle, captions_data.get("style") or {}),
            visible=bool(captions_data.get("visible", True)),
        )
    return ReelsLayers(captions=captions)


def _build(cls, data: dict):
    names = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in names})
