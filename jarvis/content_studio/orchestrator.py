"""Stage 2 orchestration: "Create this Reel" / "Create this Post" /
"Create this Story" / "Create this Carousel" - turns one already-
generated ContentPlanItem into a REAL, genuine project in the existing,
unmodified jarvis.reel_generator or jarvis.design_studio module, and
links it to this Content Studio project (see jarvis.content_studio.db's
own docstring for why only a pointer, never a duplicated copy, is
stored here).

This module is a THIN ORCHESTRATOR, exactly like jarvis.reel_generator
.footage is for AI Video Studio's own pipeline (see that module's own
docstring for the identical "call the other module's real functions in
the real order its own dashboard already uses, never reimplement any
step" principle applied in a different direction) - it never
reimplements brief/script/storyboard/scene/cover/caption generation for
Reels, nor brief/render/variant generation for Designs. Every call here
is a verbatim, unmodified call into jarvis.reel_generator's or
jarvis.design_studio's own public functions - the exact same functions
jarvis.gui.views.reel_generator.dashboard/jarvis.gui.views.design_studio
.dashboard already call for a person using either module directly.

"Reel" creation runs the SAME brief -> script pipeline
jarvis.gui.views.reel_generator.dashboard._create_reel_concept() runs -
this module deliberately stops there (script generated, not yet
approved) rather than running the FULL Reel pipeline (storyboard,
scenes, cover, caption, export) unattended, because
jarvis.reel_generator's own module brief has a hard requirement: "The
video must NOT be generated before the user approves the script." That
approval gate is enforced by jarvis.reel_generator.db.approve_script()
requiring an explicit person action in the Reel Generator's own UI -
Content Studio's own "Create this Reel" action creates the LINKED
project and gets it to the reviewable script stage, then a person
continues that project directly in AI Reel Generator (module brief's
own "Preview / Regenerate / Edit / Approve / Export" actions, section
9, map onto that existing UI, not a reimplementation of it here) to
actually approve/export it - this respects jarvis.reel_generator's own
approval gate rather than working around it.

"Post"/"Story"/"Carousel" creation runs generate_design_brief() ->
render_design() - jarvis.design_studio's own module brief has no
approval-before-render gate (a design renders immediately, review
happens on the ALREADY-RENDERED image, unlike a Reel's script-before-
any-visual-exists gate) - so these three content types DO reach a fully
rendered, viewable design in one orchestration call, matching how
jarvis.gui.views.design_studio.dashboard._create_design() itself
behaves for a person using Design Studio directly.

Every function here returns a plain error string on failure (never
raises, never fakes success - module brief Stage 2 requirements 10/11)
or the linked project's own id/paths on success, following this
codebase's established "string on failure, tuple/object on success"
convention (see jarvis.gui.views.reel_generator.dashboard's own
_create_reel_concept() for the identical pattern)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from jarvis.content_studio.plan import ContentPlanItem
from jarvis.core.llm import LLMClient
from jarvis.design_studio import db as design_db
from jarvis.design_studio import storage as design_storage
from jarvis.design_studio.brief import generate_design_brief
from jarvis.design_studio.render import RenderError, RenderResult, render_design
from jarvis.design_studio.styles import AUTO_STYLE, resolve_style
from jarvis.reel_generator import db as reel_db
from jarvis.reel_generator import storage as reel_storage
from jarvis.reel_generator.brief import LANGUAGE_ENGLISH, LANGUAGE_LITHUANIAN, ReelBrief, generate_reel_brief
from jarvis.reel_generator.script import ReelScript, generate_reel_script

# jarvis.content_studio's own content_type vocabulary -> the matching
# jarvis.design_studio.brief.FORMAT_CHOICES entry. "reel" has no entry
# here - it is handled by create_reel_project() below, which orchestrates
# jarvis.reel_generator directly, not jarvis.design_studio.
_DESIGN_FORMAT_BY_CONTENT_TYPE = {"story": "story", "post": "post", "carousel": "carousel"}


def _request_text_for(item: ContentPlanItem) -> str:
    """Builds the plain-language request text jarvis.reel_generator
    .brief.generate_reel_brief()/jarvis.design_studio.brief
    .generate_design_brief() expect, grounded in the plan item's own
    already-generated angle/objective/CTA - never a generic placeholder
    (matching jarvis.reel_generator.caption/.instagram_handoff's own
    established "ground every generated request in the actual content"
    convention, applied here to the REQUEST text itself rather than a
    caption)."""
    return f"{item.angle} Objective: {item.objective}. CTA: {item.cta}."


def _has_real_content(item: ContentPlanItem) -> bool:
    """True if `item` actually has a non-empty angle - the field the
    request text is built around. jarvis.content_studio.plan
    .generate_content_plan()'s own validation already rejects an
    all-empty-field item, so this only matters for an item constructed
    outside that path (e.g. a caller bug, or a hand-built test item) -
    without this check, _request_text_for()'s own fixed "Objective: .
    CTA: ." scaffolding text would still look non-empty to
    generate_reel_brief()/generate_design_brief()'s own bare
    `if not request_text.strip()` check, letting the LLM fabricate a
    brief from punctuation alone instead of the caller ever finding out
    the item itself was empty (a real gap found by hand-testing this
    exact case, not by static review)."""
    return bool(item.angle.strip())


def _detect_language(text: str) -> str:
    """A minimal, honest heuristic - not a real language detector (none
    exists anywhere in this codebase, and adding one is out of scope
    for this stage). Looks only for Lithuanian-specific diacritic
    characters absent from English; anything without them defaults to
    English. This keeps the module brief's own "Keep the existing
    Lithuanian UI language behavior" requirement working for the
    overwhelmingly common case (Lithuanian topics contain their own
    diacritics almost immediately) without claiming a general-purpose
    language-detection capability this codebase doesn't have."""
    lithuanian_chars = set("ąčęėįšųūžĄČĘĖĮŠŲŪŽ")
    return LANGUAGE_LITHUANIAN if any(ch in lithuanian_chars for ch in text) else LANGUAGE_ENGLISH


