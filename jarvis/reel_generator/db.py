"""SQLite metadata storage for AI Reel Generator - one row per Reel
project (jarvis.reel_generator.storage.ReelProject's id, the original
plain-language idea, the generated ReelBrief, the approved ReelScript,
and - later stages - storyboard/cover/export/handoff data).

Same pattern as jarvis.design_studio.db/jarvis.video_studio.db (see
either module's own docstring for the full rationale): per-call sqlite3
connections, idempotent CREATE TABLE IF NOT EXISTS schema plus an
idempotent ALTER TABLE ADD COLUMN migration step for columns added
after a table's first release, JSON-blob columns for content whose
shape may still evolve across this feature's staged rollout.

This module only stores/retrieves METADATA - actual rendered
images/video live on disk under jarvis.config.REEL_GENERATOR_PROJECTS_DIR
(see jarvis.reel_generator.storage). A project row here always
corresponds to a project directory there; storage.delete_project() and
delete_project_record() below are separate calls a caller must both
make to fully remove a project - same deliberate two-step-not-
auto-cascaded reasoning as jarvis.video_studio.db's own delete
functions.

script_approved tracks the module brief's own hard requirement
(section 4: "The video must NOT be generated before the user approves
the script.") as an explicit, persisted boolean - not inferred from
"script_data is not NULL", since a regenerated (but not yet
re-approved) script must not be mistaken for an approved one; see
save_script()/approve_script() below for the exact state transitions.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from jarvis.config import REEL_GENERATOR_DB_FILE

_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    original_idea TEXT NOT NULL,
    brief_data TEXT,
    script_data TEXT,
    script_approved INTEGER NOT NULL DEFAULT 0,
    storyboard_data TEXT,
    cover_path TEXT,
    caption_data TEXT,
    export_path TEXT,
    mode TEXT NOT NULL DEFAULT 'idea',
    video_studio_project_id TEXT,
    video_studio_filename TEXT,
    footage_plan_data TEXT,
    handoff_data TEXT,
    status TEXT NOT NULL DEFAULT 'created'
);
"""
# status is a free-text progress marker ("created", "brief_generated",
# "script_generated", "script_approved") - purely informational for a
# "Recent Reels" strip to show at a glance, same convention as
# jarvis.design_studio.db/jarvis.video_studio.db's own `status` column;
# nothing in this module enforces a state machine over it beyond the
# script_approved flag itself.
#
# brief_data/script_data hold one jarvis.reel_generator.brief.ReelBrief
# / jarvis.reel_generator.script.ReelScript per project (as JSON, via
# dataclasses.asdict()) - one "current" brief/script per project
# (re-generating overwrites it), not a history of past attempts.

