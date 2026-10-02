"""Auto Reel Creator (module brief, section 4): given a project's
already-detected highlight candidates (jarvis.video_studio.highlights)
and a person's choices (target duration, style, pacing), proposes an
EDIT PLAN - which clips, in what order, with what trimming, plus a
hook and ending CTA - without touching any video file itself.
generate_reel_edit() only plans; jarvis.video_studio.export.export_reel()
(a later stage) is what actually renders it, and ALWAYS as a new file
under the project's exports/ directory - this module never writes to
or modifies the original video, matching the brief's explicit "Never
overwrite the original video. Always create a new project/export."

Two-stage pipeline, same shape as jarvis.video_studio.highlights:

  1. RULE-BASED clip selection - picks from the already-detected
     HighlightCandidates (never invents new clip boundaries) by
     confidence and how well their combined duration fits the target,
     trimming a clip's tail if needed to fit. No LLM call for this
     part - selecting/ordering/trimming already-measured candidates
     is itself a measurable operation.
  2. LLM call (isolated, tool-free, JSON-only - same pattern as
     jarvis.video_studio.highlights.find_highlights()) over the
     SELECTED clips' transcript text plus the person's style/pacing
     choice, to write a hook (distinct from a candidate's own
     suggested_hook - this one's for the ASSEMBLED Reel as a whole)
     and an ending CTA, and to suggest simple caption text per clip.
     Silence removal / zoom-crop suggestions from the brief are left
     as explicit, clearly-labeled TODOs on the plan (see
     ReelEditPlan.silence_removal_suggested/zoom_suggestions) rather
     than implemented here - this stage focuses on clip selection/
     ordering/captions/hook/CTA, which is already a full, testable
     unit; the brief's "optional" phrasing for those two makes them a
     reasonable next increment rather than blocking this one.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient
from jarvis.video_studio.highlights import HighlightCandidate

# The module brief's own duration options.
TARGET_DURATIONS = (15, 30, 45, 60, 90)

# The module brief's own style options.
STYLES = ("Educational", "Storytelling", "Inspirational", "Product", "Talking Head", "UGC", "Lifestyle")

# The module brief's own pacing options - used only to steer the LLM's
# hook/CTA/caption phrasing (shorter, punchier for "Fast") since actual
# cut timing is already fixed by the selected candidates' own real
# transcript-derived boundaries, not something this stage re-times.
PACING_OPTIONS = ("Calm", "Normal", "Fast")

_MAX_GENERATION_TOKENS = 2000

_SYSTEM_PROMPT = (
    "You are a short-form video editor's assistant. You are given the "
    "transcript text of several clips that have already been selected, in "
    "order, for one Instagram Reel, along with the person's chosen style and "
    "pacing. Write ONE overall opening hook for the whole Reel (distinct from "
    "any per-clip hook), ONE short ending call-to-action line, and one short "
    "on-screen caption suggestion PER CLIP (a few words, not a full sentence) "
    "that could be overlaid as text on that clip. Match the requested style "
    "and pacing in tone. Ground everything in the actual transcript content "
    "given - never invent claims the transcript doesn't support."
)

_JSON_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object - no explanation, no markdown "
    'code fences. It must have exactly these keys: "hook" (string), "cta" '
    '(string), "clip_captions" (an array of strings, exactly one per clip, in '
    "the same order the clips were given). The response must be parseable by "
    "a strict JSON parser as-is."
)


@dataclass(frozen=True)
class PlannedClip:
    """One clip in the assembled Reel, in final playback order -
    inherits its timing directly from an already-detected
    HighlightCandidate (this module never invents a new start/end
    time)."""

    source_start_seconds: float
    source_end_seconds: float
    transcript_text: str
    caption_text: str

    @property
    def duration_seconds(self) -> float:
        return self.source_end_seconds - self.source_start_seconds


@dataclass(frozen=True)
class ReelEditPlan:
    clips: list[PlannedClip]
    hook: str
    cta: str
    target_duration_seconds: int
    style: str
    pacing: str
    total_duration_seconds: float
    silence_removal_suggested: bool
    """True if the module brief's optional "silence removal" would
    meaningfully shorten this plan (i.e. the selected clips' source
    windows contain measurable silence gaps) - a suggestion surfaced
    to the person, not something this stage applies automatically to
    the timing above (see this module's own docstring for why)."""
    zoom_crop_suggested: bool
    """True if any selected clip's source aspect ratio isn't already
    9:16 - a flag that a zoom/crop step (a later stage's job, alongside
    the timeline editor) would improve the export's framing, not
    something this stage computes an actual crop rectangle for."""
    insufficient_data: bool
    message: str | None
    """Same insufficient-data convention as
    jarvis.video_studio.highlights.HighlightResult and
    jarvis.video_studio.analysis.VideoAnalysis.error."""

    @staticmethod
    def from_dict(data: dict) -> "ReelEditPlan":
        """See jarvis.video_studio.highlights.HighlightResult
        .from_dict()'s docstring for why a bare ReelEditPlan(**data) is
        wrong - `clips` would be left as plain dicts, not PlannedClip
        instances."""
        return ReelEditPlan(
            clips=[PlannedClip(**c) for c in data["clips"]],
            hook=data["hook"], cta=data["cta"],
            target_duration_seconds=data["target_duration_seconds"], style=data["style"],
            pacing=data["pacing"], total_duration_seconds=data["total_duration_seconds"],
            silence_removal_suggested=data["silence_removal_suggested"],
            zoom_crop_suggested=data["zoom_crop_suggested"],
            insufficient_data=data["insufficient_data"], message=data["message"],
        )


def _empty_plan(target_duration: int, style: str, pacing: str, message: str) -> ReelEditPlan:
    return ReelEditPlan(
        clips=[], hook="", cta="", target_duration_seconds=target_duration, style=style, pacing=pacing,
        total_duration_seconds=0.0, silence_removal_suggested=False, zoom_crop_suggested=False,
        insufficient_data=True, message=message,
    )


def _select_candidates(
    candidates: list[HighlightCandidate], target_duration_seconds: int,
) -> list[HighlightCandidate]:
    """Greedily selects candidates, highest confidence first (ties
    broken by original/chronological order), until the target duration
    is reached or every candidate has been considered - a measurable,
    deterministic selection rule, not an LLM judgment call (the LLM's
    only job in this module is writing the hook/CTA/captions for
    whatever gets selected here).

    A candidate longer than the remaining budget is TRIMMED (its tail
    cut off, keeping the start - where a clip's opening statement/hook
    material lives) to fit, rather than rejected outright - per the
    module's brief ("trimming" is one of the edit's own listed
    components). This means a trimmed PlannedClip's transcript_text may
    run slightly longer than what its trimmed timing actually covers
    (this module has no word-level timestamp to cut the text at
    exactly the trim point) - acceptable here since transcript_text is
    only ever shown as descriptive context, never re-transcribed or
    burned into the export as subtitles from this trimmed value."""
    confidence_rank = {"high": 0, "medium": 1, "low": 2}
    ranked = sorted(
        enumerate(candidates), key=lambda pair: (confidence_rank.get(pair[1].confidence, 3), pair[0]),
    )

    selected: list[HighlightCandidate] = []
    total = 0.0
    max_total = target_duration_seconds * 1.2
    for _, candidate in ranked:
        if total >= target_duration_seconds:
            break
        remaining_budget = max_total - total
        if remaining_budget <= 0:
            break
        if candidate.duration_seconds <= remaining_budget:
            selected.append(candidate)
            total += candidate.duration_seconds
        else:
            trimmed = HighlightCandidate(
                start_seconds=candidate.start_seconds,
                end_seconds=candidate.start_seconds + remaining_budget,
                transcript_text=candidate.transcript_text, reason=candidate.reason,
                suggested_hook=candidate.suggested_hook, confidence=candidate.confidence,
            )
            selected.append(trimmed)
            total += trimmed.duration_seconds

    # Preserve chronological (source video) order for the final
    # sequence, regardless of the confidence-based selection order
    # above - a Reel that jumps backward in the source video reads as
    # disjointed; a person can still see WHY each clip was picked
    # (its own reason/confidence, unchanged from highlight detection).
    selected.sort(key=lambda c: c.start_seconds)
    return selected


def _extract_json(text: str) -> Any | None:
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def generate_reel_edit(
    llm: LLMClient, candidates: list[HighlightCandidate], *,
    target_duration_seconds: int, style: str, pacing: str,
    source_has_silence_gaps: bool = False, source_is_vertical: bool = False,
) -> ReelEditPlan:
    """The module's main entry point (module brief's `generate_reel_edit()`
    in the AI + Video Processing Architecture list). Never raises -
    every failure mode (no candidates, LLM call failure, malformed
    response) comes back via ReelEditPlan.insufficient_data/message."""
    if target_duration_seconds not in TARGET_DURATIONS:
        target_duration_seconds = min(TARGET_DURATIONS, key=lambda d: abs(d - target_duration_seconds))
    if not candidates:
        return _empty_plan(
            target_duration_seconds, style, pacing,
            "No highlight candidates are available yet - run Find Highlights first.",
        )

    selected = _select_candidates(candidates, target_duration_seconds)
    if not selected:
        return _empty_plan(
            target_duration_seconds, style, pacing,
            "None of the detected highlight candidates fit the requested duration.",
        )

    excerpt_lines = [f"{i}. {c.transcript_text}" for i, c in enumerate(selected)]
    prompt = (
        f"Style: {style}\nPacing: {pacing}\n\nSelected clips, in order:\n\n"
        + "\n\n".join(excerpt_lines) + f"\n\n{_JSON_INSTRUCTION}"
    )

    try:
        response = llm.send(
            [{"role": "user", "content": prompt}], [],
            max_tokens=_MAX_GENERATION_TOKENS, system=_SYSTEM_PROMPT,
        )
    except Exception as e:
        return _empty_plan(target_duration_seconds, style, pacing, f"AI planning failed: {e}")

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    parsed = _extract_json(text) if text else None
    if not isinstance(parsed, dict):
        return _empty_plan(
            target_duration_seconds, style, pacing, "AI planning didn't return a usable result - try again.",
        )

    hook = parsed.get("hook")
    cta = parsed.get("cta")
    clip_captions = parsed.get("clip_captions")
    if (
        not isinstance(hook, str) or not hook.strip()
        or not isinstance(cta, str) or not cta.strip()
        or not isinstance(clip_captions, list) or len(clip_captions) != len(selected)
        or not all(isinstance(c, str) for c in clip_captions)
    ):
        return _empty_plan(
            target_duration_seconds, style, pacing,
            "AI planning returned an incomplete result - try again.",
        )

    planned_clips = [
        PlannedClip(
            source_start_seconds=candidate.start_seconds, source_end_seconds=candidate.end_seconds,
            transcript_text=candidate.transcript_text, caption_text=caption.strip(),
        )
        for candidate, caption in zip(selected, clip_captions)
    ]
    total_duration = sum(c.duration_seconds for c in planned_clips)

    return ReelEditPlan(
        clips=planned_clips, hook=hook.strip(), cta=cta.strip(),
        target_duration_seconds=target_duration_seconds, style=style, pacing=pacing,
        total_duration_seconds=total_duration, silence_removal_suggested=source_has_silence_gaps,
        zoom_crop_suggested=source_is_vertical is False, insufficient_data=False, message=None,
    )
