"""Content Package + Ready to Publish stage (module brief): "After the
Reel is approved, JARVIS should automatically prepare a complete
Instagram content package... Publishing must remain a separate explicit
action."

This module is deliberately NOT jarvis.reel_generator.content_package
(that module's own ContentPackage is a different, earlier concept - a
set of ADDITIONAL promotional graphics rendered via AI Design Studio:
cover/story/post/carousel/story-CTA images - see its own docstring) and
is NOT jarvis.reel_generator.instagram_handoff (that module's job is
SENDING already-approved content into Instagram AI Manager's own
Content Studio, a later, separate stage this module's own PUBLISH_
APPROVED checkpoint exists specifically to gate). This module sits
BETWEEN them: a real, editable, persisted review layer over the Reel's
own already-generated assets (export/cover/caption/hashtags/voiceover),
so a person can review and adjust everything in one place before ever
reaching the Instagram hand-off.

No new AI-generation logic of its own: every generation call here
delegates directly to the exact same already-existing, already-tested
functions this project's earlier stages use -
jarvis.reel_generator.caption.generate_reel_caption_package() (caption +
hashtags, grounded in the real approved script - see that module's own
docstring), jarvis.reel_generator.cover.generate_cover_text()/
render_cover() (the cover image), and jarvis.reel_generator
.instagram_handoff._suggested_posting_time() (the real, connected-
Instagram-data-based posting-time recommendation - reused via a public
re-export below, never reimplemented). This module's only genuinely new
work is: (1) making caption/hashtag TEXT user-editable with an explicit
GENERATED-vs-EDITED distinction (module brief section 6: "If the
caption or hashtags are edited manually, do NOT overwrite them
automatically during another preview refresh"), (2) letting a person
pick ANY real Reel-scene frame as the cover instead of only the
generated cover image (module brief section 1B), and (3) the package-
level readiness/approval state machine (CONTENT_PACKAGE_READY -> READY_
TO_PUBLISH -> PUBLISH_APPROVED) - reusing jarvis.reel_generator
.project_status's own ProjectStatus enum (module brief section 5: "Reuse
the existing state architecture... do not introduce unnecessary enums"),
never a second competing status system.

Persistence: jarvis.reel_generator.db.save_publish_package()/
get_project().publish_package_data stores this module's own
PublishPackage (as JSON via dataclasses.asdict()) - ONE new column,
never duplicating cover_path/caption_data/export_path/voiceover_path
(the project record's own existing columns remain the single source of
truth for those underlying assets; this package only references them by
path/value and adds what's genuinely new - see jarvis.reel_generator.db's
own docstring for the full reasoning)."""

from __future__ import annotations

from dataclasses import dataclass, replace

from jarvis.core.llm import LLMClient
from jarvis.reel_generator import instagram_handoff
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.caption import ReelCaptionPackage, generate_reel_caption_package
from jarvis.reel_generator.project_status import ProjectStatus
from jarvis.reel_generator.script import ReelScript

# The module brief's own explicit package-status vocabulary (section 5:
# "GENERATING_CONTENT_PACKAGE... CONTENT_PACKAGE_READY... READY_TO_
# PUBLISH... PUBLISH_APPROVED" - reused directly as this module's own
# free-text status field, same "small, closed set of plain string
# values" convention as db.py's own pre-existing text_mode/status
# columns, not a competing enum to jarvis.reel_generator.project_status
# .ProjectStatus (that module's own richer, GUI-facing labels are
# derived FROM a PublishPackage's own state - see
# compute_publish_status() at the bottom of this module - rather than
# this module inventing a second source of truth for "what stage is
# this at").
STATUS_NOT_STARTED = "not_started"
STATUS_GENERATING = "generating"
STATUS_READY = "ready"

# Module brief section 1B: "ability to select another frame from the
# Reel as cover if supported" - the two cover sources a person can
# choose between; "generated" is the AI Design Studio cover image
# jarvis.reel_generator.cover already renders, "reel_frame" is a real
# frame extracted from the assembled export
# (jarvis.gui.views.reel_generator.dashboard's own Reel Preview stage
# ._reel_preview_thumbnail_path() - reused via the cover_path this
# module is simply given, never re-implemented here).
COVER_SOURCE_GENERATED = "generated"
COVER_SOURCE_REEL_FRAME = "reel_frame"