_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("storyboard_data", "TEXT"),
    ("cover_path", "TEXT"),
    ("caption_data", "TEXT"),
    ("export_path", "TEXT"),
    ("mode", "TEXT NOT NULL DEFAULT 'idea'"),
    ("video_studio_project_id", "TEXT"),
    ("video_studio_filename", "TEXT"),
    ("footage_plan_data", "TEXT"),
    ("handoff_data", "TEXT"),
    ("visual_plan_data", "TEXT"),
    ("visual_style", "TEXT"),
    ("voiceover_path", "TEXT"),
    ("voiceover_text", "TEXT"),
    ("text_mode", "TEXT NOT NULL DEFAULT 'baked_in'"),
    ("caption_style_data", "TEXT"),
    ("storyboard_approved", "INTEGER NOT NULL DEFAULT 0"),
    ("original_storyboard_data", "TEXT"),
    ("original_visual_plan_data", "TEXT"),
    ("reel_approved", "INTEGER NOT NULL DEFAULT 0"),
    ("reel_approved_at", "TEXT"),
    ("publish_package_data", "TEXT"),
    ("cover_candidates_data", "TEXT"),
    ("cover_integration_mode", "TEXT NOT NULL DEFAULT 'instagram'"),
    ("reel_mode", "TEXT NOT NULL DEFAULT 'static'"),
    ("motion_settings_data", "TEXT"),
    ("motion_clips_data", "TEXT"),
)
# reel_mode/motion_settings_data/motion_clips_data (NATURAL MOTION /
# HYBRID modes): reel_mode is one of "static" (the default - every
# existing project, and every new project unless explicitly switched,
# behaves EXACTLY as before this feature existed), "natural_motion"
# (every scene gets a real AI-generated video clip attempt), or
# "hybrid" (only the scenes the person marks as "moving" do).
# motion_settings_data is one JSON blob holding the whole-Reel
# jarvis.reel_generator.motion_engine.MotionSettings (as a dict, via
# dataclasses.asdict()) plus, for Hybrid/per-scene overrides, an
# optional "per_scene" dict keyed by scene number string, and, for
# Hybrid's own scene selection, a "moving_scene_numbers" list of ints -
# kept as ONE JSON blob column rather than a normalized table, matching
# this module's own established "one JSON-blob-per-feature-column"
# convention (e.g. cover_candidates_data/publish_package_data above).
# motion_clips_data is a JSON blob holding the serialized
# list[jarvis.reel_generator.scenes.SceneMotionClip] for this project's
# last motion-generation run, so reopening a project shows which scenes
# have a real clip vs. which fell back, without re-generating anything.
# See save_reel_mode()/save_motion_settings()/save_motion_clips() below
# for the exact read/write rules - each touches only its own column(s),
# same discipline as every other save_*() function in this module.
# The first four were added in this module's Stage 2 release
# (storyboard/scenes/cover/caption/export); the next four in Stage 3
# (Mode A - create from footage, jarvis.reel_generator.footage); the
# next in Stage 4 (Instagram AI Manager hand-off,
# jarvis.reel_generator.instagram_handoff); the last two in this
# module's own "Smart Visual Director" stage
# (jarvis.reel_generator.storyboard.generate_visual_plan(),
# jarvis.reel_generator.visual_plan.VisualPlan) - all present in
# _SCHEMA's CREATE TABLE above for a brand-new database, and added via
# this idempotent ALTER TABLE path for anyone who already has an older
# reel_generator.db on disk (matching jarvis.video_studio.db's own
# established pattern for columns added after a table's first release).
# visual_plan_data holds a VisualPlan (as JSON, one ScenePlan per
# scene); visual_style holds the person's own requirement-8 whole-Reel
# style selector (one of jarvis.reel_generator.visual_plan
# .VISUAL_STYLE_CHOICES, or "" if never set - a project created before
# this stage, or one whose visual plan generation was never run, simply
# has both columns NULL/empty and renders exactly as it always has).
#
# voiceover_path/voiceover_text (Stage B): the project's own generated
# narration audio file path and the exact narration text it was
# synthesized from (jarvis.reel_generator.voiceover.generate_voiceover())
# - both NULL for a project that never generated (or whose last
# generation failed for) a voiceover; a project's export step reads
# voiceover_path (if set and the file still exists) to mux it into the
# final MP4 - see save_voiceover()/clear_voiceover() below.
#
# text_mode (Stage D: textless scene visuals) is "baked_in" (the
# default - every project created before this stage, and every new
# project unless explicitly switched) or "overlay" - a WHOLE-PROJECT
# choice (not per-scene) recorded once so reopening a project renders
# NEW scenes (GENERATE SCENE VISUALS/REGENERATE SCENE) in the SAME mode
# it was created in, without the GUI needing to re-derive it from
# scratch each time. "overlay" means jarvis.reel_generator.scene_render
# /.scenes are called with render_textless=True, and the CaptionStyle
# controls (caption_style_data - Stage C's own CaptionStyle, as JSON via
# dataclasses.asdict(), only ever meaningful for "overlay" projects) are
# shown/enabled in the GUI. Switching text_mode does NOT retroactively
# re-render already-rendered scenes - a scene rendered under the OLD
# mode keeps whatever has_baked_in_text its own SceneVisual already has
# (rediscovered from disk via _discover_scene_visuals(), which cannot
# see this column at all and defaults to has_baked_in_text=True - see
# that function's own docstring for the full reasoning) until it is
# explicitly regenerated.
#
# storyboard_approved (Storyboard Creative Controls stage) is a
# SEPARATE lock from script_approved - script_approved gates storyboard
# GENERATION (module brief section 4's original hard rule: no
# downstream work before the SCRIPT is approved); storyboard_approved
# gates everything AFTER the storyboard/visual plan exist (EDIT SCENE/
# REGENERATE SCENE/CHANGE CAMERA/CHANGE STYLE/CHANGE VISUAL/CHANGE
# DURATION/REGENERATE ALL/RESET SCENE all become read-only once this is
# set - see jarvis.gui.views.reel_generator.dashboard's own
# "APPROVE STORYBOARD" button). Setting it does NOT itself trigger
# scene-visual generation - "Do not automatically render the final Reel
# before approval" - it only UNLOCKS the GENERATE SCENE VISUALS button,
# which remains its own separate, explicit click.
#
# original_storyboard_data/original_visual_plan_data (same stage) are a
# ONE-TIME snapshot, saved the FIRST time a storyboard/visual plan is
# generated for this project and never overwritten by any later
# edit/regeneration - RESET SCENE reads a single scene back out of
# these (not the current, possibly-edited storyboard_data/
# visual_plan_data) to "restore the original generated version of that
# scene." REGENERATE ALL treats its own fresh result as the new
# baseline and DOES overwrite both snapshot columns (a full
# regeneration is a deliberate new starting point, not an edit to reset
# away from) - see save_original_storyboard_snapshot()/
# reset_scene_to_original()/save_storyboard_regenerated_all() below for
# the exact read/write rules.
#
# reel_approved (Reel Generation Workflow stage) is the REEL_APPROVED
# checkpoint in the module brief's own explicit state machine
# (STORYBOARD_APPROVED -> GENERATING_SCENES -> SCENES_READY ->
# GENERATING_REEL -> REEL_READY -> REEL_APPROVED -> "ready for
# publishing"), a SEPARATE lock from storyboard_approved above - that
# one gates storyboard editing/scene-visual generation, this one gates
# the "Send to Instagram Manager" hand-off (jarvis.reel_generator
# .instagram_handoff), which remains a draft-only hand-off either way
# (module brief's own hard rule: "Do NOT automatically publish to
# Instagram" - publishing itself is a person's own separate, manual
# action inside Instagram AI Manager, unaffected by this flag). Setting
# it does NOT itself trigger any hand-off or export - see
# approve_reel()/unlock_reel_approval() below.
#
# publish_package_data (Content Package + Ready to Publish stage) is
# ONE new JSON-blob column (matching this table's own established "one
# new JSON column per staged feature" convention - see visual_plan_data/
# footage_plan_data/handoff_data above) holding the Content Package's
# own review/edit/approval overlay - never duplicating cover_path/
# caption_data/export_path/voiceover_path, which remain this table's own
# single source of truth for those assets; the package instead
# references them and adds what's genuinely new: caption/hashtag TEXT
# the person may have hand-edited (plus a generated-vs-edited flag for
# each, so a later regenerate never silently overwrites an edit - module
# brief section 6), which cover source is currently selected (the
# generated cover, or a specific Reel scene frame - module brief section
# 1B), a suggested posting date/time + timezone (a recommendation only,
# same real jarvis.reel_generator.instagram_handoff._suggested_posting_
# time() this project's own Instagram hand-off already computes - never
# re-implemented here), the package's own status ("not_started"/
# "generating"/"ready"), and the SEPARATE publish_approved/
# publish_approved_at checkpoint (module brief section 4: "APPROVE FOR
# PUBLISHING... must NOT call Instagram APIs yet" - a boundary marker
# for the future Instagram-publishing stage to read, not itself a
# trigger for anything). See jarvis.reel_generator.publish_package for
# the dataclass this column's JSON deserializes into and the functions
# that read/write it - this module only persists the blob, matching the
# same read/write split visual_plan_data's own module
# (jarvis.reel_generator.visual_plan) already uses.
#
# mode is "idea" (Stage 1/2's from-scratch pipeline: brief/script/
# storyboard/scenes) or "footage" (Stage 3's Mode A: the Reel IS a
# linked jarvis.video_studio project's own transcript/highlights/edit
# plan/export - see jarvis.reel_generator.footage's own docstring for
# why that data lives in VIDEO_STUDIO_DB_FILE, not duplicated here).
# video_studio_project_id/video_studio_filename are set only for
# mode="footage" projects - the pair jarvis.video_studio.storage
# .project_paths(project_id, original_filename) needs to reconstruct
# that project's own file paths. footage_plan_data caches the last
# jarvis.video_studio.reel.ReelEditPlan this project's own Reel
# Generator UI showed (as JSON, via dataclasses.asdict()) purely so
# reopening a Mode-A Reel from Recent Reels doesn't need to re-run
# highlight detection - jarvis.video_studio.db.get_project() already
# stores the SAME plan under its own reel_plan_data column too; this is
# a read-through cache for this module's own UI, not the source of
# truth (that remains VIDEO_STUDIO_DB_FILE's own row).


