"""Project status labeling (Stage C / module brief's own explicit
status vocabulary): "status: DRAFT/STORYBOARD APPROVED/GENERATING/
READY FOR REVIEW/EXPORTED."

This is a DERIVED, read-only label - jarvis.reel_generator.db's own
`status` column keeps its existing, separately-tested free-text values
("created"/"brief_generated"/"script_generated"/"script_approved"/
"storyboard_generated"/"exported"/"plan_generated") completely
unchanged; nothing here writes to that column or changes what any
existing caller/test observes from it. compute_status() instead reads
the SAME already-persisted fields jarvis.gui.views.reel_generator
.dashboard.ReelGeneratorView._render_step_indicator() already reads
(script_approved, storyboard/visual presence, export path) - the exact
same "safe, additive, derived from existing state" pattern that step
indicator itself established - and produces exactly the module brief's
own 5-value vocabulary as a single ProjectStatus enum member, for
display wherever a person needs to see "what stage is this Reel at"
(Recent Reels cards, a project's own header) without inventing a
second, competing source of truth for project state.

`is_generating`, if given True by a live GUI view (the one case this
module CANNOT infer from a persisted ProjectRecord alone - "a
background render is currently running" is live thread state, never
written to the database), overrides the derived label with GENERATING
regardless of what the record alone would suggest - see
compute_status()'s own docstring."""

from __future__ import annotations

from enum import Enum

from jarvis.reel_generator.db import REEL_MODE_STATIC, ProjectRecord
from jarvis.reel_generator.scenes import SceneMotionClip


class ProjectStatus(Enum):
    DRAFT = "DRAFT"
    STORYBOARD_APPROVED = "STORYBOARD APPROVED"
    GENERATING = "GENERATING"
    READY_FOR_REVIEW = "READY FOR REVIEW"
    EXPORTED = "EXPORTED"

    # Reel Generation Workflow stage's own explicit state-machine
    # vocabulary (module brief: "Use explicit states rather than
    # implicit UI conditions" - STORYBOARD_APPROVED -> GENERATING_SCENES
    # -> SCENES_READY -> GENERATING_REEL -> REEL_READY -> REEL_APPROVED).
    # ADDED alongside the 5 values above rather than renaming/replacing
    # any of them - compute_status()/compute_status_from_visuals() and
    # every test asserting their exact 5-value vocabulary are completely
    # unchanged; see compute_reel_status() below for the richer entry
    # point that produces these new values instead.
    GENERATING_SCENES = "GENERATING SCENES"
    SCENES_READY = "SCENES READY"
    GENERATING_REEL = "GENERATING REEL"
    REEL_READY = "REEL READY"
    REEL_APPROVED = "REEL APPROVED"

    # Content Package + Ready to Publish stage's own explicit state
    # machine (module brief section 5: REEL_APPROVED ->
    # GENERATING_CONTENT_PACKAGE -> CONTENT_PACKAGE_READY ->
    # READY_TO_PUBLISH -> PUBLISH_APPROVED) - ADDED alongside every
    # value above, same "reuse this one enum, never a second competing
    # status system" convention the Reel Generation Workflow stage's own
    # GENERATING_SCENES/etc. already established (see this enum's own
    # docstring above for that precedent). REEL_APPROVED is reused
    # as-is (already exists above) rather than duplicated - it is
    # simultaneously "the Reel itself is approved" AND "the entry point
    # into this stage", exactly per the module brief's own workflow
    # diagram. See jarvis.reel_generator.publish_package
    # .compute_publish_status() below for the function that derives
    # these from a PublishPackage.
    GENERATING_CONTENT_PACKAGE = "GENERATING CONTENT PACKAGE"
    CONTENT_PACKAGE_READY = "CONTENT PACKAGE READY"
    READY_TO_PUBLISH = "READY TO PUBLISH"
    PUBLISH_APPROVED = "PUBLISH APPROVED"

    # NATURAL MOTION / HYBRID modes' own additional states - ADDED
    # alongside every value above, same "reuse this one enum, never a
    # second competing status system" convention. Only ever produced by
    # compute_motion_status() below, and only ever consulted by the GUI
    # when jarvis.reel_generator.db.ProjectRecord.reel_mode is NOT
    # REEL_MODE_STATIC - a Static-mode project's own status always comes
    # from compute_reel_status()/compute_status_from_visuals() exactly
    # as before this feature existed, never from these two values.
    GENERATING_MOTION = "GENERATING MOTION"
    MOTION_PARTIAL = "MOTION PARTIAL"

    def __str__(self) -> str:
        return self.value