@dataclass(frozen=True)
class PublishPackage:
    """The Content Package's own persisted review/edit/approval state -
    see this module's own docstring for the full reasoning on what is
    and isn't duplicated here."""

    status: str = STATUS_NOT_STARTED  # STATUS_NOT_STARTED/STATUS_GENERATING/STATUS_READY

    caption_text: str = ""
    caption_edited: bool = False
    """True once a person has hand-edited the caption - module brief
    section 6's own "do NOT overwrite them automatically during another
    preview refresh" - see apply_generated_caption_and_hashtags() below
    for the exact rule this flag enforces."""

    hashtags_text: str = ""
    hashtags_edited: bool = False

    cover_path: str | None = None
    cover_source: str = COVER_SOURCE_GENERATED  # COVER_SOURCE_GENERATED or COVER_SOURCE_REEL_FRAME
    cover_scene_number: int | None = None
    """Set only when cover_source == COVER_SOURCE_REEL_FRAME - which
    scene's own real frame was picked, purely informational (the actual
    image is cover_path, already a real file on disk)."""

    suggested_posting_time: str | None = None
    posting_timezone: str = ""
    """The person's own free-text timezone label (e.g. "America/New_York"
    or "UTC+2") - module brief section 1F's "timezone" field. Left blank
    (not guessed) unless the person sets it; jarvis.reel_generator
    .instagram_handoff._suggested_posting_time()'s own recommendation
    string already names a day/hour, not a timezone, so there is no
    real value to auto-fill this from."""

    publish_approved: bool = False
    publish_approved_at: str | None = None

    generated_at: str | None = None
    """When this package was FIRST generated (module brief section 7:
    "timestamps") - set once by generate_publish_package(), never
    touched again by a later edit/regenerate of one piece."""

    @property
    def is_ready(self) -> bool:
        """CONTENT_PACKAGE_READY (module brief section 5): every
        required piece has real content - a Reel export path is NOT
        checked here (this package doesn't carry its own copy of
        export_path; the caller already knows whether one exists from
        the same ProjectRecord it read cover_path/caption_data from -
        see compute_publish_status() below, which takes export_path as
        its own explicit parameter for exactly this reason)."""
        return bool(self.caption_text.strip()) and self.cover_path is not None

    @property
    def is_ready_to_publish(self) -> bool:
        """READY_TO_PUBLISH (module brief section 5) - the package is
        complete AND has not yet been approved for publishing. A
        already-approved package is PUBLISH_APPROVED instead (a later,
        not-earlier state), so this is deliberately False once
        publish_approved is True."""
        return self.is_ready and not self.publish_approved


def apply_generated_caption_and_hashtags(
    package: PublishPackage, caption_package: ReelCaptionPackage,
) -> PublishPackage:
    """Folds a freshly-generated ReelCaptionPackage into an existing
    PublishPackage - module brief section 6's own hard rule: a caption/
    hashtags field the person has hand-edited (its own `_edited` flag is
    True) is NEVER overwritten by this, even when the underlying Reel
    caption is regenerated; only a field still in its GENERATED state
    picks up the new text. Called both on first generation (an empty,
    all-defaults PublishPackage passed in) and after a person clicks
    "Regenerate Caption"/"Regenerate Hashtags" elsewhere in the Reel
    pipeline (this function itself never calls any LLM - see this
    module's own docstring)."""
    updates: dict[str, object] = {}
    if not package.caption_edited:
        caption_text = ""
        if caption_package.caption is not None:
            caption_text = caption_package.caption.get("medium_caption", "") or ""
        updates["caption_text"] = caption_text
    if not package.hashtags_edited:
        hashtags_text = ""
        if caption_package.hashtags is not None:
            all_hashtags = [h for group in caption_package.hashtags.values() for h in group]
            hashtags_text = " ".join(all_hashtags)
        updates["hashtags_text"] = hashtags_text
    return replace(package, **updates) if updates else package


def edit_caption(package: PublishPackage, caption_text: str) -> PublishPackage:
    """A person's own explicit caption edit - marks caption_edited=True
    so it survives any later regeneration (see
    apply_generated_caption_and_hashtags()'s own docstring)."""
    return replace(package, caption_text=caption_text, caption_edited=True)