def _migrate(conn: sqlite3.Connection) -> None:
    if not _ADDED_COLUMNS:
        return
    existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    for column, column_type in _ADDED_COLUMNS:
        if column not in existing:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {column_type}")  # noqa: S608 - column/type are fixed module constants, never user input


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    REEL_GENERATOR_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(REEL_GENERATOR_DB_FILE))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass(frozen=True)
class ProjectRecord:
    id: str
    created_at: str
    original_idea: str
    brief_data: dict[str, Any] | None
    script_data: dict[str, Any] | None
    script_approved: bool
    storyboard_data: dict[str, Any] | None
    cover_path: str | None
    caption_data: dict[str, Any] | None
    export_path: str | None
    mode: str
    video_studio_project_id: str | None
    video_studio_filename: str | None
    footage_plan_data: dict[str, Any] | None
    handoff_data: dict[str, Any] | None
    visual_plan_data: dict[str, Any] | None
    visual_style: str | None
    voiceover_path: str | None
    voiceover_text: str | None
    text_mode: str
    caption_style_data: dict[str, Any] | None
    storyboard_approved: bool
    original_storyboard_data: dict[str, Any] | None
    original_visual_plan_data: dict[str, Any] | None
    reel_approved: bool
    reel_approved_at: str | None
    publish_package_data: dict[str, Any] | None
    status: str
    cover_candidates_data: list[dict[str, Any]] | None
    cover_integration_mode: str
    reel_mode: str = "static"
    motion_settings_data: dict[str, Any] | None = None
    motion_clips_data: list[dict[str, Any]] | None = None


