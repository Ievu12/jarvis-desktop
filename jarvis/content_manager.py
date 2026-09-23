"""Content Manager: builds the text prompt jarvis.morning_routine (and
any ad-hoc voice/typed question like "duok Reel idėją") sends through
the existing jarvis.core.agent.Agent.step() to get Instagram content
ideas - this module generates NO content itself. All actual idea
generation happens in the LLM's own response to that prompt, exactly
like every other agent turn; this module only (a) knows the fixed
content directions, (b) tracks which topics were recently suggested so
the prompt can ask the model to avoid repeating them, and (c) records
what the model actually proposed once a briefing has run.

Separate from jarvis.integrations.connectors.instagram in every way: it
imports nothing from that module and calls no Graph API. Optional
Instagram performance context (recent insights) is passed in by the
caller (jarvis.morning_routine) as a plain string, already fetched via
the model's own tool calls - this module never talks to
InstagramConnector directly.

Local, JSON-backed topic history at jarvis.config
.CONTENT_TOPICS_HISTORY_FILE, structured and implemented identically to
jarvis.integrations.instagram_history (one JSON object per stored
calendar date, upsert not append, corruption reported via .warning
rather than crashing). Contains no credential - only dates and short
topic strings the model itself proposed.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis.config import CONTENT_TOPICS_HISTORY_FILE

# The person's content directions (Stage 1: fixed list, set once here -
# not configurable via a tool or voice command yet). Used verbatim in
# the generated prompt so the model's ideas stay anchored to these,
# rather than drifting to generic content advice.
CONTENT_DIRECTIONS = (
    "yoga",
    "beauty/kosmetika",
    "lifestyle",
    "self-development",
    "skaitmeniniai produktai",
)

# How many past days of recorded topics to remind the model to avoid
# repeating. Kept short deliberately - content from a month ago is fair
# game again; this is about not suggesting the same Reel idea two days
# in a row, not building a permanent do-not-reuse list.
_RECENT_TOPICS_WINDOW_DAYS = 14

_WEEKDAY_NAMES_LT = (
    "pirmadienis", "antradienis", "trečiadienis", "ketvirtadienis",
    "penktadienis", "šeštadienis", "sekmadienis",
)


class TopicsHistoryLoadResult:
    """Mirrors jarvis.integrations.instagram_history.HistoryLoadResult -
    same shape, same reasoning (a corrupted file is reported via
    .warning, never crashes the caller)."""

    def __init__(self, entries: dict[str, list[str]], warning: str | None = None):
        self.entries = entries
        self.warning = warning


def load_topics_history() -> TopicsHistoryLoadResult:
    """Load the full stored topic history as {date_string: [topic, ...]}.
    Never raises - a missing file is a normal empty history."""
    if not CONTENT_TOPICS_HISTORY_FILE.exists():
        return TopicsHistoryLoadResult({})

    try:
        raw_text = CONTENT_TOPICS_HISTORY_FILE.read_text(encoding="utf-8")
    except OSError as e:
        return TopicsHistoryLoadResult(
            {},
            warning=(
                f"Could not read {CONTENT_TOPICS_HISTORY_FILE} ({e}); "
                "treating content topics history as empty."
            ),
        )

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError:
        return TopicsHistoryLoadResult(
            {},
            warning=(
                f"Content topics history file at {CONTENT_TOPICS_HISTORY_FILE} is "
                "corrupted (invalid JSON) and could not be loaded. Previously "
                "recorded topics are unavailable for de-duplication; the corrupted "
                "file was left in place for inspection. New topics can still be "
                "recorded going forward."
            ),
        )

    if not isinstance(data, dict):
        return TopicsHistoryLoadResult(
            {},
            warning=(
                f"Content topics history file at {CONTENT_TOPICS_HISTORY_FILE} has an "
                "unexpected format (expected a date -> topic list mapping) and could "
                "not be loaded."
            ),
        )

    cleaned: dict[str, list[str]] = {}
    for key, value in data.items():
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            cleaned[key] = value
    return TopicsHistoryLoadResult(cleaned)


def _save_topics_history(entries: dict[str, list[str]]) -> None:
    CONTENT_TOPICS_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONTENT_TOPICS_HISTORY_FILE.write_text(
        json.dumps(entries, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )


def record_used_topics(topics: list[str], *, on_date: str | None = None) -> None:
    """Store today's (or `on_date`'s) suggested topics, overwriting
    whatever was already recorded for that date - upsert, not append,
    matching jarvis.integrations.instagram_history.upsert_snapshot(). An
    empty `topics` list is a no-op (nothing meaningful to record)."""
    if not topics:
        return
    date_str = on_date or datetime.now(timezone.utc).date().isoformat()
    result = load_topics_history()
    entries = result.entries
    entries[date_str] = list(topics)
    _save_topics_history(entries)


def recent_topics(*, window_days: int = _RECENT_TOPICS_WINDOW_DAYS) -> list[str]:
    """Every topic recorded within the last `window_days` days (today
    inclusive), flattened and de-duplicated, oldest-recorded first. An
    empty list (never an error) if nothing has been recorded yet - a
    fresh install has no history to avoid repeating."""
    entries = load_topics_history().entries
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=window_days)

    seen: dict[str, None] = {}  # dict, not set, to preserve insertion order
    for date_str in sorted(entries):
        try:
            entry_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            continue
        if entry_date < cutoff:
            continue
        for topic in entries[date_str]:
            seen.setdefault(topic, None)
    return list(seen)


def _today_weekday_lt(now: datetime | None = None) -> tuple[str, str]:
    """Returns (iso_date_string, Lithuanian weekday name) for `now`
    (defaults to the current UTC time) - used both for the spoken
    greeting and the generated prompt's date context."""
    current = now or datetime.now(timezone.utc)
    return current.date().isoformat(), _WEEKDAY_NAMES_LT[current.weekday()]