def compute_status(record: ProjectRecord, *, is_generating: bool = False) -> ProjectStatus:
    """Derives the module brief's own 5-value project status from an
    already-persisted ProjectRecord (see this module's own docstring
    for why this is a pure, read-only derivation rather than a second
    persisted status column):

    - EXPORTED: record.export_path is set (a real export exists,
      regardless of whether the storyboard/visuals were touched again
      since - re-approving/regenerating a scene after export does NOT
      revert this label; module brief's own "EXPORTED" is a simple "has
      this Reel ever been finished" marker, matching how export_path
      itself is never cleared once set).
    - GENERATING: `is_generating=True` was passed (a live GUI view's own
      "a background scene-visual render is actively running right
      now" signal - see this function's own docstring) OR the
      storyboard has at least one scene whose visual either doesn't
      exist yet or failed to render (storyboard_data is set, but no
      complete visual set exists) - either way, there is real
      generation work outstanding for this Reel.
    - READY FOR REVIEW: a storyboard exists and its scene count matches
      how many scenes this function can confirm succeeded, via
      `rendered_scene_count`/`failed_scene_count` (see below) - the
      caller passes these two counts explicitly (this module has no
      access to jarvis.reel_generator.scenes/.scene_render's own
      SceneVisual objects, which are never persisted verbatim on
      ProjectRecord - only their PATHS/derived state are), since
      "every scene has a real, successfully rendered visual, no scene
      is missing or errored" is exactly READY FOR REVIEW's own
      definition and GENERATING's own negation.
    - STORYBOARD APPROVED: record.storyboard_approved is True (the
      Storyboard Creative Controls stage's own explicit "APPROVE
      STORYBOARD" lock - see jarvis.reel_generator.db's own docstring)
      and storyboard_data is set, but no scene visuals have been
      attempted yet (rendered_scene_count == 0 and
      failed_scene_count == 0). PRIOR to that stage existing, this
      label was derived from script_approved alone (no separate
      storyboard-level approval existed yet) - now that
      storyboard_approved is a real, explicit flag, it is the more
      precise signal and is used instead: a storyboard that exists but
      has NOT yet been explicitly approved is still DRAFT, even if the
      script itself was approved (matches the module brief's own "lock
      the storyboard... enable GENERATE REEL" - the label should say
      STORYBOARD APPROVED only once that lock has genuinely happened).
    - DRAFT: anything earlier (script not yet approved, no storyboard
      yet, or a storyboard that exists but hasn't been approved).
    """
    if record.export_path is not None:
        return ProjectStatus.EXPORTED

    if is_generating:
        return ProjectStatus.GENERATING

    if record.storyboard_data is None or not record.storyboard_approved:
        return ProjectStatus.DRAFT

    # A ProjectRecord alone has no way to tell "every scene has a real,
    # successfully rendered visual" from "nothing has been rendered
    # yet" (SceneVisual objects - success/failure per scene - are never
    # persisted verbatim on the record, only their output PATHS are, and
    # only implicitly via files on disk) - so a plain record can only
    # ever say STORYBOARD APPROVED once approved+storyboarded, never
    # READY FOR REVIEW/GENERATING. A caller that DOES have the real
    # SceneVisual list (a live GUI view) should call
    # compute_status_from_visuals() instead, which resolves this
    # correctly.
    return ProjectStatus.STORYBOARD_APPROVED