def create_project_record(project_id: str, original_idea: str) -> None:
    """Inserts a new project row right after
    jarvis.reel_generator.storage.create_project() has created the
    directory structure - called separately, matching
    jarvis.design_studio.db's own storage/db split. mode defaults to
    "idea" (Stage 1/2's from-scratch pipeline) - see
    create_footage_project_record() for Mode A."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, created_at, original_idea, brief_data, script_data, "
            "script_approved, mode, status) VALUES (?, ?, ?, NULL, NULL, 0, 'idea', 'created')",
            (project_id, _now_iso(), original_idea),
        )


def save_brief(project_id: str, brief_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.reel_generator.brief.ReelBrief (as a
    plain dict via dataclasses.asdict()) and advances status to
    'brief_generated'. Overwrites any previous brief for this project."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET brief_data = ?, status = 'brief_generated' WHERE id = ?",
            (json.dumps(brief_data, ensure_ascii=False), project_id),
        )


def save_script(project_id: str, script_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.reel_generator.script.ReelScript,
    advances status to 'script_generated', and RESETS script_approved
    back to False - a freshly (re)generated script has not been
    approved yet, even if a previous version of the script for this
    same project once was (module brief section 4's approval gate
    applies to the CURRENT script, not to "this project has ever had
    an approved script")."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET script_data = ?, script_approved = 0, status = 'script_generated' WHERE id = ?",
            (json.dumps(script_data, ensure_ascii=False), project_id),
        )


def approve_script(project_id: str) -> None:
    """Marks the project's CURRENT script_data as approved (module
    brief section 4: "[✅ APPROVE]") and advances status to
    'script_approved'. A later save_script() call (regenerating the
    script) resets this back to False - see save_script()'s own
    docstring."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET script_approved = 1, status = 'script_approved' WHERE id = ?",
            (project_id,),
        )


def save_storyboard(project_id: str, storyboard_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.reel_generator.storyboard.Storyboard
    (as a plain dict via dataclasses.asdict()) and advances status to
    'storyboard_generated'. Overwrites any previous storyboard for this
    project - only ever called after script_approved is already True
    (module brief section 4's gate: no downstream generation before
    approval), enforced by the GUI caller, not by this function itself.

    Does NOT touch original_storyboard_data - that one-time snapshot is
    written separately by save_original_storyboard_snapshot() (Storyboard
    Creative Controls stage), called once right after the FIRST
    successful generation; every subsequent call here (a scene edit, a
    single-scene regeneration, a duration change) intentionally leaves
    the original snapshot alone so RESET SCENE always has the true
    original to restore from."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET storyboard_data = ?, status = 'storyboard_generated' WHERE id = ?",
            (json.dumps(storyboard_data, ensure_ascii=False), project_id),
        )


def save_original_storyboard_snapshot(project_id: str, storyboard_data: dict[str, Any] | None) -> None:
    """Records the ONE-TIME "as originally generated" storyboard
    snapshot RESET SCENE restores individual scenes from - called once,
    right after the first successful jarvis.reel_generator.storyboard
    .generate_storyboard() call for this project (see db's own
    docstring for the full original_storyboard_data/
    original_visual_plan_data reasoning). Overwrites any PREVIOUS
    snapshot - only REGENERATE ALL calls this a second time (a full
    regeneration deliberately becomes the new baseline); a plain scene
    edit/single-scene regeneration must never call this."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET original_storyboard_data = ? WHERE id = ?",
            (json.dumps(storyboard_data, ensure_ascii=False) if storyboard_data is not None else None, project_id),
        )


def save_original_visual_plan_snapshot(project_id: str, visual_plan_data: dict[str, Any] | None) -> None:
    """The VisualPlan counterpart to save_original_storyboard_snapshot()
    above - same one-time-snapshot/REGENERATE-ALL-only-overwrite rule."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET original_visual_plan_data = ? WHERE id = ?",
            (json.dumps(visual_plan_data, ensure_ascii=False) if visual_plan_data is not None else None, project_id),
        )


def approve_storyboard(project_id: str) -> None:
    """Locks the project's storyboard (Storyboard Creative Controls
    stage's own "APPROVE STORYBOARD" button) - a SEPARATE lock from
    approve_script() above (see this module's own docstring for the
    full storyboard_approved reasoning). Does not itself trigger scene-
    visual generation or any rendering - "Do not automatically render
    the final Reel before approval" - it only unlocks the GUI's own
    GENERATE SCENE VISUALS button, a separate explicit click."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET storyboard_approved = 1 WHERE id = ?", (project_id,))


def unlock_storyboard(project_id: str) -> None:
    """Clears storyboard_approved - called when REGENERATE ALL produces
    a genuinely new storyboard/visual plan, so a person must explicitly
    re-approve the NEW plan before generating scene visuals again
    (an already-approved OLD plan being silently treated as approval
    for a completely different NEW one would be a real, surprising
    correctness bug, not a convenience)."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET storyboard_approved = 0 WHERE id = ?", (project_id,))