def build_content_brief_prompt(
    *, instagram_context: str | None = None, now: datetime | None = None
) -> str:
    """Builds the text prompt sent through Agent.step() to get today's
    content ideas. Generates no ideas itself - the model's response to
    this prompt is the actual content brief; this function only shapes
    what's asked for for (fixed output structure, content directions,
    recent-topics-to-avoid, and optional Instagram performance context
    already fetched by the caller)."""
    date_str, weekday_lt = _today_weekday_lt(now)
    directions_line = ", ".join(CONTENT_DIRECTIONS)

    avoid_topics = recent_topics()
    if avoid_topics:
        avoid_line = (
            "Pastarosiomis dienomis jau buvo pasiūlytos šios temos - nekartok jų, "
            "pasiūlyk ką nors naujo: " + "; ".join(avoid_topics) + "."
        )
    else:
        avoid_line = "Ankstesnių pasiūlytų temų istorijos dar nėra - gali siūlyti laisvai."

    if instagram_context:
        performance_line = (
            "Naudok šią naujausią Instagram statistiką, kad pasiūlymai būtų paremti "
            f"realiais ankstesnio turinio rezultatais:\n{instagram_context}"
        )
    else:
        performance_line = (
            "Instagram statistikos šiuo metu nėra prieinama arba paskyra "
            "nesukonfigūruota - remkis bendra turinio strategija be duomenimis "
            "paremtų įžvalgų, ir tai aiškiai paminėk."
        )

    return (
        f"Šiandien yra {date_str}, {weekday_lt}. Paruošk šiandienos Instagram turinio "
        f"planą pagal šias turinio kryptis: {directions_line}.\n\n"
        f"{performance_line}\n\n"
        f"{avoid_line}\n\n"
        "Pateik LYGIAI tiek idėjų:\n"
        "- 1 Reel idėja\n"
        "- 3-5 Story idėjos\n"
        "- 1 Carousel arba nuotraukos idėja\n\n"
        "Kiekvienai idėjai (be išimčių) pateik šiuos keturis elementus, aiškiai "
        "pažymėtus:\n"
        "- Hook (pirmos sekundės/pirma eilutė, kuri sulaiko dėmesį)\n"
        "- Pagrindinė mintis (kas per turinys ir kodėl jis vertingas)\n"
        "- Trumpas tekstas (galimas caption/scenarijaus juodraštis, kelios eilutės)\n"
        "- CTA (konkretus raginimas veikti)\n\n"
        "Atsakyk lietuviškai, trumpai ir konkrečiai - be bendrų frazių, be vandens. "
        "Nepublikuok, nekomentuok ir nesiųsk nieko Instagram paskyroje - tik pateik "
        "šį planą tekstu."
    )


def extract_topic_summaries(content_brief_text: str) -> list[str]:
    """A light heuristic to pull short topic labels out of the model's
    free-text content brief, for record_used_topics() to store - looks
    for lines starting with a recognized idea-type marker (Reel/Story/
    Carousel, case-insensitive) and keeps a short prefix of that line as
    the "topic". Deliberately approximate: this is only used to steer
    future de-duplication, not to parse the brief structurally for any
    other purpose, so a line it fails to catch just means one fewer
    topic recorded, never a crash or a wrong idea shown to the person.
    """
    markers = ("reel", "story", "carousel", "nuotrauk")
    topics: list[str] = []
    for line in content_brief_text.splitlines():
        stripped = line.strip().lstrip("-*#").strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(lowered.startswith(marker) or f" {marker}" in lowered[:40] for marker in markers):
            topics.append(stripped[:120])
    return topics


def format_greeting(now: datetime | None = None) -> str:
    """The fixed 'Labas rytas' + date + weekday opening line, spoken
    before the content brief itself. Kept separate from the brief prompt
    so run_morning_routine can speak it immediately (fast, no LLM call)
    while the actual content ideas are still being generated."""
    date_str, weekday_lt = _today_weekday_lt(now)
    # Human-friendly Lithuanian date reading (e.g. "2026 m. rugsėjo 23 d.")
    # rather than the raw ISO string, since this line is meant to be heard,
    # not read.
    current = now or datetime.now(timezone.utc)
    month_names_lt = (
        "sausio", "vasario", "kovo", "balandžio", "gegužės", "birželio",
        "liepos", "rugpjūčio", "rugsėjo", "spalio", "lapkričio", "gruodžio",
    )
    human_date = f"{current.year} m. {month_names_lt[current.month - 1]} {current.day} d."
    return f"Labas rytas! Šiandien {human_date}, {weekday_lt}."