def edit_hashtags(package: PublishPackage, hashtags_text: str) -> PublishPackage:
    """A person's own explicit hashtag edit - same generated-vs-edited
    protection as edit_caption()."""
    return replace(package, hashtags_text=hashtags_text, hashtags_edited=True)


def edit_posting_time(package: PublishPackage, *, posting_time: str, timezone: str = "") -> PublishPackage:
    """A person's own explicit override of the suggested posting date/
    time (and optionally its timezone label) - module brief section 6's
    editable fields include "suggested publishing time." Once a person
    sets this, it is simply the package's own current value going
    forward - there is no separate "regenerate posting time" action
    (jarvis.reel_generator.instagram_handoff._suggested_posting_time()'s
    own live-Instagram-data recommendation can always be re-applied by
    calling generate_publish_package() again with a fresh package)."""
    return replace(package, suggested_posting_time=posting_time, posting_timezone=timezone)


def select_cover(package: PublishPackage, *, cover_path: str, source: str, scene_number: int | None = None) -> PublishPackage:
    """A person's own explicit cover choice - either the AI-generated
    cover image (source=COVER_SOURCE_GENERATED) or a real frame picked
    from a specific Reel scene (source=COVER_SOURCE_REEL_FRAME,
    scene_number identifying which one) - module brief section 1B."""
    return replace(package, cover_path=cover_path, cover_source=source, cover_scene_number=scene_number)


def approve_for_publishing(package: PublishPackage, *, now_iso: str) -> PublishPackage:
    """APPROVE FOR PUBLISHING (module brief section 4) - moves the
    package to PUBLISH_APPROVED. Does NOT call any Instagram API and
    does NOT touch jarvis.reel_generator.instagram_handoff in any way -
    this is purely a persisted state change the FUTURE Instagram-
    publishing stage will read (module brief: "This creates a clean
    boundary for the future Instagram integration"). The caller (the
    GUI) is responsible for confirming package.is_ready first - same
    "GUI enforces preconditions, this function just writes" split as
    jarvis.reel_generator.db.approve_reel()."""
    return replace(package, publish_approved=True, publish_approved_at=now_iso)


def unapprove_for_publishing(package: PublishPackage) -> PublishPackage:
    """Clears publish_approved (and its timestamp) - called whenever
    something the approval depended on changes after the fact (e.g. the
    Reel export is regenerated, or the cover/caption changes materially
    enough that the previously-approved package no longer describes what
    would actually be sent) - same "a fresh/changed result must be
    re-approved, never silently inherit an old approval" reasoning as
    jarvis.reel_generator.db.unlock_reel_approval()."""
    return replace(package, publish_approved=False, publish_approved_at=None)


def generate_publish_package(
    llm: LLMClient, *, brief: ReelBrief, script: ReelScript, project_id: str,
    existing: PublishPackage | None, cover_path: str | None, now_iso: str,
) -> tuple[PublishPackage, ReelCaptionPackage]:
    """The Content Package's own "GENERATE CONTENT PACKAGE" step (module
    brief section 5: REEL_APPROVED -> GENERATING_CONTENT_PACKAGE ->
    CONTENT_PACKAGE_READY). Reuses jarvis.reel_generator.caption
    .generate_reel_caption_package() directly (the SAME caption/hashtag
    generation the Reel's own standalone caption step already uses - no
    new LLM-calling logic here) and applies its result through
    apply_generated_caption_and_hashtags() (so a previous hand-edit
    survives). `cover_path`, if given, is folded in as-is (the caller
    already has the Reel's own generated cover_path from ProjectRecord -
    this function does not render a NEW cover; use
    jarvis.reel_generator.cover.render_cover() directly for that, exactly
    as the Reel's own standalone "Regenerate Cover" button already does).
    `existing`, if given, is the project's PREVIOUS PublishPackage (so a
    re-generate preserves cover_source/publish_approved/etc. rather than
    starting over) - pass None only for a project's first-ever content-
    package generation. Returns (package, caption_package) - the caller
    persists BOTH: the caption_package via the SAME db.save_caption() the
    Reel's own standalone caption step already calls (so the two views
    of "this Reel's caption" never drift apart), and the package via
    db.save_publish_package(). Never raises - a caption-generation
    failure simply leaves caption_text/hashtags_text at whatever they
    already were (matching generate_reel_caption_package()'s own "return
    None on failure, don't raise" contract)."""
    base = existing if existing is not None else PublishPackage()
    caption_package = generate_reel_caption_package(llm, brief, script)
    package = apply_generated_caption_and_hashtags(base, caption_package)

    if package.cover_path is None and cover_path is not None:
        package = select_cover(package, cover_path=cover_path, source=COVER_SOURCE_GENERATED)

    if package.suggested_posting_time is None:
        package = replace(package, suggested_posting_time=instagram_handoff._suggested_posting_time())

    package = replace(
        package, status=STATUS_READY,
        generated_at=package.generated_at or now_iso,
    )
    return package, caption_package