def approve_reel(project_id: str) -> None:
    """Locks in the REEL_APPROVED checkpoint (Reel Generation Workflow
    stage's own "APPROVE REEL" button) - a SEPARATE lock from
    approve_storyboard() above. Does not itself send anything to
    Instagram AI Manager or publish anything anywhere - it only unlocks
    the GUI's own "Send to Instagram Manager" button, a separate
    explicit click, which itself only ever creates DRAFT content there
    (see jarvis.reel_generator.instagram_handoff's own docstring) -
    publishing remains a person's own later, manual, separate action.

    Also records reel_approved_at (Reel Preview + Approve Reel stage) -
    the real wall-clock moment approval happened, since "approved" alone
    doesn't say WHEN, and a person reviewing a project later may want to
    know how long ago it was approved. The caller (the GUI) is
    responsible for validating the Reel is genuinely in a REEL_READY
    state (a real export exists, no scenes missing/failed, not
    currently generating) BEFORE calling this - this function itself
    only persists the flag, matching approve_storyboard()'s own "the
    GUI enforces preconditions, this function just writes" split."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET reel_approved = 1, reel_approved_at = ? WHERE id = ?",
            (_now_iso(), project_id),
        )


def unlock_reel_approval(project_id: str) -> None:
    """Clears reel_approved (and its timestamp) - called whenever the
    final Reel export is regenerated (a new export makes any PREVIOUS
    reel_approved checkpoint stale: the newly-exported Reel has not
    itself been reviewed/approved yet, even if an earlier export once
    was) - same "a fresh result must be re-approved, never silently
    inherit an old approval" reasoning as unlock_storyboard() above."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET reel_approved = 0, reel_approved_at = NULL WHERE id = ?", (project_id,),
        )


def save_publish_package(project_id: str, publish_package_data: dict[str, Any] | None) -> None:
    """Stores a project's jarvis.reel_generator.publish_package
    .PublishPackage (as a plain dict via dataclasses.asdict()) - see
    this module's own docstring for the full publish_package_data
    reasoning. Overwrites any previous package for this project - the
    package itself is the single current source of truth for its own
    caption/hashtag edits, cover selection, posting time, and approval
    state; jarvis.reel_generator.publish_package's own functions decide
    WHEN a field should or shouldn't be overwritten (e.g. never
    clobbering a user-edited caption on a plain re-render), this
    function simply persists whatever it's given. `None` clears the
    package entirely (used only if a project's Reel export is ever
    regenerated in a way that invalidates the whole package - see that
    module's own docstring for exactly when)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET publish_package_data = ? WHERE id = ?",
            (json.dumps(publish_package_data, ensure_ascii=False) if publish_package_data is not None else None, project_id),
        )


def save_visual_plan(project_id: str, visual_plan_data: dict[str, Any], *, visual_style: str = "") -> None:
    """Stores a project's jarvis.reel_generator.visual_plan.VisualPlan
    (one ScenePlan per scene, as a plain dict via dataclasses.asdict())
    and the whole-Reel `visual_style` selector (requirement 8) -
    generated by jarvis.reel_generator.storyboard.generate_visual_plan(),
    a SEPARATE call from save_storyboard() above (see that function's
    own docstring for why they're independent). Overwrites any previous
    visual plan for this project. Does NOT touch storyboard_data,
    status, or any other column - a project's storyboard remains valid
    and renderable even if its visual plan generation is retried/
    changes."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET visual_plan_data = ?, visual_style = ? WHERE id = ?",
            (json.dumps(visual_plan_data, ensure_ascii=False), visual_style, project_id),
        )


def save_cover_path(project_id: str, cover_path: str) -> None:
    """Records the path to the project's generated Reel cover image.
    Overwrites any previous cover (re-rendering is idempotent, not
    additive)."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET cover_path = ? WHERE id = ?", (cover_path, project_id))


def save_cover_candidates(project_id: str, candidates: list[dict[str, Any]]) -> None:
    """Records the full set of cover variants offered by GENERATE 3
    COVERS (jarvis.reel_generator.cover.CoverCandidate, each as a plain
    dict via dataclasses.asdict()) - a SEPARATE column from cover_path,
    which stays the single "currently selected/saved" cover path every
    other part of this module (export, publish hand-off) already reads
    unchanged. cover_candidates_data exists only for the PICKER UI
    itself to re-render its own preview grid/EDIT dialog across a
    dashboard redraw without re-generating anything - selecting one
    candidate still writes its own image_path into cover_path via the
    existing save_cover_path() above, exactly as a single-cover
    generation always has. Overwrites any previous candidate set for
    this project (a fresh GENERATE 3 COVERS/REGENERATE replaces the
    whole batch, not additive)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET cover_candidates_data = ? WHERE id = ?",
            (json.dumps(candidates, ensure_ascii=False), project_id),
        )


