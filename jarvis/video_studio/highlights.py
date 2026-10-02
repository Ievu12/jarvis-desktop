"""AI Highlight Detection (module brief, section 3): identifies
potential high-value clip candidates from an already-analyzed,
already-transcribed video.

Two-stage pipeline, per the plan agreed before this stage started:

  1. RULE-BASED candidate boundaries - built from
     jarvis.video_studio.analysis.VideoAnalysis's scene changes and
     jarvis.video_studio.transcribe.TranscriptionResult's speech
     segments (measurable characteristics only: where a scene cuts,
     where speech starts/stops, how long a stretch of continuous
     speech is). No LLM involved in this stage - see
     _build_candidate_windows().
  2. LLM analysis of TRANSCRIPT TEXT ONLY (never the video frames -
     jarvis.core.llm.LLMClient has no vision/image support - see this
     module's own architecture inspection) to pick which candidate
     windows actually contain a strong opening statement, a question,
     an emotional moment, a clear explanation, or a strong conclusion,
     and to suggest a hook and a one-line reason for each one selected.
     Uses the exact isolated, tool-free, JSON-only LLM call pattern
     jarvis.instagram_ai_manager.ai_services already established (see
     that module's docstring) - no tools offered, so this call can
     never trigger a real action, and a malformed/failed response
     returns None rather than raising or fabricating a result.

Per the module's brief: "Do not claim that a clip is 'viral'. Describe
it as a candidate based on measurable characteristics." Every
HighlightCandidate carries its own real timestamps/duration/transcript
text plus the model's stated `reason` and a `confidence` the model
itself estimates (never a fabricated score this module invents) -
nothing here is ever labeled "viral" or guaranteed to perform.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient
from jarvis.video_studio.analysis import VideoAnalysis
from jarvis.video_studio.transcribe import TranscriptSegment, TranscriptionResult

_MAX_GENERATION_TOKENS = 3000

# A candidate window must contain at least this much transcript text to
# be worth sending to the LLM at all - a 1-2 word fragment ("Thank
# you.") is measurable but not usefully analyzable for "strong opening
# statement" / "clear explanation" - style content, so it's filtered
# out before the LLM call rather than wasting a request on it.
_MIN_CANDIDATE_WORD_COUNT = 4

# How many rule-based candidate windows to send to the LLM in one call
# - bounded so a long video with many scene changes doesn't produce an
# unbounded prompt; the LLM is asked to pick and rank from AT MOST this
# many, not to invent additional ones.
_MAX_CANDIDATES_TO_ANALYZE = 20

_SYSTEM_PROMPT = (
    "You are a short-form video editor's assistant. You are given a numbered "
    "list of transcript excerpts, each with a start and end timestamp, from "
    "one video. Your job is to identify which excerpts would work well as a "
    "15-90 second standalone Instagram Reel clip, based ONLY on the transcript "
    "text (you cannot see the video). Consider: a strong opening statement, a "
    "question, an emotional moment, useful/clear information, a strong "
    "conclusion, or a clear problem/solution structure. Never claim a clip "
    "will be 'viral' or guaranteed to perform well - only describe why the "
    "TEXT itself is a good candidate. Return between 1 and 8 candidates, "
    "picking only the excerpts that are genuinely strong - it is fine to "
    "return fewer than were given, or none, if nothing stands out."
)

_JSON_INSTRUCTION = (
    "Reply with ONLY a single valid JSON array - no explanation, no markdown "
    "code fences. Each element must be an object with exactly these keys: "
    '"index" (the integer number of the excerpt you are selecting, from the '
    'list given), "reason" (one sentence explaining what makes this excerpt '
    'work as a clip, grounded in its actual content), "suggested_hook" (a '
    "short opening line, in the same language as the transcript, that could "
    'introduce this clip), "confidence" (your own estimate, one of "low", '
    '"medium", or "high", of how strong a candidate this is - never a made-up '
    'numeric score). The response must be parseable by a strict JSON parser '
    "as-is."
)


@dataclass(frozen=True)
class CandidateWindow:
    """One rule-based candidate boundary, before any LLM involvement -
    purely measurable characteristics (module brief: "based on
    measurable characteristics")."""

    start_seconds: float
    end_seconds: float
    transcript_text: str
    segment_indices: tuple[int, ...]
    """Indices into the original TranscriptionResult.segments list this
    window covers - kept so a caller can reconstruct exact per-segment
    timing for subtitle/clip export in a later stage, without this
    module needing to re-derive it."""

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class HighlightCandidate:
    start_seconds: float
    end_seconds: float
    transcript_text: str
    reason: str
    suggested_hook: str
    confidence: str  # "low" | "medium" | "high" - the model's own estimate, never a fabricated number

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


@dataclass(frozen=True)
class HighlightResult:
    candidates: list[HighlightCandidate]
    insufficient_data: bool
    message: str | None
    """Set when insufficient_data is True (no transcript, no scene
    data, or the LLM call failed/returned nothing usable) - same
    convention as jarvis.video_studio.analysis.VideoAnalysis.error and
    jarvis.instagram_ai_manager.analytics_services' insufficient_data
    fields."""

    @staticmethod
    def from_dict(data: dict) -> "HighlightResult":
        """Reconstructs a HighlightResult from the plain dict
        jarvis.video_studio.db stores (via dataclasses.asdict()) - see
        TranscriptionResult.from_dict()'s docstring for why a bare
        HighlightResult(**data) is wrong (it would leave `candidates`
        as plain dicts, not HighlightCandidate instances)."""
        return HighlightResult(
            candidates=[HighlightCandidate(**c) for c in data["candidates"]],
            insufficient_data=data["insufficient_data"], message=data["message"],
        )


def _build_candidate_windows(
    analysis: VideoAnalysis, transcription: TranscriptionResult,
) -> list[CandidateWindow]:
    """Builds candidate clip boundaries from measurable signals only:
    scene-change timestamps and continuous stretches of transcribed
    speech. A candidate window is one CONTIGUOUS run of transcript
    segments that together span no more than a reasonable Reel length
    (the module brief's own duration options top out at 90s - this
    caps a window at that, splitting a longer continuous speech
    stretch at the nearest scene change or segment boundary rather than
    producing an unusably long candidate). Scene-change timestamps that
    fall within a window are not used to further sub-split it here -
    they inform WHERE speech-based windows naturally start/end (a
    scene cut is a natural clip start), not an independent signal this
    function scores."""
    if not transcription.segments:
        return []

    max_window_seconds = 90.0
    scene_times = sorted(c.timestamp_seconds for c in analysis.scene_changes)

    windows: list[CandidateWindow] = []
    current_indices: list[int] = []
    current_start: float | None = None

    def _flush() -> None:
        nonlocal current_indices, current_start
        if current_indices and current_start is not None:
            segs = [transcription.segments[i] for i in current_indices]
            text = " ".join(s.text for s in segs).strip()
            if text:
                windows.append(
                    CandidateWindow(
                        start_seconds=current_start, end_seconds=segs[-1].end_seconds,
                        transcript_text=text, segment_indices=tuple(current_indices),
                    )
                )
        current_indices = []
        current_start = None

    for i, segment in enumerate(transcription.segments):
        if current_start is None:
            current_start = segment.start_seconds

        # A scene change landing inside the gap since the last segment
        # ended is treated as a natural break point - close the current
        # window here rather than spanning across an unrelated visual
        # cut, even though the speech itself is continuous.
        if current_indices:
            prev_end = transcription.segments[current_indices[-1]].end_seconds
            crosses_scene_change = any(prev_end <= t <= segment.start_seconds for t in scene_times)
            would_exceed_max = (segment.end_seconds - current_start) > max_window_seconds
            large_gap = segment.start_seconds - prev_end > 3.0
            if crosses_scene_change or would_exceed_max or large_gap:
                _flush()
                current_start = segment.start_seconds

        current_indices.append(i)

    _flush()

    return [w for w in windows if len(w.transcript_text.split()) >= _MIN_CANDIDATE_WORD_COUNT]


def _extract_json(text: str) -> Any | None:
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def find_highlights(
    llm: LLMClient, analysis: VideoAnalysis, transcription: TranscriptionResult,
) -> HighlightResult:
    """The module's main entry point (module brief: `find_highlights()`
    in the AI + Video Processing Architecture list). Never raises -
    every failure mode (no transcript, LLM call failure, malformed
    response) comes back via HighlightResult.insufficient_data/message,
    matching this package's established error-handling convention (see
    jarvis.video_studio.analysis.analyze_video()'s docstring)."""
    if transcription.error or not transcription.segments:
        return HighlightResult(
            candidates=[], insufficient_data=True,
            message="No transcript is available yet - transcribe the video first to detect highlights.",
        )

    windows = _build_candidate_windows(analysis, transcription)
    if not windows:
        return HighlightResult(
            candidates=[], insufficient_data=True,
            message="The transcript didn't contain any segments substantial enough to evaluate as clip candidates.",
        )

    windows = windows[:_MAX_CANDIDATES_TO_ANALYZE]
    excerpt_lines = []
    for i, w in enumerate(windows):
        excerpt_lines.append(
            f"{i}. [{w.start_seconds:.1f}s - {w.end_seconds:.1f}s] {w.transcript_text}"
        )
    prompt = (
        "Transcript excerpts (numbered, with timestamps):\n\n"
        + "\n\n".join(excerpt_lines)
        + f"\n\n{_JSON_INSTRUCTION}"
    )

    try:
        response = llm.send(
            [{"role": "user", "content": prompt}], [],
            max_tokens=_MAX_GENERATION_TOKENS, system=_SYSTEM_PROMPT,
        )
    except Exception as e:
        return HighlightResult(candidates=[], insufficient_data=True, message=f"AI analysis failed: {e}")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    parsed = _extract_json(text) if text else None
    if not isinstance(parsed, list):
        return HighlightResult(
            candidates=[], insufficient_data=True,
            message="AI analysis didn't return a usable result - try again.",
        )

    candidates: list[HighlightCandidate] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        index = item.get("index")
        reason = item.get("reason")
        suggested_hook = item.get("suggested_hook")
        confidence = item.get("confidence")
        if (
            not isinstance(index, int) or not (0 <= index < len(windows))
            or not isinstance(reason, str) or not reason.strip()
            or not isinstance(suggested_hook, str) or not suggested_hook.strip()
            or confidence not in ("low", "medium", "high")
        ):
            continue
        window = windows[index]
        candidates.append(
            HighlightCandidate(
                start_seconds=window.start_seconds, end_seconds=window.end_seconds,
                transcript_text=window.transcript_text, reason=reason.strip(),
                suggested_hook=suggested_hook.strip(), confidence=confidence,
            )
        )

    if not candidates:
        return HighlightResult(
            candidates=[], insufficient_data=True,
            message="The AI didn't find any strong clip candidates in this video's transcript.",
        )

    candidates.sort(key=lambda c: c.start_seconds)
    return HighlightResult(candidates=candidates, insufficient_data=False, message=None)
