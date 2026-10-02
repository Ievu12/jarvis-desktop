"""Speech-driven effect timing - requirement 5 ("Sinchronizuoti
animuotus subtitrus su tariamais žodžiais" / "leisti tam tikriems
žodžiams automatiškai priskirti animacijas" - sync animated captions
with spoken words / let specific words automatically trigger
animations). Finds WHERE a chosen keyword/phrase is actually spoken in
a real transcription (jarvis.video_editor.captions.WordTiming, the same
real, whisper-measured timing data captions.py/the GUI's own "Generate
& Edit Subtitles" button already produce) - the pure text-matching half
of "ištarus 'Nuostabus pasiūlymas', ekrane atsiranda animuotas tekstas"
(when "Great offer" is said, an animated text/graphic appears on
screen). The actual sticker/text overlay is then placed by the EXISTING
jarvis.video_editor.stickers/.text_overlay mechanisms, using the real
start/end time this module finds - this module never builds a filter
clause or touches ffmpeg itself, keeping it a pure, I/O-free text-
matching utility, trivially unit-testable without any ffmpeg call.

Sentence-boundary/pause detection for highlighting a sentence's own most
important words is explicitly NOT implemented as a separate "importance"
heuristic here - with no real NLP/vision model in this stack (see
jarvis.video_editor.captions's own honest "no real transcription-content
reasoning beyond text-level LLM calls" stance, mirrored here), ranking
words by "importance" would mean inventing an arbitrary, unverifiable
rule (e.g. "longer words matter more") rather than a genuinely measured
signal - this module instead gives the person DIRECT, text-based search
for the exact word/phrase they already know matters to them, which is
both more honest and more useful than a fake importance score."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from jarvis.video_editor.captions import WordTiming


@dataclass(frozen=True)
class SpeechMatch:
    """One real match of a searched phrase against the actual spoken
    words - `start_seconds`/`end_seconds` span exactly the matched
    word(s)' own real, measured timing (the same source-relative
    timeline WordTiming itself uses - see that dataclass's own
    docstring for why no multi-clip timeline offset is applied here
    either), `matched_text` is the real spoken words as transcribed
    (which may differ slightly in capitalization/punctuation from the
    search phrase - whisper's own transcription, not a copy of what was
    searched for)."""

    start_seconds: float
    end_seconds: float
    matched_text: str


def _normalize(text: str) -> str:
    """Case-folds and strips punctuation/diacritics-INSENSITIVE
    comparison would be WRONG for Lithuanian (diacritics are meaningful,
    distinct letters, not decoration) - this only lowercases and strips
    punctuation, never touches letters themselves, so "ą"/"a" stay
    genuinely different characters, matching whisper's own real
    Lithuanian transcription output faithfully."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    return text.strip()


def find_phrase_matches(words: list[WordTiming], phrase: str) -> list[SpeechMatch]:
    """Searches for every occurrence of `phrase` (one or more words,
    whitespace-separated) as a CONSECUTIVE run within `words` - a real,
    exact (case/punctuation-insensitive, diacritic-SENSITIVE) text
    match against the actual transcription, never a fuzzy/approximate
    one (an approximate match could silently trigger an effect on the
    wrong word, which requirement 5's own "galimybė koreguoti efektų
    laiką ir išjungti automatinius pasiūlymus" - ability to adjust/
    disable automatic suggestions - implies the person must be able to
    trust at face value before accepting). Returns [] if `phrase` is
    empty/whitespace or no match is found - never raises."""
    phrase_words = [w for w in _normalize(phrase).split() if w]
    if not phrase_words or not words:
        return []

    normalized_words = [_normalize(w.text) for w in words]
    matches: list[SpeechMatch] = []
    phrase_len = len(phrase_words)
    for i in range(len(words) - phrase_len + 1):
        if normalized_words[i : i + phrase_len] == phrase_words:
            span = words[i : i + phrase_len]
            matches.append(SpeechMatch(
                start_seconds=span[0].start_seconds, end_seconds=span[-1].end_seconds,
                matched_text=" ".join(w.text for w in span),
            ))
    return matches


@dataclass(frozen=True)
class SpeechTrigger:
    """One person-configured "when this phrase is spoken, suggest this
    timing" rule - a plain, JSON-serializable value (requirement:
    "galimybė nustatyti, kokie efektai atsiranda ties konkrečiais
    žodžiais ar frazėmis" - ability to set which effects appear at
    specific words/phrases). This dataclass only carries the SEARCH
    phrase and an optional padding window around the match - it never
    carries the sticker/text configuration itself, since those already
    have their own real dataclasses (StickerInstance/TextOverlay) this
    module deliberately doesn't duplicate or wrap."""

    phrase: str
    lead_in_seconds: float = 0.0
    hold_seconds: float = 1.0

    def validate(self) -> list[str]:
        """Never raises - matches every other dataclass's own
        established "describe problems, don't throw" convention."""
        problems: list[str] = []
        if not self.phrase.strip():
            problems.append("Speech trigger has no phrase to search for.")
        if self.lead_in_seconds < 0.0:
            problems.append("Speech trigger lead-in time cannot be negative.")
        if self.hold_seconds <= 0.0:
            problems.append("Speech trigger hold duration must be greater than zero.")
        return problems


def resolve_trigger_timing(trigger: SpeechTrigger, words: list[WordTiming]) -> list[SpeechMatch]:
    """Applies `trigger`'s own lead_in/hold adjustment to every real
    match find_phrase_matches() finds - `start_seconds` is pulled
    earlier by `lead_in_seconds` (so an effect can appear slightly
    BEFORE the word is fully spoken, a real, common timing preference
    for a punchy reveal), `end_seconds` is replaced by
    `start_seconds + hold_seconds` (how long the effect should stay
    on screen after triggering, independent of how long the matched
    word itself took to say). Raises nothing - an invalid trigger
    (caught by its own validate()) simply returns no matches, since a
    caller should check validate() itself before relying on this
    function's own output being meaningful."""
    if trigger.validate():
        return []
    raw_matches = find_phrase_matches(words, trigger.phrase)
    return [
        SpeechMatch(
            start_seconds=max(0.0, match.start_seconds - trigger.lead_in_seconds),
            end_seconds=max(0.0, match.start_seconds - trigger.lead_in_seconds) + trigger.hold_seconds,
            matched_text=match.matched_text,
        )
        for match in raw_matches
    ]