# Real, reported bug fix ("the selected cover never appears in the
# exported video"): jarvis.reel_generator.export.export_reel_video()
# never received cover_path at all, so a person's SELECT choice in
# CHOOSE A REEL COVER only ever affected the separate Content Package/
# Instagram-profile cover image, never the actual MP4. These three
# modes let a person choose whether/how the selected cover reaches the
# video itself:
#   - "instagram": unchanged, pre-existing behavior - the cover stays a
#     separate image for Instagram's own profile-grid thumbnail, never
#     baked into the video. This is the DEFAULT (every project created
#     before this fix keeps behaving exactly as it always did).
#   - "intro": the cover image becomes a real, additional first segment
#     of the exported video itself (module brief's own "1-2 seconds"),
#     shown briefly before the first real scene - the separate Instagram
#     cover is NOT saved/shown in this mode.
#   - "both": the cover is BOTH kept as the separate Instagram cover AND
#     inserted as the video's own intro segment.
COVER_INTEGRATION_MODE_INSTAGRAM = "instagram"
COVER_INTEGRATION_MODE_INTRO = "intro"
COVER_INTEGRATION_MODE_BOTH = "both"
COVER_INTEGRATION_MODE_CHOICES = (
    COVER_INTEGRATION_MODE_INSTAGRAM, COVER_INTEGRATION_MODE_INTRO, COVER_INTEGRATION_MODE_BOTH,
)


def save_cover_integration_mode(project_id: str, mode: str) -> None:
    """Records how the project's selected cover (cover_path) should
    reach the final exported Reel - see COVER_INTEGRATION_MODE_CHOICES'
    own docstring for what each value means. Defaults to
    COVER_INTEGRATION_MODE_INSTAGRAM for every project (both a brand-new
    one, via _SCHEMA's own column default, and an older one that
    predates this column, via the idempotent ALTER TABLE migration's own
    DEFAULT clause) - the pre-existing, unchanged behavior."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET cover_integration_mode = ? WHERE id = ?",
            (mode, project_id),
        )


REEL_MODE_STATIC = "static"
REEL_MODE_NATURAL_MOTION = "natural_motion"
REEL_MODE_HYBRID = "hybrid"
REEL_MODE_CHOICES = (REEL_MODE_STATIC, REEL_MODE_NATURAL_MOTION, REEL_MODE_HYBRID)
# REEL_MODE_STATIC (the default for every project, both new and
# pre-existing, via _SCHEMA's/the ALTER TABLE migration's own DEFAULT
# clause - the ONLY mode that existed before this feature): every scene
# is a still image, exactly as before - REEL_MODE_NATURAL_MOTION/
# REEL_MODE_HYBRID never affect a Static project's behavior in any way.
# REEL_MODE_NATURAL_MOTION: every scene additionally gets a real
# AI-generated video clip attempt (jarvis.reel_generator.motion_engine),
# falling back to its own still image (plus this codebase's
# pre-existing optional Ken Burns pan/zoom) for any scene whose
# generation wasn't configured/failed. REEL_MODE_HYBRID: only the scene
# numbers listed in motion_settings_data's own "moving_scene_numbers"
# get a clip attempt; every other scene stays static, exactly like a
# Static-mode scene.


def save_reel_mode(project_id: str, mode: str) -> None:
    """Records which of the three Reel modes (REEL_MODE_CHOICES) this
    project uses - read fresh by the GUI on every export click (same
    "re-read fresh from DB, never cache" convention as
    save_cover_integration_mode()'s own docstring). Raises ValueError
    for a mode outside REEL_MODE_CHOICES - a real, caller-side bug (an
    unrecognized value could otherwise silently be written and later
    misread as REEL_MODE_STATIC via `row[30] or REEL_MODE_STATIC`'s own
    falsy-string-never-happens assumption), never silently accepted."""
    if mode not in REEL_MODE_CHOICES:
        raise ValueError(f"Unknown reel_mode: {mode!r} (expected one of {REEL_MODE_CHOICES})")
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET reel_mode = ? WHERE id = ?",
            (mode, project_id),
        )


def save_motion_settings(project_id: str, motion_settings_data: dict[str, Any]) -> None:
    """Stores this project's NATURAL MOTION / HYBRID settings (as a
    plain dict - see motion_settings_data's own docstring above for its
    shape: a whole-Reel jarvis.reel_generator.motion_engine.MotionSettings
    dict, an optional per-scene override dict, and Hybrid's own
    moving_scene_numbers list) as JSON. Touches ONLY this one column -
    never reel_mode/motion_clips_data, same discipline as every other
    save_*() function in this module."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET motion_settings_data = ? WHERE id = ?",
            (json.dumps(motion_settings_data, ensure_ascii=False), project_id),
        )