@dataclass(frozen=True)
class ReelCreationResult:
    reel_generator_project_id: str
    brief: ReelBrief
    script: ReelScript


def create_reel_project(llm: LLMClient, item: ContentPlanItem) -> ReelCreationResult | str:
    """Orchestrates jarvis.reel_generator directly - creates a new Reel
    Generator project (jarvis.reel_generator.storage.create_project() +
    db.create_project_record(), the EXACT calls
    jarvis.gui.views.reel_generator.dashboard._create_reel_concept()
    itself makes) and generates its brief + script, stopping there per
    this module's own docstring (the script-approval gate belongs to
    jarvis.reel_generator, not this orchestrator). Returns a plain error
    string on any failure (empty item text, LLM failure, storage
    failure) - never raises, never fakes success."""
    if item.content_type != "reel":
        return f"'{item.content_type}' is not a Reel content type."
    if not _has_real_content(item):
        return "This content plan item has no angle to work from - regenerate the content plan."

    request_text = _request_text_for(item)
    language = _detect_language(request_text)

    try:
        project = reel_storage.create_project()
    except reel_storage.StorageError as e:
        return str(e)

    reel_db.create_project_record(project.project_id, request_text)

    brief = generate_reel_brief(llm, request_text, language=language)
    if brief is None:
        return "JARVIS couldn't create a Reel brief for this content plan item - try again."
    reel_db.save_brief(project.project_id, dataclasses.asdict(brief))

    script = generate_reel_script(llm, brief)
    if script is None:
        return "JARVIS created a Reel brief but couldn't fit a script into the selected duration - try again."
    reel_db.save_script(project.project_id, dataclasses.asdict(script))

    return ReelCreationResult(reel_generator_project_id=project.project_id, brief=brief, script=script)


@dataclass(frozen=True)
class DesignCreationResult:
    design_studio_project_id: str
    render_result: RenderResult


def create_design_project(llm: LLMClient, item: ContentPlanItem) -> DesignCreationResult | str:
    """Orchestrates jarvis.design_studio directly, for "post"/"story"/
    "carousel" content types - creates a new Design Studio project
    (jarvis.design_studio.storage.create_project() + db
    .create_project_record(), the EXACT calls
    jarvis.gui.views.design_studio.dashboard._create_design() itself
    makes) and renders it (generate_design_brief() -> render_design()),
    a fully viewable design in one call (see this module's own docstring
    for why Design Studio's own module has no approval-before-render
    gate the way Reel Generator does). Returns a plain error string on
    any failure - never raises, never fakes success."""
    design_format = _DESIGN_FORMAT_BY_CONTENT_TYPE.get(item.content_type)
    if design_format is None:
        return f"'{item.content_type}' is not a Post/Story/Carousel content type."
    if not _has_real_content(item):
        return "This content plan item has no angle to work from - regenerate the content plan."

    request_text = _request_text_for(item)

    try:
        project = design_storage.create_project()
    except design_storage.StorageError as e:
        return str(e)

    design_db.create_project_record(project.project_id, request_text)

    brief = generate_design_brief(llm, request_text, forced_format=design_format, forced_style=AUTO_STYLE)
    if brief is None:
        return "JARVIS couldn't create a design brief for this content plan item - try again."
    design_db.save_brief(project.project_id, dataclasses.asdict(brief))

    style = resolve_style(brief.style)
    output_path = project.variants_dir / "design.jpg"
    try:
        render_result = render_design(brief, style, output_path=output_path)
    except RenderError as e:
        return f"Couldn't render the design: {e}"
    design_db.save_design_path(project.project_id, str(render_result.output_path))

    return DesignCreationResult(design_studio_project_id=project.project_id, render_result=render_result)