def compute_publish_status(
    package: PublishPackage | None, *, reel_approved: bool, export_path: str | None,
    is_generating_package: bool = False,
) -> ProjectStatus:
    """Derives this stage's own slice of jarvis.reel_generator
    .project_status.ProjectStatus (module brief section 5: "Reuse the
    existing state architecture... do not create duplicate state
    systems") from a project's PublishPackage - same "pure, read-only
    derivation from already-persisted state" convention as
    project_status.compute_reel_status() itself.

    - REEL_APPROVED: `reel_approved` is False, or True but no package
      has been generated yet (package is None or still
      STATUS_NOT_STARTED) - the entry point into this stage, matching
      the module brief's own workflow diagram exactly (REEL_APPROVED is
      simultaneously the PRECONDITION for this stage and its own first
      state, reused as-is - see ProjectStatus's own docstring).
    - GENERATING_CONTENT_PACKAGE: `is_generating_package=True` (a live
      "GENERATE CONTENT PACKAGE" background call is actively running
      right now - the one signal this function cannot infer from
      persisted state alone, same reasoning as compute_reel_status()'s
      own is_generating_reel parameter).
    - READY_TO_PUBLISH: the package exists, package.is_ready is True
      (real caption + cover), and it has not been approved yet. The
      module brief's own workflow diagram lists CONTENT_PACKAGE_READY
      and READY_TO_PUBLISH as sequential steps of one underlying "the
      package is complete, go review it" condition (this module's own
      PublishPackage has no separate "the person has finished
      reviewing" flag to distinguish them further) - this function
      returns READY_TO_PUBLISH for that condition; CONTENT_PACKAGE_READY
      remains in the enum for a caller that wants to label the instant a
      GENERATE CONTENT PACKAGE click finishes distinctly from the
      broader "still awaiting approval" READY_TO_PUBLISH label (e.g. a
      brief success toast right after generation), without this
      function needing to track that transitional moment itself.
    - PUBLISH_APPROVED: package.publish_approved is True.

    Also requires a real Reel export (`export_path is not None`) for
    anything past REEL_APPROVED - module brief section 8's own invalid-
    transition rule ("cannot approve publishing if Reel is missing")
    applies to the whole stage, not only the final approval click; a
    package generated before an export existed (should not normally
    happen, since GENERATE CONTENT PACKAGE itself is gated on
    reel_approved which itself requires an export) is defensively still
    reported as REEL_APPROVED rather than any later state."""
    if not reel_approved or export_path is None:
        return ProjectStatus.REEL_APPROVED
    if is_generating_package:
        return ProjectStatus.GENERATING_CONTENT_PACKAGE
    if package is None or package.status == STATUS_NOT_STARTED:
        return ProjectStatus.REEL_APPROVED
    if package.publish_approved:
        return ProjectStatus.PUBLISH_APPROVED
    if package.is_ready:
        return ProjectStatus.READY_TO_PUBLISH
    return ProjectStatus.GENERATING_CONTENT_PACKAGE


__all__ = [
    "STATUS_NOT_STARTED", "STATUS_GENERATING", "STATUS_READY",
    "COVER_SOURCE_GENERATED", "COVER_SOURCE_REEL_FRAME",
    "PublishPackage",
    "apply_generated_caption_and_hashtags", "edit_caption", "edit_hashtags",
    "edit_posting_time", "select_cover", "approve_for_publishing", "unapprove_for_publishing",
    "generate_publish_package", "compute_publish_status",
]