def save_motion_clips(project_id: str, motion_clips_data: list[dict[str, Any]]) -> None:
    """Stores this project's last motion-generation run's own
    list[jarvis.reel_generator.scenes.SceneMotionClip] (as a list of
    plain dicts - the GUI/motion_engine call site is responsible for
    serializing each SceneMotionClip, including converting its own Path
    fields to plain strings, before calling this) as JSON. Touches ONLY
    this one column."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET motion_clips_data = ? WHERE id = ?",
            (json.dumps(motion_clips_data, ensure_ascii=False), project_id),
        )


def save_caption(project_id: str, caption_data: dict[str, Any]) -> None:
    """Stores a project's jarvis.reel_generator.caption
    .ReelCaptionPackage (as a plain dict via dataclasses.asdict()).
    Overwrites any previous caption package for this project."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET caption_data = ? WHERE id = ?",
            (json.dumps(caption_data, ensure_ascii=False), project_id),
        )


def save_export_path(project_id: str, export_path: str) -> None:
    """Records the path to the project's most recent final MP4 export
    and advances status to 'exported'. A project can have only ONE
    "current" export path recorded here (unlike
    jarvis.video_studio.db's separate `exports` table, which keeps a
    full history) - Stage 2's export step always writes a NEW,
    timestamped file under exports_dir (never overwriting a previous
    export, module brief section 14), so old export files remain on
    disk even though only the latest one's path is tracked here; a
    fuller export history table can be added later if genuinely
    needed, matching jarvis.design_studio.db's own "start simple, grow
    the schema only when a real need appears" precedent.

    Also clears reel_approved AND reel_approved_at (Reel Generation
    Workflow / Reel Preview stages) - a fresh export is a genuinely new
    Reel render that has not itself been reviewed/approved yet, even if
    a PREVIOUS export for this project once was (same "a fresh result
    must be re-approved, never silently inherit an old approval"
    reasoning as unlock_storyboard()) - and a stale timestamp from a
    previous, now-superseded approval must never linger and be mistaken
    for describing the CURRENT export."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET export_path = ?, status = 'exported', "
            "reel_approved = 0, reel_approved_at = NULL WHERE id = ?",
            (export_path, project_id),
        )


def save_voiceover(project_id: str, voiceover_path: str, voiceover_text: str) -> None:
    """Records the project's generated voiceover audio file path and the
    exact narration text it was synthesized from (Stage B:
    jarvis.reel_generator.voiceover.generate_voiceover()). Overwrites any
    previous voiceover for this project (regeneration is idempotent, not
    additive - same convention as save_cover_path())."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET voiceover_path = ?, voiceover_text = ? WHERE id = ?",
            (voiceover_path, voiceover_text, project_id),
        )


def clear_voiceover(project_id: str) -> None:
    """Clears a project's recorded voiceover (path and text) without
    touching anything else - used when a voiceover generation attempt
    fails after a previous one had succeeded, so a stale (no-longer-
    matching) voiceover path is never left pointing at content that no
    longer reflects the project's current storyboard."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET voiceover_path = NULL, voiceover_text = NULL WHERE id = ?",
            (project_id,),
        )


def save_text_mode(project_id: str, text_mode: str) -> None:
    """Records this project's WHOLE-PROJECT text_mode ("baked_in" or
    "overlay" - see this module's own docstring for the full
    reasoning). Does not touch storyboard_data, visuals, or any other
    column - switching modes never retroactively re-renders already-
    rendered scenes."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET text_mode = ? WHERE id = ?", (text_mode, project_id))