def compute_status_from_visuals(
    record: ProjectRecord, *, total_scenes: int, rendered_scene_count: int, failed_scene_count: int,
    is_generating: bool = False,
) -> ProjectStatus:
    """The richer entry point a live GUI view uses (it has real
    SceneVisual objects in memory - jarvis.gui.views.reel_generator
    .dashboard.ReelGeneratorView._current_visuals - that a plain
    ProjectRecord alone can't provide): same EXPORTED/GENERATING/DRAFT
    rules as compute_status() above, but READY FOR REVIEW vs GENERATING
    for an approved storyboard is decided from the REAL counts instead
    of guessing from record fields alone.

    - `total_scenes`: the storyboard's own scene count.
    - `rendered_scene_count`: how many scenes have a successfully
      rendered visual (no error, a real image_path) RIGHT NOW.
    - `failed_scene_count`: how many scenes have an attempted-but-
      failed visual RIGHT NOW.

    READY FOR REVIEW only when rendered_scene_count == total_scenes > 0
    and failed_scene_count == 0 (every scene done, none failed).
    GENERATING when the storyboard is approved and exists but that
    condition doesn't hold yet (nothing rendered yet, some scenes
    failed, or rendering is only partially complete) - this Reel
    genuinely still has generation work outstanding. Uses
    record.storyboard_approved (not script_approved) for the same
    reason compute_status() above does - see that function's own
    docstring."""
    if record.export_path is not None:
        return ProjectStatus.EXPORTED
    if is_generating:
        return ProjectStatus.GENERATING
    if record.storyboard_data is None or not record.storyboard_approved:
        return ProjectStatus.DRAFT
    if total_scenes > 0 and rendered_scene_count == total_scenes and failed_scene_count == 0:
        return ProjectStatus.READY_FOR_REVIEW
    if rendered_scene_count > 0 or failed_scene_count > 0:
        return ProjectStatus.GENERATING
    return ProjectStatus.STORYBOARD_APPROVED


def compute_reel_status(
    record: ProjectRecord, *, total_scenes: int, rendered_scene_count: int, failed_scene_count: int,
    is_generating_scenes: bool = False, is_generating_reel: bool = False,
) -> ProjectStatus:
    """Reel Generation Workflow stage's own richer entry point: the same
    underlying signals compute_status_from_visuals() already reads
    (record.storyboard_approved/storyboard_data/export_path,
    rendered/failed scene counts), but resolved into the module brief's
    own explicit 6-state machine (STORYBOARD_APPROVED -> GENERATING_
    SCENES -> SCENES_READY -> GENERATING_REEL -> REEL_READY ->
    REEL_APPROVED) instead of the older 5-value DRAFT/.../EXPORTED
    vocabulary - see this module's own docstring for why BOTH entry
    points exist side by side rather than one replacing the other:
    compute_status()/compute_status_from_visuals() remain exactly as
    they were for every existing caller/test; this is an ADDITIONAL,
    parallel view of the same state for the new workflow stage's own
    GUI section, never a replacement.

    - DRAFT: storyboard doesn't exist yet or isn't approved (identical
      rule to compute_status_from_visuals()).
    - GENERATING_SCENES: `is_generating_scenes=True` (a live "GENERATE
      SCENE VISUALS" background render is actively running right now)
      OR the storyboard is approved but scene-visual rendering has
      started and is incomplete (some rendered/failed, not all).
    - SCENES_READY: every scene has a real, successfully rendered
      visual (rendered_scene_count == total_scenes > 0, failed_scene_
      count == 0), no Reel export exists yet, and reel generation isn't
      currently running.
    - GENERATING_REEL: `is_generating_reel=True` (a live "Export Final
      Reel" background render is actively running right now) - the
      Reel Assembly step (requirement 3: assemble scenes in storyboard
      order, 9:16, preserve durations/overlays/voiceover) is in
      progress.
    - REEL_READY: record.export_path is set (Reel Assembly finished -
      a real MP4 exists) but record.reel_approved is not yet True -
      the module brief's own "REVIEW REEL" checkpoint before approval.
    - REEL_APPROVED: record.export_path is set AND record.reel_approved
      is True - the explicit "APPROVE REEL" checkpoint (a SEPARATE,
      later lock from storyboard_approved) has been passed. This is
      the terminal state for THIS module - "ready for publishing" is
      the module brief's own description of what REEL_APPROVED enables
      (the person may now use the already-existing, always-manual
      "Send to Instagram Manager" hand-off - jarvis.reel_generator
      .instagram_handoff never publishes anything itself either way),
      not a 7th state this function returns.
    """
    if record.storyboard_data is None or not record.storyboard_approved:
        return ProjectStatus.DRAFT

    if record.export_path is not None:
        if is_generating_reel:
            return ProjectStatus.GENERATING_REEL
        return ProjectStatus.REEL_APPROVED if record.reel_approved else ProjectStatus.REEL_READY

    if is_generating_reel:
        return ProjectStatus.GENERATING_REEL

    if total_scenes > 0 and rendered_scene_count == total_scenes and failed_scene_count == 0:
        return ProjectStatus.SCENES_READY

    if is_generating_scenes or rendered_scene_count > 0 or failed_scene_count > 0:
        return ProjectStatus.GENERATING_SCENES

    return ProjectStatus.STORYBOARD_APPROVED


