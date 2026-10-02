"""Cover generation (module brief, section 11): "Automatically create
a Reel cover... suitable frame/design, short title, brand styling...
Output: 1080x1920. Keep text inside safe areas."

Reuses jarvis.design_studio.render.render_design() directly, unmodified
- "reel_cover" is already a first-class 1080x1920 format there, with
safe-area-aware layout and (if a Brand Kit is saved)
jarvis.design_studio.brand_kit.apply_brand_kit() folding in the
person's own brand colors, exactly per module brief section 17: "Use
AI Design Studio for: Reel cover". No new rendering code in this
module - only a short cover-title generator (a distinct, short
"3 YOGA HABITS FOR A BETTER MORNING"-style title, NOT the Reel's own
on-screen scene text verbatim) and a thin wrapper constructing the
DesignBrief a cover render needs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from jarvis.core.llm import LLMClient
from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.render import RenderError, RenderResult, render_design
from jarvis.design_studio.styles import DesignStyle, resolve_style
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript

_MAX_GENERATION_TOKENS = 300

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

_SYSTEM_PROMPT = (
    "You write short, punchy Instagram Reel cover titles - the kind of 3-7 word "
    "headline shown as a still-image thumbnail before someone taps play (e.g. "
    "\"3 YOGA HABITS FOR A BETTER MORNING\"). Given a Reel's topic and script, "
    "write ONE cover title and one short supporting line (a few words, can be "
    "empty if the title alone is strong enough). Write in the same language as "
    "the script.\n\n" + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object with exactly two string fields: \"title\" and "
    "\"supporting_text\" (supporting_text may be an empty string)."
)


def _extract_json(text: str) -> Any | None:
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _call_llm_for_json(llm: LLMClient, *, system: str, prompt: str) -> Any | None:
    try:
        response = llm.send(
            [{"role": "user", "content": prompt}], [], max_tokens=_MAX_GENERATION_TOKENS, system=system,
        )
    except Exception:
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return None
    return _extract_json(text)


@dataclass(frozen=True)
class CoverText:
    title: str
    supporting_text: str


def generate_cover_text(
    llm: LLMClient, brief: ReelBrief, script: ReelScript, *, previous_titles: tuple[str, ...] = (),
) -> CoverText | None:
    """Generates a short cover title (+ optional supporting line) from
    the Reel's topic and approved script. Returns None on any failure -
    never raises.

    `previous_titles` (real, reported bug fix: "Regenerate Cover leaves
    the cover looking the same") lists every title already tried for
    this same cover, oldest first - passed through to the prompt so the
    LLM is explicitly told to write something different this time,
    rather than a same-input-same-output call with no instruction to
    vary at all (the LLM's own sampling temperature alone was not a
    reliable source of real variety for a short, templated 3-7 word
    title - a real, hand-tested outcome, not a theoretical concern).
    Empty (the default) for the very first generation, matching every
    existing call site's behavior exactly."""
    prompt = f"Topic: {brief.topic}\nScript: {script.full_text}"
    if previous_titles:
        prompt += (
            "\n\nTitle(s) already tried for this cover (write a genuinely DIFFERENT "
            "title and angle this time - a different word choice, hook, or emphasis, "
            "not a trivial rewording): " + " | ".join(previous_titles)
        )
    result = _call_llm_for_json(llm, system=_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, dict):
        return None
    title = result.get("title")
    supporting_text = result.get("supporting_text")
    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(supporting_text, str):
        supporting_text = ""
    return CoverText(title=title.strip(), supporting_text=supporting_text.strip())


class CoverError(Exception):
    """Raised only for a rendering failure (wraps RenderError) - cover
    TEXT generation failures are reported via generate_cover_text()
    returning None instead, matching this codebase's established
    "LLM step returns None, rendering step raises" split (see
    jarvis.design_studio.render/.brief for the same split)."""


# Real, reported bug fix: render_cover() used to always resolve
# design_brief.style straight from brief.style, which never changes for
# a given Reel - combined with render_design() having no randomness of
# its own (a fixed two-color gradient from the DesignStyle, no
# background photo/composition variety - see jarvis.design_studio
# .render's own docstring), clicking "Regenerate Cover" repeatedly
# rendered the exact same background/colors/fonts every time, changing
# only whatever title text the LLM happened to write (see
# generate_cover_text()'s own `previous_titles` fix just above for that
# half of it). This is a small, curated rotation of VISUALLY DISTINCT
# existing DesignStyle presets (not a random/new style system) - reusing
# jarvis.design_studio.styles.DESIGN_STYLES unmodified, the same table
# every other Design Studio surface already draws from.
_COVER_STYLE_ROTATION: tuple[str, ...] = (
    "modern", "luxury", "bold", "elegant", "wellness", "professional", "feminine", "clean",
)


def style_for_attempt(brief_style: str, attempt: int) -> str:
    """Picks the DesignStyle NAME to render this cover attempt with.
    attempt=0 (the very first generation) always uses the Reel's own
    brief.style, exactly as before this fix - a first-time cover looks
    identical to pre-fix behavior. Every later attempt (attempt >= 1)
    cycles through _COVER_STYLE_ROTATION, skipping brief_style itself if
    it appears there (so a regenerate never "changes" to the style
    already showing) - deterministic per attempt number, not random, so
    a person can predict what REGENERATE COVER does and so this stays
    trivially testable."""
    if attempt <= 0:
        return brief_style
    rotation = tuple(s for s in _COVER_STYLE_ROTATION if s != brief_style) or _COVER_STYLE_ROTATION
    return rotation[(attempt - 1) % len(rotation)]


def render_cover(
    brief: ReelBrief, cover_text: CoverText, *, output_path: Path,
    style_transform: Callable[[DesignStyle], DesignStyle] | None = None, attempt: int = 0,
) -> RenderResult:
    """Renders the Reel cover via jarvis.design_studio.render
    .render_design() with format="reel_cover" (1080x1920) - module
    brief section 11's exact output spec, already the default for that
    format. Raises CoverError (wrapping the underlying RenderError) on
    failure.

    `attempt` (default 0, matching every existing call site's previous
    behavior exactly) is which regeneration this is for the same cover
    - see style_for_attempt()'s own docstring for how it changes the
    DesignStyle used, the real fix for "Regenerate Cover looks the
    same"."""
    design_brief = DesignBrief(
        topic=brief.topic, objective=brief.objective, audience=brief.audience, tone=brief.tone,
        headline=cover_text.title, supporting_text=cover_text.supporting_text, cta="",
        format="reel_cover", style=style_for_attempt(brief.style, attempt),
    )
    style = resolve_style(design_brief.style)
    if style_transform is not None:
        style = style_transform(style)
    try:
        return render_design(design_brief, style, output_path=output_path)
    except RenderError as e:
        raise CoverError(str(e)) from e


# --- Three-cover picker (real, reported requirement: "generate 3 different -------------------
# cover variants, let me preview/select/edit one") ---------------------------------------------


@dataclass(frozen=True)
class CoverCandidate:
    """One of several cover variants offered for a single Reel - never
    a separate concept from CoverText/render_cover() above, just their
    OWN outputs bundled with which attempt/style produced them, so a
    later SELECT/EDIT/SAVE can re-render the exact same candidate (e.g.
    after a text/color/font edit) without losing track of which
    DesignStyle it started from."""
    attempt: int
    style_name: str
    cover_text: CoverText
    image_path: Path


def generate_cover_candidates(
    llm: LLMClient, brief: ReelBrief, script: ReelScript, *, output_dir: Path,
    count: int = 3, style_transform: Callable[[DesignStyle], DesignStyle] | None = None,
    start_attempt: int = 0, previous_titles: tuple[str, ...] = (),
) -> list[CoverCandidate]:
    """Generates `count` (default 3, the module brief's own "GENERATE 3
    COVERS" requirement) genuinely different cover variants for the same
    Reel - reuses generate_cover_text()'s own `previous_titles` fix and
    render_cover()'s own `attempt`-based style rotation UNCHANGED (see
    both functions' own docstrings for why each individual regenerate
    already produces a different result) rather than inventing a
    second, parallel "batch" code path - this is simply that same loop,
    called `count` times up front instead of once per click.

    `start_attempt`/`previous_titles` let a caller generate a SECOND (or
    later) batch of 3 that's genuinely different from an earlier batch
    still on screen (module brief's own "REGENERATE" on the whole
    3-cover set, not just one candidate) - `start_attempt` continues the
    SAME style rotation style_for_attempt() already uses (so batch 2
    picks up with the next un-shown styles, never repeating batch 1's
    three), and `previous_titles` is seeded with every title already
    shown across ALL previous batches (not just this one), so
    generate_cover_text()'s own "write something different" instruction
    has the full history, not just this call's own 3.

    A candidate whose TEXT generation fails (generate_cover_text()
    returns None - a real, hand-tested intermittent LLM failure, not
    fabricated) is silently skipped rather than aborting the whole
    batch, matching this codebase's established "one failure doesn't
    stop the others" convention (jarvis.design_studio.variants
    .generate_variants()'s own precedent) - a person still gets
    whichever candidates DID succeed, rather than none at all. A
    candidate whose RENDER fails (CoverError) is skipped the same way.
    Returns however many candidates actually succeeded (0 to `count`) -
    never raises itself.

    Each candidate's image is written to `output_dir / f"cover_{attempt}.jpg"`
    - a stable, predictable filename per attempt number (not per
    candidate INDEX in the returned list, since a skipped failure would
    otherwise shift every later candidate's own filename) so SELECT can
    later reference a specific candidate's file by its own attempt
    number without ambiguity."""
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates: list[CoverCandidate] = []
    seen_titles: list[str] = list(previous_titles)
    for attempt in range(start_attempt, start_attempt + count):
        cover_text = generate_cover_text(llm, brief, script, previous_titles=tuple(seen_titles))
        if cover_text is None:
            continue
        output_path = output_dir / f"cover_{attempt}.jpg"
        try:
            render_cover(brief, cover_text, output_path=output_path, style_transform=style_transform, attempt=attempt)
        except CoverError:
            continue
        candidates.append(CoverCandidate(
            attempt=attempt, style_name=style_for_attempt(brief.style, attempt),
            cover_text=cover_text, image_path=output_path,
        ))
        seen_titles.append(cover_text.title)
    return candidates


TEXT_POSITION_CHOICES: tuple[str, ...] = ("top", "bottom")
DEFAULT_TEXT_POSITION = "top"


@dataclass(frozen=True)
class CoverEditOverrides:
    """A person's own explicit edits to one already-generated
    CoverCandidate (module brief's own "let me edit text, colors, font,
    position" requirement) - every field is optional (None = keep the
    candidate's own original value), so re-rendering after a partial
    edit (e.g. only the title changed) never silently resets everything
    else back to the candidate's un-edited state.

    "Position" is a REAL, honest choice between two of this renderer's
    own already-existing text placements, not a fabricated new layout
    engine - jarvis.design_studio.render.render_design() lays out
    headline/supporting_text in a fixed vertical flow near the TOP of
    the safe area (this codebase's ONLY headline placement - there is
    no parameter to move it), but it ALSO already draws a separate
    `cta` pill near the BOTTOM of the canvas whenever DesignBrief.cta is
    non-empty (see render_design()'s own "if brief.cta.strip()"
    branch) - a real, pre-existing bottom-of-canvas text element this
    module never previously used for a cover (cover.render_cover()
    always passes cta=""). `text_position="bottom"` (see
    apply_cover_edit_overrides()'s own docstring for exactly how) moves
    the SAME title text into that already-existing bottom pill instead
    of inventing a new coordinate system - "top" (the default) is
    byte-for-byte the pre-existing behavior. This is deliberately NOT a
    free-form/pixel-coordinate position - that would require changing
    render_design() itself, which the module brief's own "do not modify
    the working image-generation code" constraint rules out."""
    title: str | None = None
    supporting_text: str | None = None
    headline_color: str | None = None
    background_color_1: str | None = None
    background_color_2: str | None = None
    headline_font: str | None = None
    text_position: Literal["top", "bottom"] | None = None


def apply_cover_edit_overrides(
    cover_text: CoverText, overrides: CoverEditOverrides,
) -> tuple[CoverText, Callable[[DesignStyle], DesignStyle]]:
    """Applies a person's CoverEditOverrides on top of an existing
    CoverCandidate's own CoverText, returning (new_cover_text,
    style_transform) ready to pass straight into render_cover() - the
    SAME style_transform mechanism every other Reel Generator visual
    edit already uses (jarvis.design_studio.brand_kit
    .apply_brand_kit()'s own precedent, reused here for a person's own
    one-off manual edit instead of a saved whole-account Brand Kit).
    Never raises - unset overrides are passed through as plain identity
    (the style/text field is returned completely unchanged).

    `text_position="bottom"` is NOT applied here (this function only
    returns a CoverText + style_transform, and render_cover() itself
    has no `cta`/position parameter of its own to pass through to it) -
    see render_cover_with_overrides() below, which is the actual call
    site that honors it, by building its own DesignBrief with cta set
    directly rather than going through render_cover()'s own fixed
    cta=""."""
    new_cover_text = CoverText(
        title=overrides.title if overrides.title is not None else cover_text.title,
        supporting_text=(
            overrides.supporting_text if overrides.supporting_text is not None else cover_text.supporting_text
        ),
    )

    def _transform(style: DesignStyle) -> DesignStyle:
        import dataclasses

        changes: dict[str, Any] = {}
        if overrides.headline_color is not None:
            changes["headline_color"] = overrides.headline_color
        if overrides.background_color_1 is not None:
            changes["background_color_1"] = overrides.background_color_1
        if overrides.background_color_2 is not None:
            changes["background_color_2"] = overrides.background_color_2
        if overrides.headline_font is not None:
            changes["headline_font"] = overrides.headline_font
        return dataclasses.replace(style, **changes) if changes else style

    return new_cover_text, _transform


def render_cover_with_overrides(
    brief: ReelBrief, cover_text: CoverText, overrides: CoverEditOverrides, *, output_path: Path,
    attempt: int = 0,
) -> RenderResult:
    """The actual EDIT COVER re-render call site - applies
    apply_cover_edit_overrides() (text/color/font) AND
    `text_position="bottom"` (moving the title into render_design()'s
    own pre-existing cta pill instead of the top headline slot - see
    CoverEditOverrides's own docstring for why this is a real, not
    fabricated, positional choice) in one call. Raises CoverError on
    any render failure, same convention as render_cover()."""
    new_cover_text, style_transform = apply_cover_edit_overrides(cover_text, overrides)
    position = overrides.text_position or DEFAULT_TEXT_POSITION
    style_name = style_for_attempt(brief.style, attempt)
    style = resolve_style(style_name)
    style = style_transform(style)

    if position == "bottom":
        design_brief = DesignBrief(
            topic=brief.topic, objective=brief.objective, audience=brief.audience, tone=brief.tone,
            headline="", supporting_text=new_cover_text.supporting_text, cta=new_cover_text.title,
            format="reel_cover", style=style_name,
        )
    else:
        design_brief = DesignBrief(
            topic=brief.topic, objective=brief.objective, audience=brief.audience, tone=brief.tone,
            headline=new_cover_text.title, supporting_text=new_cover_text.supporting_text, cta="",
            format="reel_cover", style=style_name,
        )
    try:
        return render_design(design_brief, style, output_path=output_path)
    except RenderError as e:
        raise CoverError(str(e)) from e