def save_caption_style(project_id: str, caption_style_data: dict[str, Any]) -> None:
    """Stores this project's jarvis.reel_generator.export.CaptionStyle
    (as a plain dict via dataclasses.asdict()) - only ever meaningful
    for a project whose text_mode is "overlay" (see this module's own
    docstring); saved regardless of text_mode so switching TO "overlay"
    later doesn't lose a style already chosen. Overwrites any previous
    style for this project."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET caption_style_data = ? WHERE id = ?",
            (json.dumps(caption_style_data, ensure_ascii=False), project_id),
        )


def set_status(project_id: str, status: str) -> None:
    """Updates only the status marker - used by later stages to advance
    a project's at-a-glance state without touching brief_data/
    script_data."""
    with _connect() as conn:
        conn.execute("UPDATE projects SET status = ? WHERE id = ?", (status, project_id))


def create_footage_project_record(
    project_id: str, original_idea: str, *, video_studio_project_id: str, video_studio_filename: str,
) -> None:
    """Inserts a new project row for a Mode-A ("create from my
    footage") project - mode="footage", linked to an already-created
    jarvis.video_studio project via video_studio_project_id/
    video_studio_filename (see jarvis.reel_generator.footage's own
    docstring for why that linked project, not this module's own
    columns, is the actual source of truth for the transcript/
    highlights/edit plan). original_idea here is a short caller-
    supplied label (e.g. the uploaded file's own name) rather than a
    typed idea, purely for the "Recent Reels" strip - Mode A has no
    plain-language prompt step."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO projects (id, created_at, original_idea, script_approved, mode, "
            "video_studio_project_id, video_studio_filename, status) "
            "VALUES (?, ?, ?, 1, 'footage', ?, ?, 'created')",
            (project_id, _now_iso(), original_idea, video_studio_project_id, video_studio_filename),
        )


def save_footage_plan(project_id: str, plan_data: dict[str, Any]) -> None:
    """Caches the linked jarvis.video_studio project's current
    jarvis.video_studio.reel.ReelEditPlan (as JSON) on this project's
    own row and advances status to 'plan_generated' - a read-through
    cache for this module's own "Recent Reels" reopen flow; the real
    source of truth remains jarvis.video_studio.db's own
    reel_plan_data column on the LINKED project (see this module's own
    docstring)."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET footage_plan_data = ?, status = 'plan_generated' WHERE id = ?",
            (json.dumps(plan_data, ensure_ascii=False), project_id),
        )


def save_handoff(project_id: str, handoff_data: dict[str, Any]) -> None:
    """Records that this project's content was sent to Instagram AI
    Manager (see jarvis.reel_generator.instagram_handoff) -
    `handoff_data` holds what was sent and the resulting
    jarvis.instagram_ai_manager.db row ids, so a UI can show "already
    sent to Instagram Manager" rather than risk a person clicking the
    hand-off button twice and creating duplicate draft rows there.
    Overwrites any previous hand-off record (a second hand-off is a
    deliberate re-send, not accumulated) - same convention as
    jarvis.video_studio.db.save_handoff()."""
    with _connect() as conn:
        conn.execute(
            "UPDATE projects SET handoff_data = ? WHERE id = ?",
            (json.dumps(handoff_data, ensure_ascii=False), project_id),
        )


_SELECT_COLUMNS = (
    "id, created_at, original_idea, brief_data, script_data, script_approved, "
    "storyboard_data, cover_path, caption_data, export_path, mode, "
    "video_studio_project_id, video_studio_filename, footage_plan_data, handoff_data, "
    "visual_plan_data, visual_style, voiceover_path, voiceover_text, "
    "text_mode, caption_style_data, storyboard_approved, "
    "original_storyboard_data, original_visual_plan_data, reel_approved, reel_approved_at, "
    "publish_package_data, status, cover_candidates_data, cover_integration_mode, "
    "reel_mode, motion_settings_data, motion_clips_data"
)


def _row_to_record(row: tuple) -> ProjectRecord:
    return ProjectRecord(
        id=row[0], created_at=row[1], original_idea=row[2],
        brief_data=json.loads(row[3]) if row[3] else None,
        script_data=json.loads(row[4]) if row[4] else None,
        script_approved=bool(row[5]),
        storyboard_data=json.loads(row[6]) if row[6] else None,
        cover_path=row[7],
        caption_data=json.loads(row[8]) if row[8] else None,
        export_path=row[9], mode=row[10],
        video_studio_project_id=row[11], video_studio_filename=row[12],
        footage_plan_data=json.loads(row[13]) if row[13] else None,
        handoff_data=json.loads(row[14]) if row[14] else None,
        visual_plan_data=json.loads(row[15]) if row[15] else None,
        visual_style=row[16],
        voiceover_path=row[17], voiceover_text=row[18],
        text_mode=row[19] or "baked_in",
        caption_style_data=json.loads(row[20]) if row[20] else None,
        storyboard_approved=bool(row[21]),
        original_storyboard_data=json.loads(row[22]) if row[22] else None,
        original_visual_plan_data=json.loads(row[23]) if row[23] else None,
        reel_approved=bool(row[24]),
        reel_approved_at=row[25],
        publish_package_data=json.loads(row[26]) if row[26] else None,
        status=row[27],
        cover_candidates_data=json.loads(row[28]) if row[28] else None,
        cover_integration_mode=row[29] or COVER_INTEGRATION_MODE_INSTAGRAM,
        reel_mode=row[30] or REEL_MODE_STATIC,
        motion_settings_data=json.loads(row[31]) if row[31] else None,
        motion_clips_data=json.loads(row[32]) if row[32] else None,
    )


def get_project(project_id: str) -> ProjectRecord | None:
    with _connect() as conn:
        row = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects WHERE id = ?",  # noqa: S608 - _SELECT_COLUMNS is a fixed module constant, never user input
            (project_id,),
        ).fetchone()
    return None if row is None else _row_to_record(row)


def list_projects(limit: int = 50) -> list[ProjectRecord]:
    """Newest-first, for a "Recent Reels" strip. Ties broken by
    sqlite's own implicit rowid, same reasoning
    jarvis.design_studio.db.list_projects() documents for its own
    identical tie-break."""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT_COLUMNS} FROM projects ORDER BY created_at DESC, rowid DESC LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
    return [_row_to_record(r) for r in rows]


def delete_project_record(project_id: str) -> None:
    """Deletes only this table's row - does NOT touch the project's
    on-disk directory (see jarvis.reel_generator.storage.delete_project()
    for that)."""
    with _connect() as conn:
        conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