def compute_motion_status(
    record: ProjectRecord, clips: list[SceneMotionClip], *, is_generating: bool = False,
) -> ProjectStatus:
    """NATURAL MOTION / HYBRID modes' own status derivation - pure, read-
    only, exactly like compute_reel_status() above. The GUI calls this
    ONLY when `record.reel_mode != jarvis.reel_generator.db
    .REEL_MODE_STATIC` - a Static-mode project's status keeps coming
    from compute_reel_status()/compute_status_from_visuals() untouched;
    this function is never consulted for one.

    `clips` is the project's own current
    list[jarvis.reel_generator.scenes.SceneMotionClip] (deserialized
    from record.motion_clips_data by the caller - this function takes
    the already-deserialized list, not the raw JSON, to stay a plain,
    directly-testable pure function like every sibling compute_*()
    function here).

    - GENERATING_MOTION: `is_generating=True` (a live "GENERATE MOTION"
      background render is actively running right now) OR
      `reel_mode != static` but no clips have been generated/attempted
      yet at all (an empty `clips` list) - there is real motion-
      generation work outstanding for this Reel.
    - MOTION_PARTIAL: at least one requested clip failed (a SceneMotionClip
      with `error` set) while at least one other succeeded - matches
      requirement 6's "keep the original and show a clear error": the
      Reel is still usable (failed scenes fall back to their still
      image), but not every requested clip is real, so this is
      surfaced distinctly rather than silently reported as fully done.
    - Falls through to compute_reel_status()'s own return for this
      record when every requested clip succeeded (or every clip failed,
      which is reported as MOTION_PARTIAL too - see below) - motion
      generation is complete either way, and the Reel's own EXPORTED/
      REEL_READY/REEL_APPROVED state is unaffected by which scenes ended
      up static vs. moving, matching requirement 8's "preserve all
      existing functionality" for the export/approval workflow itself."""
    if record.reel_mode == REEL_MODE_STATIC:
        # Defensive - the GUI is expected to never call this for a
        # Static project at all (see this function's own docstring),
        # but a caller that does gets the exact same answer
        # compute_reel_status() alone would have given, never a
        # fabricated motion-specific state for a project that never
        # asked for one.
        return ProjectStatus.STORYBOARD_APPROVED if record.storyboard_data is not None else ProjectStatus.DRAFT

    if is_generating:
        return ProjectStatus.GENERATING_MOTION
    if not clips:
        return ProjectStatus.GENERATING_MOTION

    failed = [c for c in clips if c.error is not None]
    if failed:
        return ProjectStatus.MOTION_PARTIAL

    return ProjectStatus.STORYBOARD_APPROVED
