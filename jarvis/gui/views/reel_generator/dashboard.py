"""AI Reel Generator dashboard (module brief, section 1): the top-level
view for the sidebar's "🎞️ AI Reel Generator" nav item.

Stage 4 of this feature's staged rollout adds, on top of Stage 1-3
(Mode B "✨ CREATE FROM MY IDEA" and Mode A "🎥 CREATE FROM MY FOOTAGE"):
Instagram AI Manager hand-off (jarvis.reel_generator.instagram_handoff,
module brief section 16), a Content Package
(jarvis.reel_generator.content_package, section 15 - one click creates
matching Story promotion/Post/Carousel/Story CTA graphics alongside the
Reel's own cover, all sharing the same headline/CTA/style), and
pre-export Quality Control (jarvis.reel_generator.quality_control,
section 20 - shown once an export exists, never blocking Export itself
since every check is informational/advisory, not a hard gate the
person can't override).

The module brief's hard gate for Mode B (section 4: "The video must
NOT be generated before the user approves the script.") is enforced
structurally, not just by a UI hint: storyboard generation (and
everything after it) is only ever reachable from the Approve button's
own callback, which only appears once a script exists - there is no
path in this view that can reach _on_generate_storyboard_clicked()
without a script having been approved first. Mode A has no equivalent
script-approval step (module brief section 19: for footage, "JARVIS
edits it" - there is no from-scratch script to approve), so its own
edit PLAN is shown for review before Export is offered instead - same
"never render/export before the person has seen and can act on a
plan" spirit, applied to the thing Mode A actually generates.
Instagram hand-off never auto-publishes anything (module brief section
16: "Do NOT automatically publish. Require user approval before
publishing.") - it only makes content available in Instagram AI
Manager's own Content Studio, where that module's own separate,
unrelated publish flow (outside this view's scope) requires its own
explicit person action.

Every LLM call and every Pillow/ffmpeg-blocking call (brief, script,
storyboard, scene visuals, cover, caption, export, Mode A's own
transcribe/analyze/highlights/plan/export, Instagram hand-off, and
Content Package rendering) runs through jarvis.gui.worker
.run_generation_in_background() on this view's own polled result queue,
exactly like every other feature module's generation panels in this
codebase (jarvis.gui.views.design_studio.dashboard - see its own
docstring for why a dedicated queue per feature area)."""

from __future__ import annotations

import dataclasses
import logging
import queue
import uuid
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.reel_generator import caption as caption_mod
from jarvis.reel_generator import content_package as content_package_mod
from jarvis.reel_generator import cover as cover_mod
from jarvis.reel_generator import db, footage, instagram_handoff, storage
from jarvis.reel_generator.brief import (
    DEFAULT_DURATION_SECONDS,
    DURATION_CHOICES,
    LANGUAGE_ENGLISH,
    LANGUAGE_LITHUANIAN,
    STYLE_CHOICES,
    ReelBrief,
    generate_reel_brief,
)
from jarvis.reel_generator import export as export_mod
from jarvis.reel_generator.export import ExportError, export_reel_video
from jarvis.reel_generator.quality_control import QualityReport, run_quality_control
from jarvis.reel_generator.scene_render import render_all_story_scenes, render_story_scene_visual
from jarvis.reel_generator.scenes import SceneMotionClip, SceneVisual, render_all_scenes
from jarvis.reel_generator import motion_engine
from jarvis.reel_generator.motion_engine import MOTION_INTENSITY_CHOICES, MOTION_STYLE_CHOICES, MotionSettings
from jarvis.reel_generator.script import ReelScript, generate_reel_script
from jarvis.reel_generator.storage import ReelProject, StorageError
from jarvis.reel_generator import project_status
from jarvis.reel_generator import publish_package as publish_package_mod
from jarvis.reel_generator import visual_quality
from jarvis.reel_generator import voiceover as voiceover_mod
from jarvis.reel_generator.storyboard import (
    Scene,
    Storyboard,
    generate_storyboard,
    generate_visual_plan,
    regenerate_scene_camera,
    regenerate_scene_style,
    regenerate_scene_visual as regenerate_scene_visual_plan,
)
from jarvis.reel_generator.visual_plan import DEFAULT_VISUAL_STYLE, VISUAL_STYLE_CHOICES, ScenePlan, VisualPlan
from jarvis.design_studio.brand_kit import apply_brand_kit, get_brand_kit
from jarvis.gui import theme
from jarvis.gui.views.reel_generator.common import LabeledDropdown, ReelProjectCard, format_file_size, status_label
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.video_studio import reel as video_reel
from jarvis.video_studio.export import EXPORT_FORMATS
from jarvis.video_studio.reel import PACING_OPTIONS, STYLES as FOOTAGE_STYLES, TARGET_DURATIONS as FOOTAGE_DURATIONS
from jarvis.video_studio.storage import SUPPORTED_EXTENSIONS as VIDEO_SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)
# Standard library logging, added specifically so an exception inside a
# CTkButton's own `command=` callback is never silently invisible - see
# _on_generate_visuals_clicked()'s own docstring for the exact bug this
# was added to catch (customtkinter's CTkButton calls `command()`
# directly with no try/except of its own, so an unhandled exception
# there only ever reaches Tkinter's default report_callback_exception -
# a bare traceback to stderr, invisible when launched via
# scripts/launch_jarvis.bat with no attached console, or a packaged
# build). No handler is configured here - jarvis.cli.main/jarvis.gui.app
# own the process-wide logging setup (or its absence); this module only
# ever calls logger.exception()/.error(), which reaches the root
# logger's own handlers (or Python's own "no handlers configured"
# lastResort stderr handler if the app has configured none - still
# visible in a terminal launch, unlike an uncaught exception from deep
# inside Tkinter's own C-level event dispatch).

_FOOTAGE_FILETYPES = (("Video files", "*.mp4 *.mov *.m4v *.webm"), ("All files", "*.*"))

_QUEUE_POLL_INTERVAL_MS = 100
_RECENT_REELS_LIMIT = 12

_DURATION_DROPDOWN_VALUES = tuple(f"{d} sec" for d in DURATION_CHOICES)
_DURATION_LABEL_TO_VALUE = {f"{d} sec": d for d in DURATION_CHOICES}
_DEFAULT_DURATION_LABEL = f"{DEFAULT_DURATION_SECONDS} sec"

_STYLE_LABELS = {"automatic": "Automatic"} | {name: name.replace("_", " ").title() for name in STYLE_CHOICES}
_STYLE_DROPDOWN_VALUES = ("Automatic",) + tuple(_STYLE_LABELS[s] for s in STYLE_CHOICES)
_STYLE_LABEL_TO_KEY = {v: k for k, v in _STYLE_LABELS.items()}

_LANGUAGE_DROPDOWN_VALUES = ("English", "Lithuanian")
_LANGUAGE_LABEL_TO_KEY = {"English": LANGUAGE_ENGLISH, "Lithuanian": LANGUAGE_LITHUANIAN}

# Requirement 8's "Visual Style" selector - a WHOLE-REEL style choice,
# separate from the plain STYLE_CHOICES dropdown above (that one feeds
# jarvis.reel_generator.brief's own style field; this one feeds
# jarvis.reel_generator.storyboard.generate_visual_plan()'s own
# visual_style parameter - see that function's own docstring).
_VISUAL_STYLE_LABELS = {name: name.replace("_", " ").title() for name in VISUAL_STYLE_CHOICES}
_VISUAL_STYLE_DROPDOWN_VALUES = tuple(_VISUAL_STYLE_LABELS[s] for s in VISUAL_STYLE_CHOICES)
_VISUAL_STYLE_LABEL_TO_KEY = {v: k for k, v in _VISUAL_STYLE_LABELS.items()}
_DEFAULT_VISUAL_STYLE_LABEL = _VISUAL_STYLE_LABELS[DEFAULT_VISUAL_STYLE]

_FOOTAGE_DURATION_DROPDOWN_VALUES = tuple(f"{d} sec" for d in FOOTAGE_DURATIONS)
_FOOTAGE_DURATION_LABEL_TO_VALUE = {f"{d} sec": d for d in FOOTAGE_DURATIONS}

# EDIT COVER's own font dropdown (module brief's own "let me edit the
# font" requirement) - these are the ONLY two font files
# jarvis.design_studio.styles (and every DesignStyle preset it defines)
# ever uses anywhere in this codebase (see that module's own docstring
# for why: no other .ttf is bundled with or referenced by this
# project) - not a new/duplicated font list, the exact same two paths
# every existing style already resolves to, spelled out here only
# because styles.py's own _FONT_BOLD/_FONT_REGULAR are module-private.
_COVER_EDIT_FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
_COVER_EDIT_FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"


class ReelGeneratorView(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None, navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._navigate = navigate
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._current_project: ReelProject | None = None
        self._current_brief: ReelBrief | None = None
        self._current_script: ReelScript | None = None
        self._current_storyboard: Storyboard | None = None
        self._current_visual_plan: VisualPlan | None = None
        self._current_visuals: list[SceneVisual] = []
        # NATURAL MOTION / HYBRID modes: this project's own last motion-
        # generation run's real clips - purely in-memory, re-derived from
        # jarvis.reel_generator.db's own persisted motion_clips_data on
        # reopen (see _open_reel() below), same "don't duplicate a
        # separate source of truth" convention as _current_cover_
        # candidates above. `_is_generating_motion` mirrors
        # `_is_generating`'s own role but for the SEPARATE motion-
        # generation background step (see _do_generate_visuals()'s own
        # extension for why this is a distinct flag from _is_generating -
        # motion generation always runs AFTER still-image generation
        # completes, as its own separate background step).
        self._current_motion_clips: list[SceneMotionClip] = []
        self._is_generating_motion: bool = False
        self._current_footage_plan: video_reel.ReelEditPlan | None = None
        self._current_footage_video_project_id: str | None = None
        self._current_footage_filename: str | None = None
        self._current_caption_package: caption_mod.ReelCaptionPackage | None = None
        self._current_voiceover_path: Path | None = None
        self._current_voiceover_text: str = ""
        # "Regenerate Cover looks the same" fix's own per-project state:
        # how many cover generations have happened so far for the
        # CURRENTLY loaded project (0 = none yet) and every title tried
        # so far - passed to cover_mod.render_cover()'s own `attempt`
        # and cover_mod.generate_cover_text()'s own `previous_titles` so
        # each regenerate produces a genuinely different style/title
        # (see both functions' own docstrings). Reset to empty whenever
        # a DIFFERENT project's cover flow starts (_on_create_clicked/
        # _open_reel - see both below) so one Reel's attempt count never
        # bleeds into another's.
        self._cover_attempt_count: int = 0
        self._cover_previous_titles: list[str] = []
        # 3-cover picker (GENERATE 3 COVERS / PREVIEW / SELECT / EDIT /
        # REGENERATE requirement): the current batch of candidates shown
        # in the picker UI, and which one (if any) the person has
        # clicked SELECT on - purely in-memory UI state, re-derived from
        # jarvis.reel_generator.db's own persisted cover_candidates_data
        # on reopen (see _open_reel() below) rather than duplicating a
        # separate source of truth. `_cover_candidate_next_attempt`
        # tracks how many attempts have been used so far ACROSS every
        # batch shown for this project (not just the current batch) -
        # passed as generate_cover_candidates()'s own `start_attempt` so
        # a REGENERATE ALL click always continues the SAME style
        # rotation instead of restarting it and re-showing an
        # already-seen style.
        self._current_cover_candidates: list[cover_mod.CoverCandidate] = []
        self._selected_cover_candidate_attempt: int | None = None
        self._cover_candidate_next_attempt: int = 0
        self._cover_candidate_all_titles: list[str] = []
        # Real, reported bug fix ("scene shows text + 10/10 quality
        # score but the image preview stays empty, with nothing to
        # explain why"): which scene NUMBERS currently have a real
        # visual-generation call in flight (GENERATE SCENE VISUALS adds
        # every scene it's about to render; REGENERATE SCENE adds just
        # that one number) - _render_storyboard() reads this to show a
        # per-scene GENERATING/NOT GENERATED YET/COMPLETED/FAILED status
        # line distinct from _is_generating above (which is
        # whole-Reel/boolean, not per-scene - see that field's own
        # docstring). Always cleared in the matching result handler,
        # success or failure, so a crashed render never leaves a scene
        # stuck reporting GENERATING forever.
        self._scenes_generating: set[int] = set()
        # Set True only while a real scene-visual render (GENERATE SCENE
        # VISUALS / REGENERATE SCENE) is actually in flight on a
        # background thread - the one signal jarvis.reel_generator
        # .project_status.compute_status_from_visuals() cannot infer
        # from persisted state alone (see that function's own
        # docstring). Cleared in the matching result handler regardless
        # of success/failure/error, so a crashed/failed render never
        # leaves this view stuck reporting GENERATING forever.
        self._is_generating: bool = False
        # Reel Generation Workflow stage's own counterpart to
        # _is_generating above, but for the LATER "Export Final Reel"
        # (Reel Assembly) step specifically, not scene-visual rendering
        # - the GENERATING_REEL state in project_status
        # .compute_reel_status() cannot be inferred from persisted state
        # alone either (same reasoning as _is_generating's own
        # docstring). Cleared in _handle_export_result() regardless of
        # success/failure/error.
        self._is_generating_reel: bool = False
        # Reel Preview stage's own "clickable scene selection" state -
        # purely a UI selection (which scene's own real position in the
        # assembled Reel is currently shown in the preview thumbnail/
        # timeline), never persisted - reopening a project simply
        # defaults back to no selection (the whole-Reel's own real first
        # frame thumbnail), same as every other purely-in-memory UI-only
        # piece of state this view already keeps (e.g. self._is_generating).
        self._reel_preview_selected_scene: int | None = None
        # Content Package + Ready to Publish stage's own in-memory
        # PublishPackage - the persisted source of truth is
        # db.get_project(...).publish_package_data (see
        # jarvis.reel_generator.publish_package's own docstring); this
        # mirrors the exact "load into memory on open, write through on
        # every edit" pattern self._current_caption_package/
        # ._current_visual_plan/etc. already establish, never a second
        # competing source of truth.
        self._current_publish_package: publish_package_mod.PublishPackage | None = None
        self._is_generating_publish_package: bool = False
        # Stage D: this project's own persisted whole-project text mode
        # ("baked_in" - the default, unchanged pre-Stage-D behavior - or
        # "overlay") and CaptionStyle - see jarvis.reel_generator.db's
        # own docstring for the full text_mode/caption_style_data
        # reasoning. A brand-new (never-yet-saved) project defaults to
        # "baked_in" until the person explicitly switches it, matching
        # db.create_project_record()'s own column default.
        self._current_text_mode: str = "baked_in"
        self._current_caption_style: export_mod.CaptionStyle = export_mod.CaptionStyle()

        SectionHeader(self, "AI Reel Generator").pack(
            anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM),
        )

        self._step_indicator_container = ctk.CTkFrame(self, fg_color="transparent")
        self._step_indicator_container.pack(anchor="w", padx=theme.SPACE_LG, pady=(0, theme.SPACE_SM))

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        self._build_prompt_area()
        self._build_footage_area()

        self._status_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._brief_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._brief_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._script_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._script_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._storyboard_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._storyboard_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._cover_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._cover_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._caption_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._caption_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._voiceover_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._voiceover_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._export_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._export_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._quality_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._quality_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        # Content Package + Ready to Publish stage - own container,
        # deliberately separate from _content_package_container below
        # (that one is jarvis.reel_generator.content_package's own,
        # EARLIER, different concept: additional AI-Design-Studio
        # promotional graphics - cover/story/post/carousel - see that
        # module's own docstring). This one is
        # jarvis.reel_generator.publish_package's own Reel+cover+
        # caption+hashtags+audio+publishing-metadata review/approval
        # package - see that module's own docstring for the full
        # distinction.
        self._publish_package_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._publish_package_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._content_package_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._content_package_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._handoff_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._handoff_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._footage_plan_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._footage_plan_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._footage_export_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._footage_export_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._footage_quality_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._footage_quality_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._footage_handoff_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._footage_handoff_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._recent_section_label = SectionHeader(self._scroll, "Recent Reels")
        self._recent_section_label.pack(anchor="w", pady=(theme.SPACE_LG, theme.SPACE_SM))
        self._recent_reels_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._recent_reels_container.pack(fill="x")

        self._refresh_recent_reels()
        self._render_step_indicator()
        self._poll_queue()

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate()
        calls on every panel it shows - re-lists recent Reels (cheap,
        local SQLite read) on every visit, same convention
        jarvis.gui.views.design_studio.dashboard.DesignStudioView
        .refresh() already established."""
        self._refresh_recent_reels()

    def _render_step_indicator(self) -> None:
        """New pipeline brief's own explicit UX requirement: "Use a
        clear step indicator... The user should always know what stage
        they are currently in." Six fixed steps
        (IDEA/STORYBOARD/PREVIEW/GENERATE/EDIT/FINAL), each highlighted
        based on this view's own ALREADY-TRACKED state (no new state of
        its own - purely a read of self._current_brief/_storyboard/
        _visuals/export status) - re-rendered after every state-changing
        action in this view, same "cheap, local, re-render on every
        relevant change" convention as _refresh_recent_reels()."""
        for widget in self._step_indicator_container.winfo_children():
            widget.destroy()

        has_export = (
            self._current_project is not None
            and db.get_project(self._current_project.project_id) is not None
            and db.get_project(self._current_project.project_id).export_path is not None  # type: ignore[union-attr]
        )
        has_visuals = bool(self._current_visuals) and all(v.error is None for v in self._current_visuals)
        has_storyboard = self._current_storyboard is not None
        has_script = self._current_script is not None

        steps = [
            ("1. IDEA", True),
            ("2. STORYBOARD", has_script),
            ("3. PREVIEW", has_storyboard),
            ("4. GENERATE", has_storyboard),
            ("5. EDIT", has_visuals),
            ("6. FINAL", has_export),
        ]
        # The CURRENT step is the LAST one whose own prerequisite is
        # true - a simple, honest "how far has this project actually
        # gotten" read, not a separately-tracked/driftable state flag.
        current_index = max((i for i, (_, reached) in enumerate(steps) if reached), default=0)

        row = ctk.CTkFrame(self._step_indicator_container, fg_color="transparent")
        row.pack(anchor="w")
        for i, (label, reached) in enumerate(steps):
            is_current = i == current_index
            color = theme.ACCENT_PRIMARY if is_current else (theme.TEXT_SECONDARY if reached else theme.TEXT_MUTED)
            ctk.CTkLabel(
                row, text=label,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold" if is_current else "normal"),
                text_color=color, anchor="w",
            ).pack(side="left", padx=(0, theme.SPACE_SM))
            if i < len(steps) - 1:
                ctk.CTkLabel(
                    row, text="→", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                    text_color=theme.TEXT_MUTED,
                ).pack(side="left", padx=(0, theme.SPACE_SM))

        # Stage C's own explicit 5-value project status
        # (DRAFT/STORYBOARD APPROVED/GENERATING/READY FOR
        # REVIEW/EXPORTED) shown alongside (not instead of) the step
        # indicator above - the step indicator answers "how far through
        # the pipeline is this Reel", this answers "what would I call
        # this Reel's current state" (module brief's own exact
        # vocabulary), each read from the same already-tracked state.
        if self._current_project is not None:
            record = db.get_project(self._current_project.project_id)
            if record is not None:
                rendered_count = sum(1 for v in self._current_visuals if v.error is None and v.image_path is not None)
                failed_count = sum(1 for v in self._current_visuals if v.error is not None)
                total_scenes = len(self._current_storyboard.scenes) if self._current_storyboard is not None else 0
                status = project_status.compute_status_from_visuals(
                    record, total_scenes=total_scenes, rendered_scene_count=rendered_count,
                    failed_scene_count=failed_count, is_generating=self._is_generating,
                )
                ctk.CTkLabel(
                    self._step_indicator_container, text=str(status),
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                    text_color=theme.ACCENT_PRIMARY, anchor="w",
                ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

                # Reel Generation Workflow stage's own richer state
                # machine (STORYBOARD_APPROVED -> GENERATING_SCENES ->
                # SCENES_READY -> GENERATING_REEL -> REEL_READY ->
                # REEL_APPROVED) - shown ADDITIONALLY (not instead of)
                # the label above, only once it would say something new
                # (once the storyboard is approved - before that point
                # it's identical to DRAFT and adds nothing) - same "read
                # from already-tracked state, never a competing source
                # of truth" convention as the block above.
                if record.storyboard_approved and record.storyboard_data is not None:
                    reel_status = project_status.compute_reel_status(
                        record, total_scenes=total_scenes, rendered_scene_count=rendered_count,
                        failed_scene_count=failed_count, is_generating_scenes=self._is_generating,
                        is_generating_reel=self._is_generating_reel,
                    )
                    ctk.CTkLabel(
                        self._step_indicator_container, text=f"Reel workflow: {reel_status}",
                        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                        text_color=theme.TEXT_SECONDARY, anchor="w",
                    ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

                # Content Package + Ready to Publish stage's own status
                # line - shown ADDITIONALLY, only once it would say
                # something new (once the Reel is genuinely approved -
                # see compute_publish_status()'s own docstring for why
                # REEL_APPROVED is both this stage's precondition and
                # its own first reported state either way, so showing it
                # only starting here avoids a redundant duplicate of the
                # line above for every earlier stage of the pipeline).
                if record.reel_approved:
                    publish_status = publish_package_mod.compute_publish_status(
                        self._current_publish_package, reel_approved=record.reel_approved,
                        export_path=record.export_path, is_generating_package=self._is_generating_publish_package,
                    )
                    ctk.CTkLabel(
                        self._step_indicator_container, text=f"Content package: {publish_status}",
                        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                        text_color=theme.TEXT_SECONDARY, anchor="w",
                    ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def open_project(self, project_id: str) -> None:
        """Public wrapper over _open_reel() - loads and displays an
        already-created Reel project (brief/script/approval state/
        storyboard/etc., whatever that project already has - see
        _open_reel()'s own docstring) exactly as clicking that project's
        own card in "Recent Reels" already does. Exists so a caller
        OUTSIDE this module (jarvis.gui.app._navigate(), on behalf of
        jarvis.content_studio's own "Preview / Continue" action - see
        that module's own docstring) can open a specific linked project
        without reaching into this view's private _open_reel() method
        or reimplementing any part of what it does - this is the ONLY
        change this module makes for that integration; _open_reel()
        itself, and everything it does, is completely unmodified."""
        self._open_reel(project_id)

    # --- prompt area ---------------------------------------------------------------------

    def _build_prompt_area(self) -> None:
        card = Card(self._scroll)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="What Reel do you want to create?",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._prompt_entry = ctk.CTkTextbox(inner, height=70, wrap="word", fg_color=theme.BG_SURFACE)
        self._prompt_entry.pack(fill="x", pady=(0, theme.SPACE_SM))

        ctk.CTkLabel(
            inner,
            text='Example: "Create a 20 second Reel about 3 morning yoga exercises."',
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        controls_row = ctk.CTkFrame(inner, fg_color="transparent")
        controls_row.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._duration_dropdown = LabeledDropdown(controls_row, "Duration:", _DURATION_DROPDOWN_VALUES)
        self._duration_dropdown.set(_DEFAULT_DURATION_LABEL)
        self._duration_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._style_dropdown = LabeledDropdown(controls_row, "Style:", _STYLE_DROPDOWN_VALUES)
        self._style_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._language_dropdown = LabeledDropdown(controls_row, "Language:", _LANGUAGE_DROPDOWN_VALUES)
        self._language_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._visual_style_dropdown = LabeledDropdown(controls_row, "Visual Style:", _VISUAL_STYLE_DROPDOWN_VALUES)
        self._visual_style_dropdown.set(_DEFAULT_VISUAL_STYLE_LABEL)
        self._visual_style_dropdown.pack(side="left")

        self._create_button = ctk.CTkButton(
            inner, text="🎬 CREATE REEL CONCEPT", command=self._on_create_clicked, height=40,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
        )
        self._create_button.pack(anchor="w")

    def _on_create_clicked(self) -> None:
        request_text = self._prompt_entry.get("1.0", "end").strip()
        if not request_text:
            self._set_status("Describe the Reel you want to create first.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        duration_seconds = _DURATION_LABEL_TO_VALUE.get(self._duration_dropdown.get(), DEFAULT_DURATION_SECONDS)
        style_key = _STYLE_LABEL_TO_KEY.get(self._style_dropdown.get(), "automatic")
        language = _LANGUAGE_LABEL_TO_KEY.get(self._language_dropdown.get(), LANGUAGE_ENGLISH)

        self._set_status("Creating your Reel brief and script...", kind="loading")
        self._create_button.configure(state="disabled")
        self._clear_container(self._brief_container)
        self._clear_container(self._script_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _create_reel_concept(llm, request_text, duration_seconds, style_key, language),
            self._result_queue, source=("create", self),
        )

    def _on_regenerate_script_clicked(self) -> None:
        if self._current_project is None or self._current_brief is None:
            self._set_status(
                "No Reel brief is loaded yet - create or open a Reel first.", kind="error",
            )
            return
        self._set_status("Regenerating the script...", kind="loading")
        self._clear_container(self._script_container)
        llm = self._llm
        project = self._current_project
        brief = self._current_brief
        if llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        run_generation_in_background(
            lambda: _regenerate_script(llm, project, brief),
            self._result_queue, source=("regenerate_script", self),
        )

    # --- Mode A: create from my footage -----------------------------------------------------

    def _build_footage_area(self) -> None:
        card = Card(self._scroll)
        card.pack(fill="x", pady=(theme.SPACE_MD, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="🎥 Or create from your own footage",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SUBTITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner, text="Upload a video and JARVIS will find highlights and plan a Reel edit from it.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        controls_row = ctk.CTkFrame(inner, fg_color="transparent")
        controls_row.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._footage_duration_dropdown = LabeledDropdown(
            controls_row, "Duration:", _FOOTAGE_DURATION_DROPDOWN_VALUES,
        )
        self._footage_duration_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._footage_style_dropdown = LabeledDropdown(controls_row, "Style:", FOOTAGE_STYLES)
        self._footage_style_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._footage_pacing_dropdown = LabeledDropdown(controls_row, "Pacing:", PACING_OPTIONS)
        self._footage_pacing_dropdown.pack(side="left")

        ctk.CTkButton(
            inner, text="🎥 CREATE FROM MY FOOTAGE", command=self._on_upload_footage_clicked, height=40,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w")

    def _on_upload_footage_clicked(self) -> None:
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        selected = filedialog.askopenfilename(title="Select a video", filetypes=_FOOTAGE_FILETYPES)
        if not selected:
            return
        source_path = Path(selected)
        if source_path.suffix.lower() not in VIDEO_SUPPORTED_EXTENSIONS:
            supported = ", ".join(sorted(e.lstrip(".").upper() for e in VIDEO_SUPPORTED_EXTENSIONS))
            self._set_status(f"Unsupported file type. Supported formats: {supported}.", kind="error")
            return

        duration_seconds = _FOOTAGE_DURATION_LABEL_TO_VALUE.get(
            self._footage_duration_dropdown.get(), FOOTAGE_DURATIONS[0],
        )
        style = self._footage_style_dropdown.get()
        pacing = self._footage_pacing_dropdown.get()

        self._set_status(f"Analyzing {source_path.name}... this can take a minute.", kind="loading")
        self._clear_container(self._footage_plan_container)
        self._clear_container(self._footage_export_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _create_footage_reel(llm, source_path, duration_seconds, style, pacing),
            self._result_queue, source=("footage", self),
        )

    def _render_footage_plan(self, plan: video_reel.ReelEditPlan) -> None:
        self._clear_container(self._footage_plan_container)
        card = Card(self._footage_plan_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="REEL EDIT PLAN (from your footage)",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        if plan.insufficient_data or not plan.clips:
            ctk.CTkLabel(
                inner, text=plan.message or "JARVIS couldn't build a Reel from this footage.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w", wraplength=520, justify="left",
            ).pack(anchor="w")
            return

        ctk.CTkLabel(
            inner, text=f"Hook: {plan.hook}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=520, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkLabel(
            inner, text=f"CTA: {plan.cta}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=520, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        for i, clip in enumerate(plan.clips, start=1):
            row = ctk.CTkFrame(inner, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)
            ctk.CTkLabel(
                row_inner,
                text=f"CLIP {i}  ·  {clip.source_start_seconds:.1f}–{clip.source_end_seconds:.1f}s",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")
            ctk.CTkLabel(
                row_inner, text=f"Caption: {clip.caption_text}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=500, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        notes = []
        if plan.silence_removal_suggested:
            notes.append("Silence removal could shorten this further.")
        if plan.zoom_crop_suggested:
            notes.append("Source isn't 9:16 - export will crop to fit.")
        if notes:
            ctk.CTkLabel(
                inner, text=" · ".join(notes),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w", wraplength=520, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(
            button_row, text="🎬 Export This Reel", command=self._on_export_footage_clicked, width=150,
        ).pack(side="left")

    def _on_export_footage_clicked(self) -> None:
        plan = self._current_footage_plan
        filename = self._current_footage_filename
        video_project_id = self._current_footage_video_project_id
        if plan is None or filename is None or video_project_id is None:
            self._set_status("No footage edit plan is loaded yet - upload footage first.", kind="error")
            return
        if plan.insufficient_data or not plan.clips:
            self._set_status(
                plan.message or "This footage has no usable edit plan to export.", kind="error",
            )
            return
        self._set_status("Rendering the final Reel video...", kind="loading")
        run_generation_in_background(
            lambda: _export_footage(video_project_id, filename, plan),
            self._result_queue, source=("footage_export", self),
        )

    def _render_footage_export(self, export_result) -> None:
        self._clear_container(self._footage_export_container)
        card = Card(self._footage_export_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="🎬 FINAL REEL",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner,
            text=(
                f"{export_result.width}×{export_result.height}  ·  {export_result.duration_seconds:.1f} sec  ·  "
                f"{format_file_size(export_result.file_size_bytes)}"
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(
            button_row, text="Open Folder", command=lambda: self._open_folder(export_result.output_path),
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="📤 Send to Instagram Manager", command=self._on_send_footage_to_instagram_clicked,
            width=210, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

        self._run_and_render_quality_control(
            export_result.output_path, self._footage_quality_container,
            target_duration_seconds=(
                self._current_footage_plan.target_duration_seconds if self._current_footage_plan is not None else None
            ),
        )

    def _on_send_footage_to_instagram_clicked(self) -> None:
        llm = self._llm
        plan = self._current_footage_plan
        if llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        if plan is None:
            self._set_status("No footage edit plan is loaded yet - upload footage first.", kind="error")
            return
        record = db.get_project(self._current_project.project_id) if self._current_project is not None else None
        export_path = record.export_path if record is not None else None
        self._set_status("Sending to Instagram AI Manager...", kind="loading")
        run_generation_in_background(
            lambda: _send_footage_reel_handoff(llm, plan, export_path),
            self._result_queue, source=("footage_handoff", self),
        )

    # --- status/container helpers ----------------------------------------------------------

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _clear_container(self, container: ctk.CTkFrame) -> None:
        for widget in container.winfo_children():
            widget.destroy()

    # --- brief + script display -------------------------------------------------------------

    def _render_brief(self, brief: ReelBrief) -> None:
        self._clear_container(self._brief_container)
        card = Card(self._brief_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="REEL BRIEF",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        for field_label, value in (
            ("Topic", brief.topic), ("Audience", brief.audience), ("Goal", brief.objective),
            ("Duration", f"{brief.duration_seconds} sec"), ("Style", brief.style.replace("_", " ").title()),
        ):
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            ctk.CTkLabel(
                row, text=f"{field_label}:", width=90,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=value,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=500, justify="left",
            ).pack(side="left", fill="x", expand=True)

    def _render_script(self, project: ReelProject, script: ReelScript, *, approved: bool) -> None:
        self._clear_container(self._script_container)
        card = Card(self._script_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        header_row = ctk.CTkFrame(inner, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            header_row, text="SCRIPT PREVIEW",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(side="left")
        if approved:
            ctk.CTkLabel(
                header_row, text="✅ Approved",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.SUCCESS, anchor="w",
            ).pack(side="left", padx=(theme.SPACE_SM, 0))

        for segment in script.segments:
            row = ctk.CTkFrame(inner, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)
            ctk.CTkLabel(
                row_inner,
                text=f"{segment.label}  ·  {segment.start_seconds:g}–{segment.end_seconds:g} sec",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")
            ctk.CTkLabel(
                row_inner, text=segment.text,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=520, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        ctk.CTkLabel(
            inner, text=f"Estimated speaking duration: ~{script.estimated_speaking_seconds:g} sec",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_SM, theme.SPACE_MD))

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(
            button_row, text="✅ APPROVE", command=lambda: self._on_approve_clicked(project),
            width=120,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="✏️ EDIT", command=self._on_edit_script_clicked,
            width=100, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🔄 REGENERATE", command=self._on_regenerate_script_clicked,
            width=130, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _on_approve_clicked(self, project: ReelProject) -> None:
        db.approve_script(project.project_id)
        record = db.get_project(project.project_id)
        if record is not None and record.script_data is not None:
            script = _script_from_dict(record.script_data)
            self._current_script = script
            self._render_script(project, script, approved=True)
        self._refresh_recent_reels()
        # Module brief section 4's gate is structural: storyboard
        # generation is only ever triggered from HERE, right after
        # db.approve_script() has actually run - there is no other path
        # in this view that reaches _on_generate_storyboard_clicked().
        self._on_generate_storyboard_clicked()

    def _on_edit_script_clicked(self) -> None:
        self._set_status(
            "Editing the script directly is coming in a later update - use Regenerate for now.",
            kind="muted",
        )

    # --- storyboard ----------------------------------------------------------------------

    def _on_generate_storyboard_clicked(self) -> None:
        project = self._current_project
        brief = self._current_brief
        script = self._current_script
        if project is None or brief is None or script is None:
            self._set_status(
                "No approved script is loaded yet - approve a script before generating the storyboard.",
                kind="error",
            )
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        self._set_status("Building the storyboard...", kind="loading")
        self._clear_container(self._storyboard_container)
        self._clear_container(self._cover_container)
        self._clear_container(self._caption_container)
        self._clear_container(self._export_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _create_storyboard(llm, project, brief, script),
            self._result_queue, source=("storyboard", self),
        )

    def _render_storyboard(self, project: ReelProject, storyboard: Storyboard) -> None:
        self._clear_container(self._storyboard_container)
        card = Card(self._storyboard_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        # Storyboard Creative Controls stage's own approval lock -
        # re-read from the database each render (same "DB is the single
        # source of truth, no separate mirrored instance state" pattern
        # as self._current_text_mode/_current_caption_style) rather than
        # a separate self._storyboard_locked flag that could drift out
        # of sync with what's actually persisted. Once True, every
        # EDIT/REGENERATE/CHANGE/RESET control below is disabled - "lock
        # the storyboard" per the module brief's own APPROVE STORYBOARD
        # requirement.
        record = db.get_project(project.project_id)
        is_locked = record is not None and record.storyboard_approved

        header_row = ctk.CTkFrame(inner, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            header_row, text="VISUAL STORYBOARD",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(side="left")
        if is_locked:
            ctk.CTkLabel(
                header_row, text="🔒 APPROVED - locked for editing",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.ACCENT_PRIMARY, anchor="w",
            ).pack(side="left", padx=(theme.SPACE_SM, 0))

        # Visual Reel Generator stage's own "add a visual variety
        # system" requirement - a local, no-LLM check (jarvis
        # .reel_generator.visual_quality.check_scene_variety()) run
        # purely for DISPLAY here (never blocks rendering on its own -
        # only visual_quality_score does that, via generate_visual_plan()'s
        # own retry loop) so a person can see exactly which consecutive
        # scenes repeat a camera angle/movement/background and choose to
        # regenerate them.
        if self._current_visual_plan is not None:
            variety_issues = visual_quality.check_scene_variety(self._current_visual_plan)
            if variety_issues:
                issue_text = "; ".join(
                    f"Scene {issue.scene_number} repeats {issue.field.replace('_', ' ')} (\"{issue.value}\") from the previous scene"
                    for issue in variety_issues
                )
                ctk.CTkLabel(
                    inner, text=f"⚠️ Low visual variety: {issue_text}",
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                    text_color=theme.DANGER, anchor="w", wraplength=700, justify="left",
                ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        visuals_by_number = {v.scene_number: v for v in self._current_visuals}
        plans_by_number = (
            {p.scene_number: p for p in self._current_visual_plan.scenes}
            if self._current_visual_plan is not None else {}
        )
        for scene in storyboard.scenes:
            row = ctk.CTkFrame(inner, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

            ctk.CTkLabel(
                row_inner,
                text=f"SCENE {scene.number}  ·  {scene.start_seconds:g}–{scene.end_seconds:g} sec  ·  {scene.segment_kind.upper()}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")
            ctk.CTkLabel(
                row_inner, text=f"Text: {scene.on_screen_text}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=500, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
            ctk.CTkLabel(
                row_inner, text=f"Visual: {scene.visual_description}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w", wraplength=500, justify="left",
            ).pack(anchor="w")

            visual = visuals_by_number.get(scene.number)
            # Real, reported bug fix ("scene shows text + a 10/10
            # quality score, but the image preview stays empty, with
            # nothing explaining why"): a scene's ScenePlan (hence its
            # quality score, shown below via _render_scene_plan_summary)
            # can exist well before that scene's own SceneVisual does -
            # Smart Visual Director only writes the PLAN; a visual only
            # exists once GENERATE SCENE VISUALS/REGENERATE SCENE has
            # actually rendered it. This status line is shown for EVERY
            # scene, always ABOVE the quality score, so "10/10" is never
            # mistaken for proof that a real image exists - see
            # SceneVisual.ai_generation_warning's own docstring for the
            # FAILED-but-still-usable-fallback case this also covers.
            if scene.number in self._scenes_generating:
                status_text, status_color = "GENERATING...", theme.ACCENT_PRIMARY
            elif visual is None:
                status_text, status_color = "NOT GENERATED YET", theme.TEXT_MUTED
            elif visual.error is not None:
                status_text, status_color = "FAILED", theme.DANGER
            elif visual.ai_generation_warning is not None:
                status_text, status_color = "COMPLETED (fallback used)", theme.TEXT_MUTED
            else:
                status_text, status_color = "COMPLETED", theme.SUCCESS
            ctk.CTkLabel(
                row_inner, text=f"VISUAL STATUS: {status_text}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=status_color, anchor="w",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
            if visual is not None and visual.ai_generation_warning is not None and visual.error is None:
                ctk.CTkLabel(
                    row_inner, text=visual.ai_generation_warning,
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                    text_color=theme.TEXT_MUTED, anchor="w", wraplength=500, justify="left",
                ).pack(anchor="w")

            if visual is not None:
                if visual.error is not None:
                    ctk.CTkLabel(
                        row_inner, text=f"Couldn't render this scene's visual: {visual.error}",
                        font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                        text_color=theme.DANGER, anchor="w", wraplength=500, justify="left",
                    ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
                elif visual.image_path is not None:
                    # Previously a bare try/except Exception: pass, AND
                    # (the actual root cause, found by launching the REAL
                    # visible desktop app - not a withdrawn/offscreen
                    # window - and taking a real screen capture of the
                    # real click-through result): the thumbnail was NOT
                    # blank pixel-wise (confirmed: thousands of unique
                    # colors, real headline/sticker/badge all present),
                    # but at the previous 68x121 display size, several of
                    # this codebase's own lighter gradient styles (e.g.
                    # "wellness"/"lifestyle" - pale cream/beige
                    # backgrounds) render nearly all their own detail
                    # (the two text-cue overlays especially) too small to
                    # read on a real screen, and the pale background
                    # itself reads as visually blank at that size even
                    # though the pixels underneath are real and varied -
                    # a genuine display-legibility bug, not a generation
                    # failure. Fixed by roughly doubling the thumbnail
                    # size and adding a visible border so a light-style
                    # thumbnail is never indistinguishable from an empty
                    # background, regardless of the render's own palette.
                    try:
                        from PIL import Image

                        pil_image = Image.open(visual.image_path)
                        pil_image.load()  # force full decode now, not lazily during Tk's own later draw call
                        ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(140, 249))
                        thumb_border = ctk.CTkFrame(
                            row_inner, fg_color=theme.BORDER_SUBTLE, corner_radius=theme.RADIUS_BUTTON,
                        )
                        thumb_border.pack(anchor="w", pady=(theme.SPACE_XS, 0))
                        ctk.CTkLabel(thumb_border, image=ctk_image, text="").pack(padx=2, pady=2)
                    except Exception as e:
                        logger.exception(
                            "Couldn't display scene %s's thumbnail (file exists on disk: %s)",
                            scene.number, visual.image_path,
                        )
                        ctk.CTkLabel(
                            row_inner, text=f"Visual was rendered but couldn't be displayed: {e}",
                            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                            text_color=theme.DANGER, anchor="w", wraplength=500, justify="left",
                        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

            plan = plans_by_number.get(scene.number)
            if plan is not None:
                self._render_scene_plan_summary(row_inner, scene, plan)

            scene_button_row = ctk.CTkFrame(row_inner, fg_color="transparent")
            scene_button_row.pack(anchor="w", pady=(theme.SPACE_SM, 0))
            scene_button_state = "disabled" if is_locked else "normal"
            ctk.CTkButton(
                scene_button_row, text="✏️ EDIT SCENE", state=scene_button_state,
                command=lambda s=scene: self._on_edit_scene_clicked(project, storyboard, s),
                width=110, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS))
            ctk.CTkButton(
                scene_button_row, text="🔄 REGENERATE", state=scene_button_state,
                command=lambda s=scene: self._on_regenerate_scene_clicked(project, storyboard, s),
                width=120, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS))
            ctk.CTkButton(
                scene_button_row, text="📷 CHANGE CAMERA", state=scene_button_state,
                command=lambda s=scene: self._on_change_camera_clicked(project, storyboard, s),
                width=140, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS))
            ctk.CTkButton(
                scene_button_row, text="🎨 CHANGE STYLE", state=scene_button_state,
                command=lambda s=scene: self._on_change_style_clicked(project, storyboard, s),
                width=130, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS))
            ctk.CTkButton(
                scene_button_row, text="🔀 CHANGE VISUAL", state=scene_button_state,
                command=lambda s=scene: self._on_change_visual_clicked(project, storyboard, s),
                width=135, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS))
            ctk.CTkButton(
                scene_button_row, text="↩️ RESET SCENE", state=scene_button_state,
                command=lambda s=scene: self._on_reset_scene_clicked(project, storyboard, s),
                width=125, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", pady=(0, theme.SPACE_XS))

            self._render_scene_motion_row(row_inner, project, scene)

        self._render_text_mode_controls(inner, project)
        self._render_reel_mode_control(inner, project)

        storyboard_button_state = "disabled" if is_locked else "normal"
        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(
            button_row, text="🎯 SMART VISUAL DIRECTOR", state=storyboard_button_state,
            command=lambda: self._on_generate_visual_plan_clicked(project, storyboard),
            width=210, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🖼️ GENERATE SCENE VISUALS", command=lambda: self._on_generate_visuals_clicked(project, storyboard),
            width=200, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🔄 REGENERATE ALL", state=storyboard_button_state,
            command=self._on_generate_storyboard_clicked,
            width=170, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        if is_locked:
            ctk.CTkLabel(
                button_row, text="✅ STORYBOARD APPROVED",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.ACCENT_PRIMARY,
            ).pack(side="left", padx=(0, theme.SPACE_SM))
        else:
            ctk.CTkButton(
                button_row, text="✅ APPROVE STORYBOARD",
                command=lambda: self._on_approve_storyboard_clicked(project),
                width=190,
            ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🖼️ Reel Cover", command=self._on_generate_cover_clicked,
            width=120, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="✍️ Caption", command=self._on_generate_caption_clicked,
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🎙️ Voiceover", command=self._on_generate_voiceover_clicked,
            width=130, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🎬 Export Final Reel", command=self._on_export_clicked,
            width=160, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _render_text_mode_controls(self, inner: ctk.CTkFrame, project: ReelProject) -> None:
        """Stage D: the "Textless mode" toggle and, only when it's on,
        the CaptionStyle (font/position/size/animation) dropdowns -
        Stage C's own CaptionStyle system, previously left un-wired in
        the GUI specifically because enabling it while every scene still
        baked its own text into its image would duplicate that text
        (see jarvis.reel_generator.export's own docstring for that
        history). Safe now because switching this toggle ON changes
        the project's own persisted text_mode, which
        _do_generate_visuals()/_do_regenerate_scene() read to render
        genuinely TEXTLESS scene images (jarvis.reel_generator
        .scene_render/.scenes's own render_textless=True) - the export
        step's own auto-derivation (jarvis.reel_generator.export
        .export_reel_video()'s own docstring) then burns these styled
        captions in without ever risking a duplicate."""
        controls_row = ctk.CTkFrame(inner, fg_color="transparent")
        controls_row.pack(fill="x", pady=(theme.SPACE_SM, 0))

        switch_var = ctk.StringVar(value="on" if self._current_text_mode == "overlay" else "off")

        def on_toggle() -> None:
            new_mode = "overlay" if switch_var.get() == "on" else "baked_in"
            self._current_text_mode = new_mode
            db.save_text_mode(project.project_id, new_mode)
            if self._current_storyboard is not None:
                self._render_storyboard(project, self._current_storyboard)

        switch = ctk.CTkSwitch(
            controls_row, text="Textless mode (styled captions burned in at export)",
            variable=switch_var, onvalue="on", offvalue="off", command=on_toggle,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        )
        switch.pack(anchor="w")

        if self._current_text_mode != "overlay":
            return

        style_row = ctk.CTkFrame(inner, fg_color="transparent")
        style_row.pack(fill="x", pady=(theme.SPACE_SM, 0))

        def on_style_change(_=None) -> None:
            new_style = export_mod.CaptionStyle(
                font=font_dropdown.get(), position=position_dropdown.get(),
                size=size_dropdown.get(), animation=animation_dropdown.get(),
            )
            self._current_caption_style = new_style
            db.save_caption_style(project.project_id, dataclasses.asdict(new_style))

        font_dropdown = LabeledDropdown(style_row, "Font:", export_mod.CAPTION_FONT_CHOICES)
        font_dropdown.set(self._current_caption_style.font)
        font_dropdown.dropdown.configure(command=on_style_change)
        font_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        position_dropdown = LabeledDropdown(style_row, "Position:", export_mod.CAPTION_POSITION_CHOICES)
        position_dropdown.set(self._current_caption_style.position)
        position_dropdown.dropdown.configure(command=on_style_change)
        position_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        size_dropdown = LabeledDropdown(style_row, "Size:", export_mod.CAPTION_SIZE_CHOICES)
        size_dropdown.set(self._current_caption_style.size)
        size_dropdown.dropdown.configure(command=on_style_change)
        size_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        animation_dropdown = LabeledDropdown(style_row, "Animation:", export_mod.CAPTION_ANIMATION_CHOICES)
        animation_dropdown.set(self._current_caption_style.animation)
        animation_dropdown.dropdown.configure(command=on_style_change)
        animation_dropdown.pack(side="left")

    # --- NATURAL MOTION / AI VIDEO mode ---------------------------------------------------

    def _render_reel_mode_control(self, inner: ctk.CTkFrame, project: ReelProject) -> None:
        """STATIC / NATURAL MOTION / HYBRID mode selector - the exact
        dropdown-tied-to-DB-field pattern _render_cover_integration_mode_
        control() already established (read db.get_project() fresh
        every render, never a cached copy - _on_export_clicked() and
        _do_generate_visuals() both re-read reel_mode fresh too, so
        changing this dropdown always affects the NEXT click without
        extra wiring). Static (db.REEL_MODE_STATIC) is every project's
        default and the ONLY mode that existed before this feature - a
        project that never touches this control behaves exactly as
        before.

        If Runway isn't configured, an inline notice is shown below the
        selector (NEVER disabling the selector itself - Natural Motion/
        Hybrid stay selectable per this feature's own honesty
        requirement, exactly like jarvis.reel_generator.image_generation
        .is_configured() never disables AI Reel Generator's own Visual
        Story Director, just degrades its output with a clear reason)."""
        project_id = project.project_id
        record = db.get_project(project_id)
        current_mode = record.reel_mode if record is not None else db.REEL_MODE_STATIC

        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(anchor="w", pady=(theme.SPACE_SM, 0), fill="x")
        ctk.CTkLabel(
            mode_row, text="Reel mode:",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, theme.SPACE_SM))

        labels_by_mode = {
            db.REEL_MODE_STATIC: "Static Reel (still images)",
            db.REEL_MODE_NATURAL_MOTION: "Natural Motion Reel (AI video clips)",
            db.REEL_MODE_HYBRID: "Hybrid Reel (mix of static and moving)",
        }
        modes_by_label = {v: k for k, v in labels_by_mode.items()}
        current_label = labels_by_mode.get(current_mode, labels_by_mode[db.REEL_MODE_STATIC])

        dropdown = ctk.CTkOptionMenu(
            mode_row, values=list(labels_by_mode.values()),
            command=lambda label: self._on_reel_mode_changed(project, modes_by_label[label]),
            width=280, fg_color=theme.BG_CARD, button_color=theme.BG_CARD_HOVER,
        )
        dropdown.set(current_label)
        dropdown.pack(side="left")

        if current_mode != db.REEL_MODE_STATIC and not motion_engine.video_generation.is_configured():
            ctk.CTkLabel(
                inner,
                text="⚠️ RUNWAY_API_KEY is not configured - Natural Motion/Hybrid scenes will use their "
                     "still images instead (with an optional Ken Burns pan/zoom).",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.DANGER, anchor="w", wraplength=650, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        if current_mode != db.REEL_MODE_STATIC:
            self._render_motion_settings_panel(inner, project, current_mode)

    def _on_reel_mode_changed(self, project: ReelProject, mode: str) -> None:
        db.save_reel_mode(project.project_id, mode)
        self._set_status("Reel mode saved - it will apply the next time scene visuals/motion are generated.", kind="muted")
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)

    def _current_motion_settings_data(self, project: ReelProject) -> dict:
        record = db.get_project(project.project_id)
        return (record.motion_settings_data if record is not None and record.motion_settings_data else {}) or {}

    def _current_global_motion_settings(self, project: ReelProject) -> MotionSettings:
        data = self._current_motion_settings_data(project).get("global") or {}
        try:
            return MotionSettings(**{k: v for k, v in data.items() if k in MotionSettings.__dataclass_fields__})
        except TypeError:
            return MotionSettings()

    def _render_motion_settings_panel(self, inner: ctk.CTkFrame, project: ReelProject, reel_mode: str) -> None:
        """Requirement 3's own settings vocabulary (intensity/camera/
        people/environment/duration/style), applied to the WHOLE Reel -
        every change is immediately persisted via db.save_motion_settings()
        (same "save on every change, never a separate Save button"
        convention _render_text_mode_controls()'s own CaptionStyle
        dropdowns already use). Per-scene overrides are intentionally
        NOT exposed in this first version (the module brief's own
        "configurable per scene OR for the whole Reel" is satisfied by
        the whole-Reel case here plus Hybrid's own per-scene MOVING/
        STATIC selection below, which is the per-scene control that
        actually matters for requirement 7) - motion_settings_data's own
        "per_scene" key remains reserved for a future per-scene
        intensity/style override without needing another schema
        change."""
        settings = self._current_global_motion_settings(project)
        data = self._current_motion_settings_data(project)

        panel = ctk.CTkFrame(inner, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
        panel.pack(fill="x", pady=(theme.SPACE_SM, 0))
        panel_inner = ctk.CTkFrame(panel, fg_color="transparent")
        panel_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        ctk.CTkLabel(
            panel_inner, text="🎬 MOTION SETTINGS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))

        def save_settings(**overrides) -> None:
            from dataclasses import asdict, replace

            new_settings = replace(settings, **overrides)
            data["global"] = asdict(new_settings)
            db.save_motion_settings(project.project_id, data)

        row1 = ctk.CTkFrame(panel_inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, theme.SPACE_XS))

        intensity_dropdown = LabeledDropdown(row1, "Intensity:", MOTION_INTENSITY_CHOICES)
        intensity_dropdown.set(settings.intensity)
        intensity_dropdown.dropdown.configure(command=lambda v: save_settings(intensity=v))
        intensity_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        style_dropdown = LabeledDropdown(row1, "Style:", MOTION_STYLE_CHOICES)
        style_dropdown.set(settings.style)
        style_dropdown.dropdown.configure(command=lambda v: save_settings(style=v))
        style_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        duration_entry = ctk.CTkEntry(row1, width=70)
        duration_entry.insert(0, str(settings.clip_duration_seconds))
        ctk.CTkLabel(row1, text="Clip length (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        duration_entry.pack(side="left")

        def on_duration_change(_event=None) -> None:
            try:
                value = float(duration_entry.get())
            except ValueError:
                return
            save_settings(clip_duration_seconds=value)

        duration_entry.bind("<FocusOut>", on_duration_change)
        duration_entry.bind("<Return>", on_duration_change)

        row2 = ctk.CTkFrame(panel_inner, fg_color="transparent")
        row2.pack(fill="x", pady=(theme.SPACE_XS, 0))

        camera_var = ctk.StringVar(value="on" if settings.camera_movement_enabled else "off")
        ctk.CTkSwitch(
            row2, text="Camera movement", variable=camera_var, onvalue="on", offvalue="off",
            command=lambda: save_settings(camera_movement_enabled=camera_var.get() == "on"),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left", padx=(0, theme.SPACE_MD))

        people_var = ctk.StringVar(value="on" if settings.people_movement_enabled else "off")
        ctk.CTkSwitch(
            row2, text="People movement", variable=people_var, onvalue="on", offvalue="off",
            command=lambda: save_settings(people_movement_enabled=people_var.get() == "on"),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left", padx=(0, theme.SPACE_MD))

        environment_var = ctk.StringVar(value="on" if settings.environment_animation_enabled else "off")
        ctk.CTkSwitch(
            row2, text="Environment animation", variable=environment_var, onvalue="on", offvalue="off",
            command=lambda: save_settings(environment_animation_enabled=environment_var.get() == "on"),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left")

        if reel_mode == db.REEL_MODE_HYBRID and self._current_storyboard is not None:
            self._render_hybrid_scene_toggle(panel_inner, project, data)

    def _render_hybrid_scene_toggle(self, inner: ctk.CTkFrame, project: ReelProject, motion_settings_data: dict) -> None:
        """HYBRID mode's own per-scene MOVING/STATIC selection
        (requirement 7) - writes scene numbers into motion_settings_data's
        own "moving_scene_numbers" list. A scene NOT in that list stays
        static automatically (jarvis.reel_generator.motion_engine
        .generate_motion_for_scenes()'s own "only scenes present in
        settings_by_scene get attempted" contract), so this toggle is
        the ONE place Hybrid's own static-vs-moving choice is made."""
        storyboard = self._current_storyboard
        if storyboard is None:
            return
        moving_scene_numbers: set[int] = set(motion_settings_data.get("moving_scene_numbers") or [])

        ctk.CTkLabel(
            inner, text="Moving scenes (Hybrid):",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_SM, theme.SPACE_XS))

        toggle_row = ctk.CTkFrame(inner, fg_color="transparent")
        toggle_row.pack(fill="x")

        def on_toggle(scene_number: int, var: ctk.StringVar) -> None:
            current = set(motion_settings_data.get("moving_scene_numbers") or [])
            if var.get() == "on":
                current.add(scene_number)
            else:
                current.discard(scene_number)
            motion_settings_data["moving_scene_numbers"] = sorted(current)
            db.save_motion_settings(project.project_id, motion_settings_data)

        for scene in storyboard.scenes:
            var = ctk.StringVar(value="on" if scene.number in moving_scene_numbers else "off")
            ctk.CTkSwitch(
                toggle_row, text=f"Scene {scene.number}", variable=var, onvalue="on", offvalue="off",
                command=lambda s=scene.number, v=var: on_toggle(s, v),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_MD), pady=(0, theme.SPACE_XS))

    def _current_motion_clip_for_scene(self, scene_number: int) -> SceneMotionClip | None:
        return next((c for c in self._current_motion_clips if c.scene_number == scene_number), None)

    def _render_scene_motion_row(self, row_inner: ctk.CTkFrame, project: ReelProject, scene: Scene) -> None:
        """NATURAL MOTION / HYBRID modes' own per-scene motion status +
        Preview/Compare/Regenerate row (requirement 6) - shown only when
        this scene actually has a motion attempt recorded (a Static-mode
        project, or a Hybrid scene never marked as moving, has none at
        all, so this renders nothing for them - zero visual change to
        the existing storyboard card for every project not using this
        feature)."""
        clip = self._current_motion_clip_for_scene(scene.number)
        if clip is None:
            return

        motion_row = ctk.CTkFrame(row_inner, fg_color="transparent")
        motion_row.pack(anchor="w", pady=(theme.SPACE_XS, 0))

        if clip.error is not None:
            ctk.CTkLabel(
                motion_row, text=f"⚠️ Motion clip failed: {clip.error} - using the still image instead.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.DANGER, anchor="w", wraplength=500, justify="left",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        else:
            ctk.CTkLabel(
                motion_row, text="🎬 Real motion clip generated",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.SUCCESS, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))

        button_row = ctk.CTkFrame(motion_row, fg_color="transparent")
        button_row.pack(anchor="w")
        if clip.video_path is not None:
            ctk.CTkButton(
                button_row, text="▶️ Preview Motion", command=lambda p=clip.video_path: self._open_video(p),
                width=140, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS))
            ctk.CTkButton(
                button_row, text="🖼️ Compare Original", command=lambda c=clip: self._on_compare_motion_clicked(c),
                width=150, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="🔄 Regenerate Motion",
            command=lambda s=scene: self._on_regenerate_motion_clicked(project, s),
            width=155, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
            border_width=1, border_color=theme.BORDER_SUBTLE,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left")

    def _on_compare_motion_clicked(self, clip: SceneMotionClip) -> None:
        """Requirement 6's own "compare the original image with the
        clip" - opens the still image (the real base frame the clip was
        generated FROM) in the OS's own default image viewer, and the
        clip itself via the exact same _open_video() every other Reel
        preview already uses - two real, native viewers side by side is
        this codebase's own established "no new in-app media widget"
        convention (see _open_video()'s own docstring), applied here to
        a comparison instead of a single playback."""
        import os

        if clip.source_image_path is not None and clip.source_image_path.is_file():
            try:
                os.startfile(str(clip.source_image_path))
            except OSError as e:
                self._set_status(f"Couldn't open the original image: {e}", kind="error")
        if clip.video_path is not None:
            self._open_video(clip.video_path)

    def _on_regenerate_motion_clicked(self, project: ReelProject, scene: Scene) -> None:
        """Regenerates motion for just THIS one scene (requirement 6's
        own "regenerate only the selected scene") - never the whole
        batch. On failure, the scene's PREVIOUS clip/still is left
        completely untouched until this new attempt's own outcome is
        known (read-modify-write only ever replaces this one scene's
        entry, and only after generate_motion_for_scene() itself
        returns - never a partial/interrupted write)."""
        storyboard = self._current_storyboard
        visual_plan = self._current_visual_plan
        if storyboard is None or visual_plan is None:
            self._set_status("No storyboard/visual plan loaded for this project.", kind="error")
            return
        visual = next((v for v in self._current_visuals if v.scene_number == scene.number), None)
        plan = visual_plan.for_scene(scene.number)
        if visual is None or plan is None:
            self._set_status(f"Scene {scene.number} has no rendered visual/plan to animate.", kind="error")
            return
        settings = self._current_global_motion_settings(project)
        output_path = project.root_dir / "motion" / f"clip_{scene.number:02d}.mp4"

        def start() -> None:
            self._set_status(f"Regenerating motion for Scene {scene.number}...", kind="loading")
            run_generation_in_background(
                lambda: _regenerate_motion_clip(project, scene, plan, visual, settings, output_path),
                self._result_queue, source=("regenerate_motion", self),
            )

        self._confirm_paid_motion_generation(1, start)

    def _handle_regenerate_motion_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Motion regeneration failed: {result.error}", kind="error")
            return
        project, clip = result.value
        self._current_motion_clips = [
            clip if c.scene_number == clip.scene_number else c for c in self._current_motion_clips
        ]
        if not any(c.scene_number == clip.scene_number for c in self._current_motion_clips):
            self._current_motion_clips.append(clip)
        db.save_motion_clips(project.project_id, [_serialize_motion_clip(c) for c in self._current_motion_clips])
        if clip.error is not None:
            self._set_status(f"Scene {clip.scene_number}'s motion clip failed: {clip.error}", kind="error")
        else:
            self._set_status(f"Scene {clip.scene_number}'s motion clip regenerated.", kind="muted")
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)

    def _render_scene_plan_summary(self, row_inner: ctk.CTkFrame, scene: Scene, plan) -> None:
        """Visual Story Director brief's own storyboard layout: a
        labeled field per line (VOICEOVER/ON-SCREEN TEXT/VISUAL/CAMERA/
        B-ROLL/STICKER/TRANSITION), plus a real thumbnail already shown
        above this block by _render_storyboard() itself (this function
        only adds the TEXT fields, not another image)."""
        plan_row = ctk.CTkFrame(row_inner, fg_color=theme.BG_CARD, corner_radius=theme.RADIUS_BUTTON)
        plan_row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        plan_inner = ctk.CTkFrame(plan_row, fg_color="transparent")
        plan_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        def field(label: str, value: str) -> None:
            if not value:
                return
            row = ctk.CTkFrame(plan_inner, fg_color="transparent")
            row.pack(fill="x", pady=(theme.SPACE_XS, 0))
            ctk.CTkLabel(
                row, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.ACCENT_PRIMARY, anchor="w", width=110,
            ).pack(side="left", anchor="n")
            ctk.CTkLabel(
                row, text=value, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=340, justify="left",
            ).pack(side="left", fill="x", expand=True)

        hook_marker = "🔥 HOOK SCENE" if plan.is_hook else ""
        if hook_marker:
            ctk.CTkLabel(
                plan_inner, text=hook_marker,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.DANGER, anchor="w",
            ).pack(anchor="w")

        field("VOICEOVER:", plan.voiceover)
        field("ON-SCREEN TEXT:", scene.on_screen_text)
        field("VISUAL:", plan.main_visual_prompt)
        camera_value = f"{plan.camera_shot.replace('_', ' ')}, {plan.camera_movement.replace('_', ' ')}"
        field("CAMERA:", camera_value)
        if plan.environment:
            field("ENVIRONMENT:", plan.environment)
        if plan.subject_action:
            field("ACTION:", plan.subject_action)
        if plan.b_roll_type != "none":
            field("B-ROLL:", plan.b_roll_type.replace("_", " "))
        if plan.supporting_visuals:
            field("SUPPORTING:", ", ".join(plan.supporting_visuals))
        sticker_display = f"{plan.sticker_glyph} {plan.sticker}" if plan.sticker != "none" else ""
        field("STICKER:", sticker_display)
        field("TRANSITION:", f"{plan.transition}  ·  motion: {plan.motion.replace('_', ' ')}")
        if plan.music_mood != "none":
            field("MUSIC MOOD:", plan.music_mood)

        for cue in plan.text_cues:
            field(
                "TEXT ANIM:",
                f"{cue.position} {cue.start_seconds:g}-{cue.end_seconds:g}s ({cue.animation}): \"{cue.text}\"",
            )

        # Visual Reel Generator stage's own quality gate score
        # ("would this scene still make sense with text removed") -
        # re-derived from the plan's own stored score, shown in RED
        # when below the render floor so a person immediately sees
        # WHICH scene needs manual attention/regeneration, matching
        # this codebase's own "never silently hide a real problem"
        # convention.
        score_color = theme.TEXT_SECONDARY if plan.visual_quality_score >= visual_quality.MIN_RENDERABLE_QUALITY_SCORE else theme.DANGER
        score_row = ctk.CTkFrame(plan_inner, fg_color="transparent")
        score_row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        ctk.CTkLabel(
            score_row, text="QUALITY:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w", width=110,
        ).pack(side="left", anchor="n")
        ctk.CTkLabel(
            score_row, text=f"{plan.visual_quality_score}/10",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=score_color, anchor="w",
        ).pack(side="left")

    # --- Smart Visual Director (requirements 1-8, 18) ---------------------------------------

    def _on_generate_visual_plan_clicked(self, project: ReelProject, storyboard: Storyboard) -> None:
        try:
            self._do_generate_visual_plan(project, storyboard)
        except Exception as e:
            logger.exception("SMART VISUAL DIRECTOR click handler raised an unexpected exception")
            self._set_status(f"Couldn't start the Smart Visual Director: {e}", kind="error")

    def _do_generate_visual_plan(self, project: ReelProject, storyboard: Storyboard) -> None:
        brief = self._current_brief
        if brief is None:
            self._set_status(
                "No Reel brief is loaded for this project - reopen the Reel from Recent Reels and try again.",
                kind="error",
            )
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        visual_style = _VISUAL_STYLE_LABEL_TO_KEY.get(self._visual_style_dropdown.get(), DEFAULT_VISUAL_STYLE)
        self._set_status("Running the Smart Visual Director...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: _create_visual_plan(llm, project, brief, storyboard, visual_style),
            self._result_queue, source=("visual_plan", self),
        )

    # --- scene visuals ---------------------------------------------------------------------

    def _on_generate_visuals_clicked(self, project: ReelProject, storyboard: Storyboard) -> None:
        # Wrapped in try/except and logged: customtkinter's CTkButton
        # calls its own `command` directly with no try/except of its
        # own (confirmed by reading ctk_button.py in the installed
        # package) - an exception raised anywhere in this method would
        # otherwise only ever reach Tkinter's default
        # report_callback_exception (a bare traceback to stderr), which
        # is invisible when JARVIS is launched via scripts
        # /launch_jarvis.bat with no attached console window, or a
        # packaged build. That silent-crash path matches a real,
        # reported symptom exactly ("the button visually reacts/flashes
        # when clicked, but absolutely nothing else happens... no
        # error message") that no automated test happened to trigger
        # (this method never raised in any tested path) - logging AND
        # showing the real error here, rather than only in tests,
        # ensures a future exception here is never silent again,
        # in the live app as well as in tests.
        try:
            self._do_generate_visuals(project, storyboard)
        except Exception as e:
            logger.exception("GENERATE SCENE VISUALS click handler raised an unexpected exception")
            self._set_status(f"Couldn't start scene visual generation: {e}", kind="error")

    def _do_generate_visuals(self, project: ReelProject, storyboard: Storyboard) -> None:
        brief = self._current_brief
        if brief is None:
            # A real, previously-silent failure mode: this button only
            # exists inside an already-rendered storyboard card (see
            # _render_storyboard()), so `project`/`storyboard` are
            # always valid via closure - but self._current_brief is
            # re-read fresh at click time and could theoretically be
            # unset if the project's own brief was never loaded. Never
            # fail silently here - always tell the person what's
            # missing (module brief section 20's own "never silently
            # fail" convention, applied to the exact bug report this
            # fixes: clicking produced no loading state, no visuals,
            # and no visible error at all).
            self._set_status(
                "No Reel brief is loaded for this project - reopen the Reel from Recent Reels and try again.",
                kind="error",
            )
            return
        visual_plan = self._current_visual_plan
        render_textless = self._current_text_mode == "overlay"
        if visual_plan is None and self._llm is not None:
            # Root-cause fix for the reported bug ("GENERATE SCENE
            # VISUALS still shows a plain light background with text,
            # not real images"): a plain click of this button, without
            # first clicking SMART VISUAL DIRECTOR, used to always fall
            # through to the original, AI-incapable
            # jarvis.reel_generator.scenes.render_all_scenes() path (see
            # _create_scene_visuals()'s own docstring) - real AI image
            # generation exists and IS configured
            # (jarvis.reel_generator.image_generation.is_configured()),
            # but was only ever reachable via the OTHER, ScenePlan-
            # enriched render path, which itself requires a VisualPlan
            # that only SMART VISUAL DIRECTOR produces. Rather than
            # inventing a second, parallel AI-generation path, this
            # implicitly runs the SAME, already-tested Smart Visual
            # Director step first (generate_visual_plan(), exactly as
            # _do_generate_visual_plan() itself calls it) whenever no
            # plan is loaded yet and an LLM is available - collapsing
            # "two clicks" into one, transparently, while leaving every
            # OTHER call site (SMART VISUAL DIRECTOR's own explicit
            # button, an already-loaded visual_plan, per-scene
            # regeneration) completely untouched. When no LLM is
            # configured at all, this intentionally falls through to the
            # unchanged, pre-existing behavior below (original Pillow
            # text-card render) - there is no AI path to reach in that
            # case regardless, so the pre-existing status message
            # (_handle_visuals_result()'s own "Run SMART VISUAL DIRECTOR
            # first" note) still applies.
            self._set_status("Planning scene visuals with the Smart Visual Director...", kind="loading")
            self._is_generating = True
            self._scenes_generating = {s.number for s in storyboard.scenes}
            self._render_step_indicator()
            self._render_storyboard(project, storyboard)
            visual_style = _VISUAL_STYLE_LABEL_TO_KEY.get(self._visual_style_dropdown.get(), DEFAULT_VISUAL_STYLE)
            llm = self._llm
            run_generation_in_background(
                lambda: _create_scene_visuals_with_auto_plan(
                    llm, project, brief, storyboard, visual_style, render_textless,
                ),
                self._result_queue, source=("visuals", self),
            )
            return
        self._set_status("Rendering scene visuals...", kind="loading")
        self._is_generating = True
        self._scenes_generating = {s.number for s in storyboard.scenes}
        self._render_step_indicator()
        self._render_storyboard(project, storyboard)
        run_generation_in_background(
            lambda: _create_scene_visuals(project, brief, storyboard, visual_plan, render_textless),
            self._result_queue, source=("visuals", self),
        )

    # --- storyboard approval (Storyboard Creative Controls stage) --------------------------

    def _on_approve_storyboard_clicked(self, project: ReelProject) -> None:
        """Locks the storyboard (module brief: "lock the storyboard and
        enable the next step: GENERATE REEL") - does NOT itself trigger
        scene-visual generation or export; GENERATE SCENE VISUALS
        remains its own separate, explicit click ("Do not automatically
        render the final Reel before approval")."""
        try:
            db.approve_storyboard(project.project_id)
            if self._current_storyboard is not None:
                self._render_storyboard(project, self._current_storyboard)
            self._set_status(
                "Storyboard approved - it's now locked. Click GENERATE SCENE VISUALS when you're ready to render it.",
                kind="success",
            )
            self._refresh_recent_reels()
            self._render_step_indicator()
        except Exception as e:
            logger.exception("APPROVE STORYBOARD click handler raised an unexpected exception")
            self._set_status(f"Couldn't approve the storyboard: {e}", kind="error")

    # --- per-scene edit / regenerate -----------------------------------------------------

    def _on_edit_scene_clicked(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        """Opens a small modal to edit this scene's own on-screen text
        and visual description - never the whole storyboard - matching
        the module brief's own "regenerating only one scene, not the
        entire Reel" requirement. Saving persists the WHOLE storyboard
        (jarvis.reel_generator.db.save_storyboard() always overwrites
        the full storyboard_data column - there is no per-scene update
        function), with only this one scene's fields changed via
        dataclasses.replace(); every other scene is written back
        unmodified. Does NOT re-render this scene's visual by itself -
        a person clicks REGENERATE SCENE separately once happy with the
        new text, so editing text never triggers a real (costly) image-
        generation API call on its own."""
        try:
            self._do_edit_scene(project, storyboard, scene)
        except Exception as e:
            logger.exception("EDIT SCENE click handler raised an unexpected exception")
            self._set_status(f"Couldn't open the scene editor: {e}", kind="error")

    def _do_edit_scene(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Edit Scene {scene.number}")
        dialog.geometry("480x360")
        dialog.transient(self.winfo_toplevel())

        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="ON-SCREEN TEXT", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w")
        text_box = ctk.CTkTextbox(inner, height=60, wrap="word")
        text_box.pack(fill="x", pady=(theme.SPACE_XS, theme.SPACE_SM))
        text_box.insert("1.0", scene.on_screen_text)

        ctk.CTkLabel(
            inner, text="VISUAL DESCRIPTION", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w")
        visual_box = ctk.CTkTextbox(inner, height=100, wrap="word")
        visual_box.pack(fill="x", pady=(theme.SPACE_XS, theme.SPACE_SM))
        visual_box.insert("1.0", scene.visual_description)

        # CHANGE DURATION (Storyboard Creative Controls stage) - folded
        # into this same dialog rather than a 7th separate button, per
        # the confirmed UI scope. Changing THIS scene's own duration
        # shifts every LATER scene's start_seconds/end_seconds by the
        # same delta, keeping each of their own durations fixed and the
        # whole storyboard contiguous (no gap/overlap) - "change the
        # scene duration without breaking the total Reel structure."
        ctk.CTkLabel(
            inner, text=f"DURATION (seconds) - current total Reel length: {storyboard.scenes[-1].end_seconds:g}s",
            anchor="w", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w")
        duration_entry = ctk.CTkEntry(inner)
        duration_entry.pack(fill="x", pady=(theme.SPACE_XS, theme.SPACE_SM))
        duration_entry.insert(0, f"{scene.duration_seconds:g}")

        def on_save() -> None:
            new_text = text_box.get("1.0", "end").strip()
            new_visual = visual_box.get("1.0", "end").strip()
            duration_text = duration_entry.get().strip()
            try:
                new_duration = float(duration_text) if duration_text else scene.duration_seconds
            except ValueError:
                new_duration = scene.duration_seconds
            new_duration = max(0.5, new_duration)  # a scene shorter than half a second isn't renderable/meaningful

            delta = new_duration - scene.duration_seconds
            updated_scene = dataclasses.replace(
                scene, on_screen_text=new_text or scene.on_screen_text,
                visual_description=new_visual or scene.visual_description,
                end_seconds=scene.start_seconds + new_duration,
            )
            new_scenes = []
            for s in storyboard.scenes:
                if s.number == scene.number:
                    new_scenes.append(updated_scene)
                elif s.number > scene.number and delta != 0:
                    # Shift every LATER scene by the same delta - each
                    # scene's OWN duration stays exactly what it was,
                    # only its position on the timeline moves.
                    new_scenes.append(dataclasses.replace(
                        s, start_seconds=s.start_seconds + delta, end_seconds=s.end_seconds + delta,
                    ))
                else:
                    new_scenes.append(s)
            new_storyboard = Storyboard(scenes=tuple(new_scenes))
            db.save_storyboard(project.project_id, dataclasses.asdict(new_storyboard))
            self._current_storyboard = new_storyboard
            dialog.destroy()
            self._render_storyboard(project, new_storyboard)
            self._set_status(
                f"Scene {scene.number} updated - click REGENERATE SCENE to render its new visual.",
                kind="success",
            )

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(button_row, text="💾 SAVE", command=on_save, width=100).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="Cancel", command=dialog.destroy, width=100,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _on_regenerate_scene_clicked(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        try:
            self._do_regenerate_scene(project, storyboard, scene)
        except Exception as e:
            logger.exception("REGENERATE SCENE click handler raised an unexpected exception")
            self._set_status(f"Couldn't start regenerating scene {scene.number}: {e}", kind="error")

    def _do_regenerate_scene(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        brief = self._current_brief
        if brief is None:
            self._set_status(
                "No Reel brief is loaded for this project - reopen the Reel from Recent Reels and try again.",
                kind="error",
            )
            return
        visual_plan = self._current_visual_plan
        plan = None
        visual_style = ""
        if visual_plan is not None:
            plan = next((p for p in visual_plan.scenes if p.scene_number == scene.number), None)
            visual_style = visual_plan.visual_style
        self._set_status(f"Regenerating scene {scene.number}'s visual...", kind="loading")
        self._is_generating = True
        self._scenes_generating.add(scene.number)
        self._render_step_indicator()
        self._render_storyboard(project, storyboard)
        render_textless = self._current_text_mode == "overlay"
        run_generation_in_background(
            lambda: _regenerate_scene_visual(project, brief, scene, plan, visual_style, render_textless),
            self._result_queue, source=("regenerate_scene", self),
        )

    def _handle_regenerate_scene_result(self, result: GenerationTaskResult) -> None:
        self._is_generating = False
        if result.error:
            self._scenes_generating = set()
            self._set_status(f"Scene regeneration failed: {result.error}", kind="error")
            return
        project, new_visual = result.value
        self._scenes_generating.discard(new_visual.scene_number)
        if new_visual.error is not None:
            if self._current_storyboard is not None:
                self._render_storyboard(project, self._current_storyboard)
            self._set_status(
                f"Scene {new_visual.scene_number} couldn't be regenerated: {new_visual.error}", kind="error",
            )
            return
        # Replace only this one scene's SceneVisual in place - every
        # other already-rendered scene's visual is left completely
        # untouched, matching the module brief's own "regenerate only
        # one scene, not the entire Reel" requirement exactly.
        self._current_visuals = [
            new_visual if v.scene_number == new_visual.scene_number else v for v in self._current_visuals
        ]
        self._clear_status()
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)
        if new_visual.ai_generation_warning is not None:
            # Real, reported bug fix ("REGENERATE doesn't always
            # generate a new image"): the click DID succeed - a real,
            # usable image exists - but the AI photo the person likely
            # expected wasn't the one actually produced (the API call
            # itself failed and this fell back to the text-card render
            # instead). Never silently reported as a plain success.
            self._set_status(
                f"Scene {new_visual.scene_number} regenerated - {new_visual.ai_generation_warning}", kind="muted",
            )
        else:
            self._set_status(f"Scene {new_visual.scene_number} regenerated.", kind="success")

    # --- CHANGE CAMERA / CHANGE STYLE / CHANGE VISUAL / RESET SCENE ------------------------

    def _require_scene_plan(self, project: ReelProject, scene: Scene) -> tuple[ScenePlan | None, str]:
        """Shared precondition check for CHANGE CAMERA/STYLE/VISUAL -
        all three regenerate an EXISTING ScenePlan's own fields (they
        never invent one from nothing), so they need the Smart Visual
        Director to have already run for this project. Returns
        (plan, visual_style) - plan is None (with a status message
        already shown) if no visual plan/no matching scene plan exists
        yet."""
        if self._current_visual_plan is None:
            self._set_status(
                "Run SMART VISUAL DIRECTOR first - these controls adjust an existing visual plan.",
                kind="error",
            )
            return None, ""
        plan = self._current_visual_plan.for_scene(scene.number)
        if plan is None:
            self._set_status(f"Scene {scene.number} has no visual plan yet - try SMART VISUAL DIRECTOR again.", kind="error")
            return None, ""
        return plan, self._current_visual_plan.visual_style

    def _replace_scene_plan(self, project: ReelProject, scene_number: int, new_plan: ScenePlan) -> None:
        """Writes `new_plan` back into self._current_visual_plan (one
        scene replaced, every other scene's own plan untouched - the
        same "only this one scene" guarantee REGENERATE SCENE already
        has) and persists it. Does NOT touch storyboard_data/Scene text
        at all - CHANGE CAMERA/STYLE/VISUAL only ever change ScenePlan
        fields, never scene.on_screen_text."""
        assert self._current_visual_plan is not None
        new_scenes = tuple(
            new_plan if p.scene_number == scene_number else p for p in self._current_visual_plan.scenes
        )
        new_visual_plan = dataclasses.replace(self._current_visual_plan, scenes=new_scenes)
        self._current_visual_plan = new_visual_plan
        db.save_visual_plan(project.project_id, dataclasses.asdict(new_visual_plan), visual_style=new_visual_plan.visual_style)

    def _on_change_camera_clicked(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        try:
            self._do_change_camera(project, storyboard, scene)
        except Exception as e:
            logger.exception("CHANGE CAMERA click handler raised an unexpected exception")
            self._set_status(f"Couldn't change scene {scene.number}'s camera: {e}", kind="error")

    def _do_change_camera(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        plan, _ = self._require_scene_plan(project, scene)
        if plan is None:
            return
        self._do_open_camera_choice_dialog(project, scene, plan)

    def _do_open_camera_choice_dialog(self, project: ReelProject, scene: Scene, plan: ScenePlan) -> None:
        """CHANGE CAMERA's own small choice dialog - "offer options such
        as: close-up, medium shot, wide shot, macro, overhead, POV, low
        angle, high angle, tracking shot, slow push-in" - the module
        brief's own named list, mapped onto this codebase's real
        CAMERA_SHOT_CHOICES/CAMERA_MOVEMENT_CHOICES vocabulary (see
        jarvis.reel_generator.storyboard.regenerate_scene_camera()'s own
        docstring: a camera_shot choice is passed through exactly;
        "low angle"/"high angle"/"tracking shot"/"slow push-in" describe
        MOVEMENT, not shot type, in this codebase's own vocabulary, so
        picking one of those from this dialog sets camera_movement
        instead via a second, small mapping - never a free-text field
        the render pipeline would have to interpret loosely)."""
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Change Camera - Scene {scene.number}")
        dialog.geometry("360x420")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="Pick a new camera angle:", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        # (button label, camera_shot to request | None, camera_movement override | None)
        options: tuple[tuple[str, str | None, str | None], ...] = (
            ("Close-up", "close_up", None), ("Medium shot", "medium_shot", None), ("Wide shot", "wide_shot", None),
            ("Macro", "macro", None), ("Overhead", "overhead", None), ("POV", "point_of_view", None),
            ("Low angle", None, "tracking"), ("High angle", None, "handheld"),
            ("Tracking shot", None, "tracking"), ("Slow push-in", None, "slow_push_in"),
        )
        for label, camera_shot, camera_movement in options:
            def on_pick(cs=camera_shot, cm=camera_movement) -> None:
                dialog.destroy()
                self._run_change_camera(project, scene, plan, camera_shot=cs, camera_movement_override=cm)
            ctk.CTkButton(
                inner, text=label, command=on_pick, height=28,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(fill="x", pady=(0, theme.SPACE_XS))
        ctk.CTkButton(inner, text="Cancel", command=dialog.destroy, height=28).pack(fill="x", pady=(theme.SPACE_SM, 0))

    def _run_change_camera(
        self, project: ReelProject, scene: Scene, plan: ScenePlan, *, camera_shot: str | None, camera_movement_override: str | None,
    ) -> None:
        if self._llm is None:
            return
        self._set_status(f"Changing scene {scene.number}'s camera...", kind="loading")
        self._is_generating = True
        self._render_step_indicator()
        llm = self._llm
        run_generation_in_background(
            lambda: _change_scene_camera(llm, project, scene, plan, camera_shot, camera_movement_override),
            self._result_queue, source=("change_camera", self),
        )

    def _handle_change_camera_result(self, result: GenerationTaskResult) -> None:
        self._is_generating = False
        if result.error:
            self._set_status(f"CHANGE CAMERA failed: {result.error}", kind="error")
            return
        project, new_plan = result.value
        if new_plan is None:
            self._set_status("JARVIS couldn't change this scene's camera - try again.", kind="error")
            return
        self._replace_scene_plan(project, new_plan.scene_number, new_plan)
        self._clear_status()
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)
        self._set_status(
            f"Scene {new_plan.scene_number}'s camera changed (quality {new_plan.visual_quality_score}/10) - "
            "click REGENERATE SCENE to render its new visual.",
            kind="success",
        )

    def _on_change_style_clicked(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        try:
            self._do_change_style(project, storyboard, scene)
        except Exception as e:
            logger.exception("CHANGE STYLE click handler raised an unexpected exception")
            self._set_status(f"Couldn't change scene {scene.number}'s style: {e}", kind="error")

    def _do_change_style(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        plan, _ = self._require_scene_plan(project, scene)
        if plan is None:
            return
        self._do_open_style_choice_dialog(project, scene, plan)

    def _do_open_style_choice_dialog(self, project: ReelProject, scene: Scene, plan: ScenePlan) -> None:
        """CHANGE STYLE's own choice dialog - "cinematic, clean
        lifestyle, luxury, soft aesthetic, modern social media,
        editorial, natural, energetic, minimal", mapped onto this
        codebase's real VISUAL_STYLE_CHOICES vocabulary (the closest
        existing match for each named option - see
        jarvis.reel_generator.visual_plan.VISUAL_STYLE_CHOICES for the
        full real vocabulary)."""
        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Change Style - Scene {scene.number}")
        dialog.geometry("340x420")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="Pick a new visual style:", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        # (button label, jarvis.reel_generator.visual_plan.VISUAL_STYLE_CHOICES value)
        options = (
            ("Cinematic", "cinematic"), ("Clean lifestyle", "lifestyle"), ("Luxury", "luxury"),
            ("Soft aesthetic", "soft_feminine"), ("Modern social media", "modern"), ("Editorial", "educational"),
            ("Natural", "ugc"), ("Energetic", "bold"), ("Minimal", "minimal"),
        )
        for label, visual_style in options:
            def on_pick(style=visual_style) -> None:
                dialog.destroy()
                self._run_change_style(project, scene, plan, style)
            ctk.CTkButton(
                inner, text=label, command=on_pick, height=28,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(fill="x", pady=(0, theme.SPACE_XS))
        ctk.CTkButton(inner, text="Cancel", command=dialog.destroy, height=28).pack(fill="x", pady=(theme.SPACE_SM, 0))

    def _run_change_style(self, project: ReelProject, scene: Scene, plan: ScenePlan, visual_style: str) -> None:
        if self._llm is None:
            return
        self._set_status(f"Changing scene {scene.number}'s style...", kind="loading")
        self._is_generating = True
        self._render_step_indicator()
        llm = self._llm
        run_generation_in_background(
            lambda: _change_scene_style(llm, project, scene, plan, visual_style),
            self._result_queue, source=("change_style", self),
        )

    def _handle_change_style_result(self, result: GenerationTaskResult) -> None:
        self._is_generating = False
        if result.error:
            self._set_status(f"CHANGE STYLE failed: {result.error}", kind="error")
            return
        project, new_plan = result.value
        if new_plan is None:
            self._set_status("JARVIS couldn't change this scene's style - try again.", kind="error")
            return
        self._replace_scene_plan(project, new_plan.scene_number, new_plan)
        self._clear_status()
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)
        self._set_status(
            f"Scene {new_plan.scene_number}'s style changed (quality {new_plan.visual_quality_score}/10) - "
            "click REGENERATE SCENE to render its new visual.",
            kind="success",
        )

    def _on_change_visual_clicked(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        try:
            self._do_change_visual(project, storyboard, scene)
        except Exception as e:
            logger.exception("CHANGE VISUAL click handler raised an unexpected exception")
            self._set_status(f"Couldn't change scene {scene.number}'s visual: {e}", kind="error")

    def _do_change_visual(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        plan, _ = self._require_scene_plan(project, scene)
        if plan is None:
            return
        self._set_status(f"Creating a new visual interpretation for scene {scene.number}...", kind="loading")
        self._is_generating = True
        self._render_step_indicator()
        llm = self._llm
        run_generation_in_background(
            lambda: _change_scene_visual(llm, project, scene, plan),
            self._result_queue, source=("change_visual", self),
        )

    def _handle_change_visual_result(self, result: GenerationTaskResult) -> None:
        self._is_generating = False
        if result.error:
            self._set_status(f"CHANGE VISUAL failed: {result.error}", kind="error")
            return
        project, new_plan = result.value
        if new_plan is None:
            self._set_status("JARVIS couldn't create a new visual interpretation for this scene - try again.", kind="error")
            return
        self._replace_scene_plan(project, new_plan.scene_number, new_plan)
        self._clear_status()
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)
        self._set_status(
            f"Scene {new_plan.scene_number} has a new visual interpretation (quality {new_plan.visual_quality_score}/10) - "
            "click REGENERATE SCENE to render it.",
            kind="success",
        )

    def _on_reset_scene_clicked(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        """RESET SCENE: "restore the original generated version of that
        scene" - reads the ONE-TIME original storyboard/visual-plan
        snapshot (jarvis.reel_generator.db's own
        original_storyboard_data/original_visual_plan_data - see that
        module's own docstring) and restores just this ONE scene's own
        entry from each, leaving every other scene's current (possibly
        edited) state completely untouched. Purely local/synchronous -
        no LLM call, since it's restoring already-generated data, not
        creating anything new."""
        try:
            self._do_reset_scene(project, storyboard, scene)
        except Exception as e:
            logger.exception("RESET SCENE click handler raised an unexpected exception")
            self._set_status(f"Couldn't reset scene {scene.number}: {e}", kind="error")

    def _do_reset_scene(self, project: ReelProject, storyboard: Storyboard, scene: Scene) -> None:
        record = db.get_project(project.project_id)
        if record is None:
            self._set_status("This project could no longer be found.", kind="error")
            return
        if record.original_storyboard_data is None:
            self._set_status("There's no original version of this storyboard to reset to.", kind="error")
            return

        original_storyboard = _storyboard_from_dict(record.original_storyboard_data)
        original_scene = next((s for s in original_storyboard.scenes if s.number == scene.number), None)
        if original_scene is None:
            self._set_status(f"Scene {scene.number} doesn't exist in the original storyboard.", kind="error")
            return

        new_scenes = tuple(
            original_scene if s.number == scene.number else s for s in storyboard.scenes
        )
        new_storyboard = Storyboard(scenes=new_scenes)
        db.save_storyboard(project.project_id, dataclasses.asdict(new_storyboard))
        self._current_storyboard = new_storyboard

        if record.original_visual_plan_data is not None and self._current_visual_plan is not None:
            original_visual_plan = _visual_plan_from_dict(record.original_visual_plan_data)
            original_plan = original_visual_plan.for_scene(scene.number)
            if original_plan is not None:
                self._replace_scene_plan(project, scene.number, original_plan)

        self._render_storyboard(project, new_storyboard)
        self._set_status(f"Scene {scene.number} restored to its original generated version.", kind="success")

    def _on_generate_cover_clicked(self) -> None:
        """GENERATE 3 COVERS (module brief's own "automatically create 3
        different cover variants" requirement) - the SAME click this
        button/every other "Regenerate Cover" call site already used,
        now producing a fresh batch of 3 instead of 1. Continues the
        SAME style-rotation/previous-titles state across every batch
        shown so far for this project (self._cover_candidate_next_attempt/
        _cover_candidate_all_titles), so a second REGENERATE ALL never
        re-shows a style/title already seen in an earlier batch."""
        project = self._current_project
        brief = self._current_brief
        script = self._current_script
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        if project is None or brief is None or script is None:
            self._set_status(
                "No Reel brief/script is loaded yet - create or open a Reel first.", kind="error",
            )
            return
        self._set_status("Generating 3 cover variants...", kind="loading")
        llm = self._llm
        start_attempt = self._cover_candidate_next_attempt
        previous_titles = tuple(self._cover_candidate_all_titles)
        run_generation_in_background(
            lambda: _create_cover_candidates(
                llm, project, brief, script, start_attempt=start_attempt, previous_titles=previous_titles,
            ),
            self._result_queue, source=("cover", self),
        )

    def _on_regenerate_single_cover_candidate_clicked(self, attempt: int) -> None:
        """REGENERATE for just ONE of the 3 candidates (module brief's
        own per-candidate REGENERATE, distinct from GENERATE 3 COVERS'
        own whole-batch regenerate above) - replaces only that one
        candidate in self._current_cover_candidates, leaving the other
        two exactly as they were, same "regenerate only the one thing
        that was clicked" convention as REGENERATE SCENE."""
        project = self._current_project
        brief = self._current_brief
        script = self._current_script
        if self._llm is None or project is None or brief is None or script is None:
            self._set_status("No Reel brief/script is loaded yet - create or open a Reel first.", kind="error")
            return
        self._set_status(f"Regenerating cover variant {attempt + 1}...", kind="loading")
        llm = self._llm
        previous_titles = tuple(self._cover_candidate_all_titles)
        run_generation_in_background(
            lambda: _create_single_cover_candidate(llm, project, brief, script, attempt=attempt, previous_titles=previous_titles),
            self._result_queue, source=("cover_single", self),
        )

    def _on_select_cover_candidate_clicked(self, attempt: int) -> None:
        """SELECT (module brief's own requirement) - picks one of the 3
        candidates as the Reel's actual cover, via the SAME
        db.save_cover_path() every single-cover generation already used
        (export/publish hand-off read that one column, completely
        unaware a picker UI exists - no change needed there)."""
        project = self._current_project
        if project is None:
            return
        candidate = next((c for c in self._current_cover_candidates if c.attempt == attempt), None)
        if candidate is None:
            return
        db.save_cover_path(project.project_id, str(candidate.image_path))
        self._selected_cover_candidate_attempt = attempt
        self._set_status(f"Cover variant {attempt + 1} selected and saved.", kind="success")
        self._render_cover_candidates()
        self._refresh_recent_reels()

    def _on_edit_cover_candidate_clicked(self, attempt: int) -> None:
        """EDIT COVER (module brief's own "let me edit text, colors,
        font, position" requirement) - opens a real modal with the
        candidate's own current title/supporting text pre-filled, a
        color picker for headline/background colors (Tkinter's own
        built-in colorchooser - no new dependency), a dropdown for the
        codebase's only two available fonts (see jarvis.design_studio
        .styles's own module docstring for why only two exist at all),
        and a top/bottom position choice - SAVE re-renders that one
        candidate in place via cover_mod.render_cover_with_overrides()
        and, if it was already the SELECTED one, re-saves cover_path too
        so the edit is reflected in the actual exported Reel."""
        project = self._current_project
        brief = self._current_brief
        if project is None or brief is None:
            self._set_status("No Reel brief is loaded for this project - reopen the Reel from Recent Reels and try again.", kind="error")
            return
        candidate = next((c for c in self._current_cover_candidates if c.attempt == attempt), None)
        if candidate is None:
            return
        self._open_cover_edit_dialog(project, brief, candidate)

    def _open_cover_edit_dialog(self, project: ReelProject, brief: ReelBrief, candidate: cover_mod.CoverCandidate) -> None:
        from tkinter import colorchooser

        current_style = cover_mod.resolve_style(cover_mod.style_for_attempt(brief.style, candidate.attempt))
        state = {
            "headline_color": current_style.headline_color,
            "background_color_1": current_style.background_color_1,
            "background_color_2": current_style.background_color_2,
        }

        dialog = ctk.CTkToplevel(self)
        dialog.title(f"Edit Cover Variant {candidate.attempt + 1}")
        dialog.geometry("420x560")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="Title:", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
        ).pack(anchor="w")
        title_entry = ctk.CTkTextbox(inner, height=50, wrap="word")
        title_entry.insert("1.0", candidate.cover_text.title)
        title_entry.pack(fill="x", pady=(0, theme.SPACE_SM))

        ctk.CTkLabel(
            inner, text="Supporting text:", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
        ).pack(anchor="w")
        supporting_entry = ctk.CTkTextbox(inner, height=40, wrap="word")
        supporting_entry.insert("1.0", candidate.cover_text.supporting_text)
        supporting_entry.pack(fill="x", pady=(0, theme.SPACE_SM))

        def _pick_color(key: str, label_widget) -> None:
            picked = colorchooser.askcolor(color=state[key], title="Choose a color")
            if picked and picked[1]:
                state[key] = picked[1]
                label_widget.configure(text=f"{key.replace('_', ' ').title()}: {picked[1]}")

        for key in ("headline_color", "background_color_1", "background_color_2"):
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            color_label = ctk.CTkLabel(
                row, text=f"{key.replace('_', ' ').title()}: {state[key]}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            )
            color_label.pack(side="left")
            ctk.CTkButton(
                row, text="Choose...", width=80, command=lambda k=key, w=color_label: _pick_color(k, w),
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(side="right")

        ctk.CTkLabel(
            inner, text="Font:", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))
        font_choices = {"Bold": _COVER_EDIT_FONT_BOLD, "Regular": _COVER_EDIT_FONT_REGULAR}
        font_var = ctk.StringVar(value="Bold" if current_style.headline_font == _COVER_EDIT_FONT_BOLD else "Regular")
        ctk.CTkOptionMenu(inner, values=list(font_choices.keys()), variable=font_var).pack(fill="x", pady=(0, theme.SPACE_SM))

        ctk.CTkLabel(
            inner, text="Text position:", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
        ).pack(anchor="w")
        position_var = ctk.StringVar(value="Top")
        ctk.CTkOptionMenu(inner, values=["Top", "Bottom"], variable=position_var).pack(fill="x", pady=(0, theme.SPACE_MD))

        status_label = ctk.CTkLabel(inner, text="", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION), text_color=theme.DANGER, anchor="w")
        status_label.pack(anchor="w", pady=(0, theme.SPACE_XS))

        def on_save() -> None:
            overrides = cover_mod.CoverEditOverrides(
                title=title_entry.get("1.0", "end").strip() or None,
                supporting_text=supporting_entry.get("1.0", "end").strip(),
                headline_color=state["headline_color"], background_color_1=state["background_color_1"],
                background_color_2=state["background_color_2"], headline_font=font_choices[font_var.get()],
                text_position="bottom" if position_var.get() == "Bottom" else "top",
            )
            try:
                result = cover_mod.render_cover_with_overrides(
                    brief, candidate.cover_text, overrides, output_path=candidate.image_path, attempt=candidate.attempt,
                )
            except cover_mod.CoverError as e:
                status_label.configure(text=f"Couldn't render: {e}")
                return
            new_cover_text, _ = cover_mod.apply_cover_edit_overrides(candidate.cover_text, overrides)
            self._current_cover_candidates = [
                dataclasses.replace(c, cover_text=new_cover_text, image_path=result.output_path)
                if c.attempt == candidate.attempt else c
                for c in self._current_cover_candidates
            ]
            self._save_current_cover_candidates(project)
            if self._selected_cover_candidate_attempt == candidate.attempt:
                db.save_cover_path(project.project_id, str(result.output_path))
            dialog.destroy()
            self._render_cover_candidates()
            self._set_status(f"Cover variant {candidate.attempt + 1} updated.", kind="success")

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(button_row, text="💾 SAVE", command=on_save, width=100).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="Cancel", command=dialog.destroy, width=100,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _save_current_cover_candidates(self, project: ReelProject) -> None:
        db.save_cover_candidates(project.project_id, [
            {
                "attempt": c.attempt, "style_name": c.style_name,
                "cover_text": dataclasses.asdict(c.cover_text), "image_path": str(c.image_path),
            }
            for c in self._current_cover_candidates
        ])

    def _render_cover(self, cover_path: Path) -> None:
        """Fallback single-cover display - used only when reopening an
        OLDER project that has a saved cover_path but no
        cover_candidates_data (a cover generated before this stage
        existed, or a project where the candidate set itself is no
        longer on disk) - see _open_reel()'s own docstring for exactly
        when this path is taken instead of _render_cover_candidates()."""
        self._clear_container(self._cover_container)
        card = Card(self._cover_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="🖼️ REEL COVER",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        try:
            from PIL import Image

            pil_image = Image.open(cover_path)
            ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(158, 281))
            ctk.CTkLabel(inner, image=ctk_image, text="").pack(anchor="w")
        except Exception:
            pass
        ctk.CTkButton(
            inner, text="🔄 Regenerate (3 new variants)", command=self._on_generate_cover_clicked,
            width=200, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def _render_cover_candidates(self) -> None:
        """The 3-cover picker's own UI (module brief's own "show them in
        a modern interface so I can preview and select one" requirement)
        - a horizontal row of candidate cards, each with its own real
        rendered thumbnail (1080x1920 - Instagram Reels' own 9:16
        format, module brief requirement 4), SELECT/EDIT/REGENERATE
        buttons, and a visible "SELECTED" marker on whichever one (if
        any) is currently saved as cover_path. A short note about
        Instagram's own profile-grid crop is shown once, since this
        renderer's own headline placement (jarvis.design_studio.render's
        fixed safe-area layout, unchanged here) sits close to that
        crop's own edge - a real, honest limitation, not fabricated
        grid-safety this codebase doesn't actually guarantee."""
        self._clear_container(self._cover_container)
        if not self._current_cover_candidates:
            return
        card = Card(self._cover_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="🖼️ CHOOSE A REEL COVER",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkLabel(
            inner,
            text="Instagram's profile grid crops this 9:16 cover toward the center - keep the most important "
                 "text away from the very top/bottom if it also needs to read well there.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=650, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        for candidate in self._current_cover_candidates:
            self._render_one_cover_candidate_card(row, candidate)

        ctk.CTkButton(
            inner, text="🔄 REGENERATE ALL (3 new variants)", command=self._on_generate_cover_clicked,
            width=220, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

        self._render_cover_integration_mode_control(inner)

    def _render_cover_integration_mode_control(self, inner: ctk.CTkFrame) -> None:
        """USE COVER AS... (this bug-fix's own requirement 3): where the
        selected cover actually goes - db.COVER_INTEGRATION_MODE_INSTAGRAM
        (separate cover for the Instagram profile grid only - the
        original, only behavior before this fix; still the default for
        every existing project, see db.save_cover_integration_mode()'s
        own docstring), db.COVER_INTEGRATION_MODE_INTRO (spliced in as
        the exported video's own first 1-2 seconds instead), or
        db.COVER_INTEGRATION_MODE_BOTH (both at once). Reads/writes
        straight from/to the database (db.get_project()/
        db.save_cover_integration_mode()) rather than keeping a second,
        in-memory copy of this one small field - _on_export_clicked()
        re-reads the persisted value fresh on every export, so changing
        this dropdown always affects the NEXT export/REGENERATE without
        needing any extra wiring (requirement 4)."""
        project = self._current_project
        if project is None:
            return
        record = db.get_project(project.project_id)
        current_mode = record.cover_integration_mode if record is not None else db.COVER_INTEGRATION_MODE_INSTAGRAM

        mode_row = ctk.CTkFrame(inner, fg_color="transparent")
        mode_row.pack(anchor="w", pady=(theme.SPACE_SM, 0), fill="x")
        ctk.CTkLabel(
            mode_row, text="Use cover as:",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(side="left", padx=(0, theme.SPACE_SM))

        labels_by_mode = {
            db.COVER_INTEGRATION_MODE_INSTAGRAM: "Instagram Cover (profile grid only)",
            db.COVER_INTEGRATION_MODE_INTRO: "Intro Cover (first 1-2s of video)",
            db.COVER_INTEGRATION_MODE_BOTH: "Both",
        }
        modes_by_label = {v: k for k, v in labels_by_mode.items()}
        current_label = labels_by_mode.get(current_mode, labels_by_mode[db.COVER_INTEGRATION_MODE_INSTAGRAM])

        dropdown = ctk.CTkOptionMenu(
            mode_row, values=list(labels_by_mode.values()),
            command=lambda label: self._on_cover_integration_mode_changed(modes_by_label[label]),
            width=260, fg_color=theme.BG_CARD, button_color=theme.BG_CARD_HOVER,
        )
        dropdown.set(current_label)
        dropdown.pack(side="left")

    def _on_cover_integration_mode_changed(self, mode: str) -> None:
        project = self._current_project
        if project is None:
            return
        db.save_cover_integration_mode(project.project_id, mode)
        self._set_status("Cover integration setting saved - it will apply on the next export.", kind="muted")

    def _render_one_cover_candidate_card(self, row: ctk.CTkFrame, candidate: cover_mod.CoverCandidate) -> None:
        is_selected = self._selected_cover_candidate_attempt == candidate.attempt
        card = ctk.CTkFrame(
            row, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON,
            border_width=2 if is_selected else 0, border_color=theme.SUCCESS,
        )
        card.pack(side="left", padx=(0, theme.SPACE_SM))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(padx=theme.SPACE_SM, pady=theme.SPACE_SM)
        try:
            from PIL import Image

            pil_image = Image.open(candidate.image_path)
            ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(130, 231))
            ctk.CTkLabel(inner, image=ctk_image, text="").pack()
        except Exception:
            ctk.CTkLabel(
                inner, text="Couldn't load preview", text_color=theme.DANGER,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack()
        ctk.CTkLabel(
            inner, text=("✅ SELECTED" if is_selected else candidate.style_name.replace("_", " ").title()),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.SUCCESS if is_selected else theme.TEXT_MUTED,
        ).pack(pady=(theme.SPACE_XS, theme.SPACE_XS))
        ctk.CTkButton(
            inner, text="✅ Select", width=120, height=26,
            command=lambda a=candidate.attempt: self._on_select_cover_candidate_clicked(a),
            state="disabled" if is_selected else "normal",
        ).pack(pady=(0, theme.SPACE_XS))
        ctk.CTkButton(
            inner, text="✏️ Edit", width=120, height=26,
            command=lambda a=candidate.attempt: self._on_edit_cover_candidate_clicked(a),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(pady=(0, theme.SPACE_XS))
        ctk.CTkButton(
            inner, text="🔄 Regenerate", width=120, height=26,
            command=lambda a=candidate.attempt: self._on_regenerate_single_cover_candidate_clicked(a),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack()

    # --- caption -------------------------------------------------------------------------

    def _on_generate_caption_clicked(self) -> None:
        project = self._current_project
        brief = self._current_brief
        script = self._current_script
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        if project is None or brief is None or script is None:
            self._set_status(
                "No Reel brief/script is loaded yet - create or open a Reel first.", kind="error",
            )
            return
        self._set_status("Writing the caption and hashtags...", kind="loading")
        llm = self._llm
        run_generation_in_background(
            lambda: _create_caption(llm, project, brief, script),
            self._result_queue, source=("caption", self),
        )

    def _render_caption(self, package: caption_mod.ReelCaptionPackage) -> None:
        self._clear_container(self._caption_container)
        card = Card(self._caption_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="✍️ CAPTION & HASHTAGS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        if package.caption is not None:
            caption_text = package.caption.get("medium_caption", "")
            caption_box = ctk.CTkTextbox(inner, height=80, wrap="word", fg_color=theme.BG_SURFACE)
            caption_box.pack(fill="x", pady=(0, theme.SPACE_SM))
            caption_box.insert("1.0", caption_text)
            caption_box.configure(state="disabled")
            ctk.CTkButton(
                inner, text="📋 Copy Caption", command=lambda: self._copy_to_clipboard(caption_text),
                width=140, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
            ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        else:
            ctk.CTkLabel(
                inner, text="Couldn't generate a caption for this Reel.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        if package.hashtags is not None:
            all_hashtags = [h for group in package.hashtags.values() for h in group]
            hashtags_text = " ".join(all_hashtags)
            ctk.CTkLabel(
                inner, text=hashtags_text,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=600, justify="left",
            ).pack(anchor="w")

        ctk.CTkButton(
            inner, text="🔄 Regenerate Caption", command=self._on_generate_caption_clicked,
            width=170, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

    # --- voiceover (Stage B) --------------------------------------------------------------

    def _on_generate_voiceover_clicked(self) -> None:
        try:
            self._do_generate_voiceover()
        except Exception as e:
            logger.exception("GENERATE VOICEOVER click handler raised an unexpected exception")
            self._set_status(f"Couldn't start voiceover generation: {e}", kind="error")

    def _do_generate_voiceover(self) -> None:
        project = self._current_project
        brief = self._current_brief
        storyboard = self._current_storyboard
        if project is None or brief is None or storyboard is None:
            self._set_status(
                "No storyboard is loaded yet - approve a script and generate a storyboard first.",
                kind="error",
            )
            return
        self._set_status("Generating the voiceover...", kind="loading")
        scenes = storyboard.scenes
        language = brief.language
        run_generation_in_background(
            lambda: _create_voiceover(project, scenes, language),
            self._result_queue, source=("voiceover", self),
        )

    def _handle_voiceover_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Voiceover generation failed: {result.error}", kind="error")
            return
        project, voiceover_result = result.value
        if not voiceover_result.ok:
            # A real, actionable failure (not configured / Azure request
            # failed / no narration text) - never silently do nothing,
            # matching this module's own "never fail silently" convention.
            # Any PREVIOUSLY successful voiceover for this project is left
            # completely untouched (this failed attempt never overwrote
            # it) - only this project's own db.clear_voiceover() call
            # inside _create_voiceover() would have cleared it, and that
            # only happens when there WAS a previous voiceover recorded.
            self._set_status(f"Voiceover generation failed: {voiceover_result.error}", kind="error")
            self._render_voiceover(voiceover_result=voiceover_result, output_path=None)
            return
        self._current_voiceover_path = voiceover_result.output_path
        self._current_voiceover_text = voiceover_result.narration_text
        self._clear_status()
        self._render_voiceover(voiceover_result=voiceover_result, output_path=voiceover_result.output_path)

    def _render_voiceover(self, *, voiceover_result, output_path: Path | None) -> None:
        self._clear_container(self._voiceover_container)
        card = Card(self._voiceover_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="🎙️ VOICEOVER",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        if output_path is not None:
            ctk.CTkLabel(
                inner, text=f"✓ Saved to {output_path.name}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
            narration_box = ctk.CTkTextbox(inner, height=70, wrap="word", fg_color=theme.BG_SURFACE)
            narration_box.pack(fill="x", pady=(0, theme.SPACE_SM))
            narration_box.insert("1.0", voiceover_result.narration_text)
            narration_box.configure(state="disabled")
        else:
            ctk.CTkLabel(
                inner, text=voiceover_result.error or "Couldn't generate a voiceover for this Reel.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w", wraplength=600, justify="left",
            ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        ctk.CTkButton(
            inner, text="🔄 Regenerate Voiceover", command=self._on_generate_voiceover_clicked,
            width=180, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def _copy_to_clipboard(self, text: str) -> None:
        self.clipboard_clear()
        self.clipboard_append(text)
        self._set_status("Caption copied to clipboard.", kind="muted")

    # --- final export ----------------------------------------------------------------------

    def _on_export_clicked(self) -> None:
        project = self._current_project
        storyboard = self._current_storyboard
        if project is None or storyboard is None:
            self._set_status(
                "No storyboard is loaded yet - approve a script and generate a storyboard first.",
                kind="error",
            )
            return
        incomplete = [v for v in self._current_visuals if v.error is not None or v.image_path is None]
        if incomplete or len(self._current_visuals) != len(storyboard.scenes):
            self._set_status(
                "Every scene needs a successfully rendered visual before exporting - "
                "regenerate any failed scenes first.", kind="error",
            )
            return
        self._set_status("Rendering the final Reel video...", kind="loading")
        self._is_generating_reel = True
        self._render_step_indicator()
        scenes = storyboard.scenes
        visuals = list(self._current_visuals)
        voiceover_path = self._current_voiceover_path
        caption_style = self._current_caption_style if self._current_text_mode == "overlay" else None

        # Cover-in-export bug fix, requirement 2/4: re-read the project's
        # OWN persisted cover_path/cover_integration_mode fresh on every
        # single export click (never a cached in-memory copy) - so
        # picking a different cover, changing the "Use cover as" dropdown,
        # or clicking REGENERATE and exporting again always reflects
        # whatever is currently saved, with zero extra wiring needed for
        # either of those actions individually to "take effect".
        record = db.get_project(project.project_id)
        cover_intro_path: Path | None = None
        if record is not None and record.cover_integration_mode in (
            db.COVER_INTEGRATION_MODE_INTRO, db.COVER_INTEGRATION_MODE_BOTH,
        ):
            candidate_cover_path = Path(record.cover_path) if record.cover_path else None
            if candidate_cover_path is not None and candidate_cover_path.is_file():
                cover_intro_path = candidate_cover_path
            else:
                # Requirement 7 (pre-export check): the user asked for an
                # intro cover but there's no real, on-disk cover file to
                # use - a silent "export without it" would contradict
                # their own chosen setting, so this is a real, actionable
                # error rather than a best-effort fallback.
                self._set_status(
                    "Cover integration is set to Intro/Both, but no cover has been generated/selected yet - "
                    "generate or select a cover first, or switch 'Use cover as' to Instagram Cover.",
                    kind="error",
                )
                self._is_generating_reel = False
                self._render_step_indicator()
                return

        # NATURAL MOTION / HYBRID modes: re-read reel_mode/motion_clips_data
        # fresh from DB too (same "never a cached in-memory copy"
        # discipline as cover_integration_mode above) - for a Static
        # project (record.reel_mode == db.REEL_MODE_STATIC, the default),
        # clip_by_scene stays None and this export call is textually
        # identical to before this feature existed.
        clip_by_scene: dict[int, Path] | None = None
        if record is not None and record.reel_mode != db.REEL_MODE_STATIC and record.motion_clips_data:
            clip_by_scene = {}
            for clip_data in record.motion_clips_data:
                clip = _deserialize_motion_clip(clip_data)
                if clip.video_path is not None and clip.video_path.is_file():
                    clip_by_scene[clip.scene_number] = clip.video_path
            if not clip_by_scene:
                clip_by_scene = None

        run_generation_in_background(
            lambda: _create_export(project, scenes, visuals, voiceover_path, caption_style, cover_intro_path, clip_by_scene),
            self._result_queue, source=("export", self),
        )

    def _render_export(self, result) -> None:
        self._clear_container(self._export_container)
        card = Card(self._export_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="🎬 FINAL REEL",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner,
            text=(
                f"{result.width}×{result.height}  ·  {result.duration_seconds:.1f} sec  ·  "
                f"{format_file_size(result.file_size_bytes)}"
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        # NATURAL MOTION / HYBRID modes: one or more requested clips
        # fell back to their still image at splice time (too short/
        # unreadable - see export_reel_video()'s own clip_by_scene
        # docstring) - shown here as a real, specific notice (never
        # silently absorbed), requirement 6's "show a clear error"
        # applied to the export stage itself.
        if getattr(result, "scene_fallback_warnings", ()):
            for warning in result.scene_fallback_warnings:
                ctk.CTkLabel(
                    inner, text=f"⚠️ {warning}",
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                    text_color=theme.DANGER, anchor="w", wraplength=600, justify="left",
                ).pack(anchor="w", pady=(0, theme.SPACE_XS))

        self._render_reel_preview(inner, result)

        # REEL_READY / REEL_APPROVED (Reel Generation Workflow stage):
        # a real, freshly-exported Reel starts at REEL_READY - "REVIEW
        # REEL" - and requires its own explicit APPROVE REEL click
        # before "Send to Instagram Manager" (module brief's own
        # "publishing must remain a separate future stage requiring
        # explicit user approval") becomes available - a SEPARATE,
        # LATER lock from storyboard_approved (see
        # jarvis.reel_generator.db's own docstring for the full
        # reel_approved reasoning). db.save_export_path() always clears
        # reel_approved on a fresh export, so a brand-new export is
        # never mistaken for an already-approved one.
        record = db.get_project(self._current_project.project_id) if self._current_project is not None else None
        is_reel_approved = record is not None and record.reel_approved

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(
            button_row, text="▶️ Play Reel", command=lambda: self._open_video(result.output_path),
            width=120,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🔄 REGENERATE REEL", command=self._on_export_clicked,
            state="disabled" if is_reel_approved else "normal",
            width=170, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="Open Folder", command=lambda: self._open_folder(result.output_path),
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="✨ Content Package", command=self._on_create_content_package_clicked, width=160,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        if is_reel_approved:
            ctk.CTkLabel(
                button_row, text="✅ REEL APPROVED",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.ACCENT_PRIMARY,
            ).pack(side="left", padx=(0, theme.SPACE_SM))
        else:
            ctk.CTkButton(
                button_row, text="✅ APPROVE REEL", command=self._on_approve_reel_clicked, width=160,
            ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="📤 Send to Instagram Manager", command=self._on_send_to_instagram_clicked, width=210,
            state="normal" if is_reel_approved else "disabled",
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")
        if not is_reel_approved:
            ctk.CTkLabel(
                inner, text="Click APPROVE REEL to review and unlock sending this Reel to Instagram Manager.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

        # Content Package + Ready to Publish stage: "GENERATE CONTENT
        # PACKAGE" only becomes available once the Reel itself is
        # approved (module brief section 5's own invalid-transition
        # rule: "cannot generate content package before Reel approval").
        if is_reel_approved:
            package_row = ctk.CTkFrame(inner, fg_color="transparent")
            package_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
            has_package = self._current_publish_package is not None
            ctk.CTkButton(
                package_row,
                text="🔄 Regenerate Content Package" if has_package else "📦 GENERATE CONTENT PACKAGE",
                command=lambda: self._on_generate_publish_package_clicked(self._current_project),
                state="disabled" if (self._current_publish_package is not None and self._current_publish_package.publish_approved) else "normal",
                width=230,
            ).pack(side="left")

        plans_by_number = (
            {p.scene_number: p for p in self._current_visual_plan.scenes}
            if self._current_visual_plan is not None else None
        )
        self._run_and_render_quality_control(
            result.output_path, self._quality_container,
            target_duration_seconds=(
                self._current_brief.duration_seconds if self._current_brief is not None else None
            ),
            scenes=self._current_storyboard.scenes if self._current_storyboard is not None else None,
            visuals=self._current_visuals, plans_by_number=plans_by_number,
        )

    def _render_reel_preview(self, inner: ctk.CTkFrame, result) -> None:
        """Reel Preview stage's own real preview section - shown inside
        the already-existing FINAL REEL card (_render_export() above),
        not a new/duplicate card, since REEL_READY/REEL_APPROVED are
        both properties of that same already-exported Reel.

        In-app playback was investigated and deliberately NOT
        implemented (module brief: "Only implement playback if it is
        stable with the existing stack"): this codebase has no video-
        decoding dependency anywhere (confirmed - no opencv/vlc/moviepy/
        imageio in this environment, matches jarvis.video_studio
        .ffmpeg_utils's own docstring on why this codebase deliberately
        never adds one), and Tkinter itself has no audio output at all.
        Building a real-time frame-pipe decoder from raw ffmpeg output
        would be a large, untested, hand-rolled subsystem carrying
        exactly the same risk the module brief warns against for a
        "large new dependency" AND would silently drop the Reel's own
        audio/voiceover from playback - a worse outcome than not
        offering in-app playback at all. Real Play/Pause/Replay is
        instead provided by _open_video() below (opens the real file in
        the OS's own default player - genuine audio+video playback,
        real seeking, zero new dependencies) - the module brief's own
        named fallback for exactly this situation.

        What THIS method adds instead (module brief's "safest
        lightweight alternative"): the thumbnail is a REAL frame
        extracted from the REAL exported MP4 (jarvis.video_studio
        .ffmpeg_utils.extract_frame() - the exact same already-existing,
        already-tested ffmpeg-subprocess helper _export_result_from_path()
        above already reuses probe_video() from) - never a fake/
        placeholder image or a fabricated still-frame animation. The
        scene list is REAL and CLICKABLE: clicking a scene swaps the
        thumbnail to a real frame extracted from THAT scene's own real
        position in the assembled export (see
        _reel_preview_thumbnail_path()'s own docstring), and shows that
        scene's real start position over the Reel's real total duration
        with a real (not animated/fake) CTkProgressBar reflecting that
        position - a genuine, computed "where in the Reel is this"
        indicator, not a live playhead (there is no live playback to
        track, per the above).

        The scene-sequence/voiceover/audio/generation-status summary
        below reads ONLY already-tracked state
        (self._current_storyboard/._current_visuals/._current_voiceover_path/
        ._current_text_mode) - no new state of its own beyond
        self._reel_preview_selected_scene (a purely in-memory UI
        selection - see its own docstring), matching this view's own
        established "read already-tracked state, don't invent a second
        source of truth" convention (see _render_step_indicator()'s own
        docstring for the same reasoning)."""
        storyboard = self._current_storyboard
        visuals = self._current_visuals
        export_path = result.output_path

        selected_scene = self._reel_preview_selected_scene
        if selected_scene is not None and (
            storyboard is None or not any(s.number == selected_scene for s in storyboard.scenes)
        ):
            # The previously-selected scene no longer exists (e.g. a
            # REGENERATE ALL replaced the storyboard) - fall back to the
            # whole-Reel default rather than silently showing a stale
            # selection for a scene that's gone.
            selected_scene = None
            self._reel_preview_selected_scene = None

        preview_row = ctk.CTkFrame(inner, fg_color="transparent")
        preview_row.pack(fill="x", pady=(0, theme.SPACE_SM))

        thumb_path = self._reel_preview_thumbnail_path(export_path, scene_number=selected_scene)
        if thumb_path is not None and thumb_path.is_file():
            try:
                from PIL import Image

                pil_image = Image.open(thumb_path)
                pil_image.load()
                preview_height = 220
                preview_width = int(preview_height * result.width / result.height) if result.height else 124
                ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(preview_width, preview_height))
                thumb_border = ctk.CTkFrame(preview_row, fg_color=theme.BORDER_SUBTLE, corner_radius=theme.RADIUS_BUTTON)
                thumb_border.pack(side="left", padx=(0, theme.SPACE_MD))
                ctk.CTkLabel(thumb_border, image=ctk_image, text="").pack(padx=2, pady=2)
                caption = f"Scene {selected_scene}" if selected_scene is not None else "First frame"
                ctk.CTkLabel(
                    thumb_border, text=caption,
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                    text_color=theme.TEXT_MUTED,
                ).pack(pady=(0, theme.SPACE_XS))
            except Exception:
                logger.exception("Couldn't display the Reel preview thumbnail (file exists on disk: %s)", thumb_path)

        info_col = ctk.CTkFrame(preview_row, fg_color="transparent")
        info_col.pack(side="left", fill="both", expand=True)

        ctk.CTkLabel(
            info_col, text="🎬 REEL PREVIEW - REVIEW BEFORE APPROVING",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w")

        scene_count = len(storyboard.scenes) if storyboard is not None else 0
        rendered_count = sum(1 for v in visuals if v.error is None and v.image_path is not None)
        failed_count = sum(1 for v in visuals if v.error is not None)
        voiceover_status = "✅ Included" if self._current_voiceover_path is not None and self._current_voiceover_path.is_file() else "— None"
        audio_status = "✅ Overlay captions/music cues configured" if self._current_text_mode == "overlay" else "Baked-in captions (no separate overlay track)"
        if failed_count:
            generation_status = f"⚠️ {rendered_count}/{scene_count} scenes rendered - {failed_count} failed"
        elif rendered_count == scene_count and scene_count > 0:
            generation_status = f"✅ All {scene_count} scenes rendered"
        else:
            generation_status = f"{rendered_count}/{scene_count} scenes rendered"

        total_duration = max(0.0, result.duration_seconds)

        for line in (
            f"Duration: {_format_mmss(total_duration)} ({total_duration:.1f}s)  ·  Scenes: {scene_count}",
            f"Voiceover: {voiceover_status}",
            f"Audio: {audio_status}",
            f"Generation status: {generation_status}",
        ):
            ctk.CTkLabel(
                info_col, text=line,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        # Current position / total duration + a real (non-animated)
        # timeline indicator for whichever scene is currently selected -
        # module brief's own "current time / total duration" and
        # "simple progress indicator or timeline if safely possible",
        # implemented honestly as a real computed position (this
        # scene's own real start_seconds in the real assembled export),
        # never a fake/simulated live playhead.
        position_seconds = 0.0
        if selected_scene is not None and storyboard is not None:
            scene = next((s for s in storyboard.scenes if s.number == selected_scene), None)
            if scene is not None:
                position_seconds = min(scene.start_seconds, total_duration)
        position_row = ctk.CTkFrame(info_col, fg_color="transparent")
        position_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkLabel(
            position_row,
            text=f"{_format_mmss(position_seconds)} / {_format_mmss(total_duration)}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        progress = ctk.CTkProgressBar(position_row, width=260, height=6)
        progress.pack(anchor="w", pady=(theme.SPACE_XS, 0))
        progress.set(position_seconds / total_duration if total_duration > 0 else 0.0)

        if storyboard is not None and storyboard.scenes:
            ctk.CTkLabel(
                info_col, text="Scene sequence (click a scene to preview its position):",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", pady=(theme.SPACE_SM, theme.SPACE_XS))
            scene_row = ctk.CTkFrame(info_col, fg_color="transparent")
            scene_row.pack(anchor="w", fill="x")
            for scene in storyboard.scenes:
                is_selected = scene.number == selected_scene
                ctk.CTkButton(
                    scene_row, text=f"#{scene.number} {_format_mmss(scene.start_seconds)}",
                    width=90, height=26,
                    command=lambda s=scene.number: self._on_reel_preview_scene_selected(s),
                    fg_color=theme.ACCENT_PRIMARY if is_selected else theme.BG_CARD,
                    hover_color=theme.ACCENT_PRIMARY_HOVER if is_selected else theme.BG_CARD_HOVER,
                    border_width=1, border_color=theme.BORDER_SUBTLE,
                ).pack(side="left", padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS))

        stale_scene = self._first_scene_visual_newer_than(export_path)
        if stale_scene is not None:
            ctk.CTkLabel(
                info_col,
                text=(
                    f"⚠️ Scene {stale_scene} was regenerated after this Reel was assembled - "
                    "click REGENERATE REEL to include the update."
                ),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.DANGER, anchor="w", wraplength=480, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def _on_reel_preview_scene_selected(self, scene_number: int) -> None:
        """Reel Preview stage's own "clickable scene selection" -
        clicking a scene entry re-renders the FINAL REEL card with that
        scene's own real thumbnail/position selected (toggles back to
        the whole-Reel default on a second click of the same scene).
        Does NOT touch the storyboard, scene visuals, or export in any
        way - purely swaps which already-real frame this preview shows;
        the existing per-scene EDIT SCENE/REGENERATE buttons in the
        storyboard cards above remain the one place scene content is
        actually changed, matching the module brief's own "reuse
        existing... do not duplicate existing functionality" rule."""
        project = self._current_project
        if project is None:
            return
        record = db.get_project(project.project_id)
        if record is None or record.export_path is None:
            return
        self._reel_preview_selected_scene = None if scene_number == self._reel_preview_selected_scene else scene_number
        self._render_export(_export_result_from_path(Path(record.export_path)))

    def _reel_preview_thumbnail_path(self, export_path: Path, *, scene_number: int | None = None) -> Path | None:
        """Extracts (or reuses an already-extracted) real frame from the
        real exported MP4, as a genuine visual preview - never a fake
        placeholder. With no `scene_number`, extracts the exported
        video's own real FIRST frame (timestamp 0.0) - cover-in-export
        bug fix requirement 8: this is what actually proves whether the
        selected cover made it into the export when "Use cover as" is
        Intro/Both (a midpoint frame, the previous default here, would
        never show the cover at all, since it's only ever the first
        1-2 seconds - see export_reel_video()'s own docstring). For an
        Instagram-Cover-only project (no intro spliced in) this is
        simply that project's own real first scene frame instead, which
        is just as genuine a "what does this Reel actually open with"
        preview. With `scene_number`, extracts a frame from THAT scene's own real
        position in the assembled export timeline instead - scene.start_
        seconds/end_seconds are already timed against the ASSEMBLED
        (concatenated) export timeline, not the scene's own original
        position (see export_reel_video()'s own docstring), so this is
        a real, accurate seek into the real file, not a guess.

        Cached alongside the export file itself (same directory,
        derived filename - one per scene, plus the whole-Reel default)
        so reopening the same project / re-selecting the same scene
        doesn't re-run ffmpeg every render pass; a NEW export (a
        different output_path, since exports are always freshly
        timestamped - see save_export_path()'s own docstring) naturally
        gets its own new thumbnails rather than showing stale ones.
        Returns None (never raises) on any failure - a missing preview
        image is a cosmetic gap, not a reason to break the whole FINAL
        REEL card."""
        try:
            from jarvis.video_studio.ffmpeg_utils import FFmpegError, extract_frame

            suffix = f"_scene{scene_number:02d}" if scene_number is not None else "_preview"
            thumb_path = export_path.with_name(f"{export_path.stem}{suffix}.jpg")
            if thumb_path.is_file():
                return thumb_path

            if scene_number is not None and self._current_storyboard is not None:
                scene = next((s for s in self._current_storyboard.scenes if s.number == scene_number), None)
                if scene is not None:
                    timestamp = max(0.0, (scene.start_seconds + scene.end_seconds) / 2)
                else:
                    timestamp = None
            else:
                timestamp = None

            if timestamp is None:
                timestamp = 0.0

            extract_frame(export_path, timestamp_seconds=timestamp, output_path=thumb_path)
            return thumb_path if thumb_path.is_file() else None
        except FFmpegError:
            return None
        except Exception:
            logger.exception("Couldn't extract a Reel preview thumbnail from %s", export_path)
            return None

    def _first_scene_visual_newer_than(self, export_path: Path) -> int | None:
        """Real, computed staleness check (not a guess): if any current
        scene's own rendered visual file was modified AFTER the export
        file itself, the assembled Reel no longer reflects that scene's
        latest regeneration - module brief's own "update the assembled
        Reel after the new scene is ready" implies the person must be
        told when it HASN'T been updated yet. Returns that scene's
        number, or None if the export is current with every scene (or
        either file can't be stat'd, in which case this check simply
        stays silent rather than risk a false warning)."""
        if not export_path.is_file():
            return None
        try:
            export_mtime = export_path.stat().st_mtime
        except OSError:
            return None
        for visual in self._current_visuals:
            if visual.image_path is None:
                continue
            try:
                if visual.image_path.stat().st_mtime > export_mtime:
                    return visual.scene_number
            except OSError:
                continue
        return None

    # --- Content Package + Ready to Publish stage ------------------------------------------

    def _on_generate_publish_package_clicked(self, project: ReelProject) -> None:
        """GENERATE CONTENT PACKAGE (module brief section 5:
        REEL_APPROVED -> GENERATING_CONTENT_PACKAGE). Requires the Reel
        to be genuinely REEL_APPROVED first (module brief section 5's
        own invalid-transition rule: "cannot generate content package
        before Reel approval") - re-checked against the real, persisted
        record rather than trusting only the button's own visibility,
        same defense-in-depth convention as _on_send_to_instagram_
        clicked()'s own reel_approved re-check."""
        try:
            self._do_generate_publish_package(project)
        except Exception as e:
            logger.exception("GENERATE CONTENT PACKAGE click handler raised an unexpected exception")
            self._set_status(f"Couldn't start generating the content package: {e}", kind="error")

    def _do_generate_publish_package(self, project: ReelProject) -> None:
        llm = self._llm
        brief = self._current_brief
        script = self._current_script
        if llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        if brief is None or script is None:
            self._set_status(
                "No Reel brief/script is loaded yet - create or open a Reel first.", kind="error",
            )
            return
        record = db.get_project(project.project_id)
        if record is None or not record.reel_approved:
            self._set_status(
                "Approve the Reel first (APPROVE REEL) before generating its content package.", kind="error",
            )
            return
        self._set_status("Preparing the content package...", kind="loading")
        self._is_generating_publish_package = True
        self._render_step_indicator()
        existing = self._current_publish_package
        cover_path = record.cover_path
        run_generation_in_background(
            lambda: _create_publish_package(llm, project, brief, script, existing, cover_path),
            self._result_queue, source=("publish_package", self),
        )

    def _handle_publish_package_result(self, result: GenerationTaskResult) -> None:
        self._is_generating_publish_package = False
        if result.error:
            self._set_status(f"Content package generation failed: {result.error}", kind="error")
            self._render_step_indicator()
            return
        project, package, caption_package = result.value
        self._current_publish_package = package
        self._current_caption_package = caption_package
        self._clear_status()
        record = db.get_project(project.project_id)
        if record is not None:
            self._render_publish_package(project, record)
        self._refresh_recent_reels()
        self._render_step_indicator()

    def _render_publish_package(self, project: ReelProject, record: db.ProjectRecord) -> None:
        """The dashboard's own "CONTENT PACKAGE" section (module brief
        section 2) - one card per required piece (🎬 REEL/🖼️ COVER/
        ✍️ CAPTION/#️⃣ HASHTAGS/🎵 AUDIO/⏰ PUBLISHING TIME), each with
        Preview/Edit/Regenerate/Status where applicable, followed by the
        READY TO PUBLISH summary + APPROVE FOR PUBLISHING action (module
        brief sections 3-4). Reads ONLY already-tracked/persisted state
        (self._current_publish_package, the same ProjectRecord every
        other _render_*() method here already reads from) - no new
        source of truth."""
        self._clear_container(self._publish_package_container)
        package = self._current_publish_package
        if package is None:
            return

        card = Card(self._publish_package_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="📦 CONTENT PACKAGE",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SUBTITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        is_locked = package.publish_approved

        self._render_publish_package_reel_section(inner, record)
        self._render_publish_package_cover_section(inner, project, package, record, is_locked)
        self._render_publish_package_caption_section(inner, project, package, is_locked)
        self._render_publish_package_hashtags_section(inner, project, package, is_locked)
        self._render_publish_package_audio_section(inner, record)
        self._render_publish_package_posting_time_section(inner, project, package, is_locked)
        self._render_publish_package_ready_to_publish(inner, project, record, package, is_locked)

    def _publish_package_section_card(self, parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
        section = ctk.CTkFrame(parent, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
        section.pack(fill="x", pady=(0, theme.SPACE_SM))
        section_inner = ctk.CTkFrame(section, fg_color="transparent")
        section_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)
        ctk.CTkLabel(
            section_inner, text=title,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        return section_inner

    def _render_publish_package_reel_section(self, parent: ctk.CTkFrame, record: db.ProjectRecord) -> None:
        """🎬 REEL (module brief section 1A): final Reel file/path, 9:16
        format, duration, resolution, file status - re-probes the real
        export file (same _export_result_from_path() every other REEL
        display already reuses) rather than caching stale dimensions."""
        section = self._publish_package_section_card(parent, "🎬 REEL")
        if record.export_path is None or not Path(record.export_path).is_file():
            ctk.CTkLabel(
                section, text="⚠️ No Reel export found yet.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w",
            ).pack(anchor="w")
            return
        result = _export_result_from_path(Path(record.export_path))
        ctk.CTkLabel(
            section,
            text=(
                f"✅ {result.width}×{result.height}  ·  {result.duration_seconds:.1f}s  ·  "
                f"{format_file_size(result.file_size_bytes)}"
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w")

    def _render_publish_package_cover_section(
        self, parent: ctk.CTkFrame, project: ReelProject, package: publish_package_mod.PublishPackage,
        record: db.ProjectRecord, is_locked: bool,
    ) -> None:
        """🖼️ COVER (module brief section 1B): preview + regenerate +
        "select another frame from the Reel as cover" (this codebase's
        own real, already-existing Reel Preview thumbnail extraction -
        _reel_preview_thumbnail_path() - reused directly, never a second
        frame-extraction implementation)."""
        section = self._publish_package_section_card(parent, "🖼️ COVER")
        row = ctk.CTkFrame(section, fg_color="transparent")
        row.pack(fill="x")
        if package.cover_path is not None and Path(package.cover_path).is_file():
            try:
                from PIL import Image

                pil_image = Image.open(package.cover_path)
                ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(101, 180))
                ctk.CTkLabel(row, image=ctk_image, text="").pack(side="left", padx=(0, theme.SPACE_SM))
            except Exception:
                logger.exception("Couldn't display the content package cover (file exists on disk: %s)", package.cover_path)
        else:
            ctk.CTkLabel(
                row, text="⚠️ No cover selected yet.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w",
            ).pack(side="left", padx=(0, theme.SPACE_SM))
        button_col = ctk.CTkFrame(row, fg_color="transparent")
        button_col.pack(side="left")
        source_label = "AI-generated cover" if package.cover_source == publish_package_mod.COVER_SOURCE_GENERATED else f"Reel scene {package.cover_scene_number}"
        ctk.CTkLabel(
            button_col, text=f"Source: {source_label}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_col, text="🔄 Regenerate Cover", command=self._on_generate_cover_clicked,
            state="disabled" if is_locked else "normal",
            width=160, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_col, text="🎬 Choose Reel Frame", command=lambda: self._do_open_cover_frame_dialog(project),
            state="disabled" if is_locked else "normal",
            width=160, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w")

    def _do_open_cover_frame_dialog(self, project: ReelProject) -> None:
        """Module brief section 1B: "ability to select another frame
        from the Reel as cover if supported" - lists every scene, each
        extracting (or reusing an already-cached) real frame from the
        REAL exported MP4 at that scene's own real position, exactly the
        same jarvis.gui.views.reel_generator.dashboard._reel_preview_
        thumbnail_path() helper the Reel Preview stage already uses -
        never a new frame-extraction implementation."""
        storyboard = self._current_storyboard
        record = db.get_project(project.project_id)
        if storyboard is None or record is None or record.export_path is None or not Path(record.export_path).is_file():
            self._set_status("No Reel export is available yet to pick a frame from.", kind="error")
            return
        export_path = Path(record.export_path)

        dialog = ctk.CTkToplevel(self)
        dialog.title("Choose a Reel Frame as Cover")
        dialog.geometry("320x420")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="Pick a scene's frame as the cover:", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        for scene in storyboard.scenes:
            def on_pick(s=scene.number) -> None:
                dialog.destroy()
                self._run_select_cover_frame(project, export_path, s)
            ctk.CTkButton(
                inner, text=f"Scene {scene.number} ({scene.on_screen_text[:30]})", command=on_pick, height=28,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(fill="x", pady=(0, theme.SPACE_XS))
        ctk.CTkButton(inner, text="Cancel", command=dialog.destroy, height=28).pack(fill="x", pady=(theme.SPACE_SM, 0))

    def _run_select_cover_frame(self, project: ReelProject, export_path: Path, scene_number: int) -> None:
        thumb_path = self._reel_preview_thumbnail_path(export_path, scene_number=scene_number)
        if thumb_path is None:
            self._set_status(f"Couldn't extract scene {scene_number}'s frame.", kind="error")
            return
        package = self._current_publish_package or publish_package_mod.PublishPackage()
        package = publish_package_mod.select_cover(
            package, cover_path=str(thumb_path), source=publish_package_mod.COVER_SOURCE_REEL_FRAME,
            scene_number=scene_number,
        )
        self._save_and_render_publish_package(project, package)
        self._set_status(f"Cover set to scene {scene_number}'s frame.", kind="success")

    def _render_publish_package_caption_section(
        self, parent: ctk.CTkFrame, project: ReelProject, package: publish_package_mod.PublishPackage, is_locked: bool,
    ) -> None:
        """✍️ CAPTION (module brief section 1C): editable text, clearly
        labeled GENERATED vs USER EDITED (module brief section 6)."""
        section = self._publish_package_section_card(parent, "✍️ CAPTION")
        state_label = "✏️ USER EDITED" if package.caption_edited else "🤖 GENERATED"
        ctk.CTkLabel(
            section, text=state_label,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        if package.caption_text:
            ctk.CTkLabel(
                section, text=package.caption_text,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=600, justify="left",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        else:
            ctk.CTkLabel(
                section, text="⚠️ No caption yet.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        button_row = ctk.CTkFrame(section, fg_color="transparent")
        button_row.pack(anchor="w")
        ctk.CTkButton(
            button_row, text="✏️ Edit Caption", command=lambda: self._do_open_edit_caption_dialog(project),
            state="disabled" if is_locked else "normal",
            width=140, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="🔄 Regenerate", command=lambda: self._on_generate_publish_package_clicked(project),
            state="disabled" if is_locked else "normal",
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _do_open_edit_caption_dialog(self, project: ReelProject) -> None:
        package = self._current_publish_package or publish_package_mod.PublishPackage()
        dialog = ctk.CTkToplevel(self)
        dialog.title("Edit Caption")
        dialog.geometry("480x320")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="CAPTION", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w")
        text_box = ctk.CTkTextbox(inner, height=180, wrap="word")
        text_box.pack(fill="both", expand=True, pady=(theme.SPACE_XS, theme.SPACE_SM))
        text_box.insert("1.0", package.caption_text)

        def on_save() -> None:
            new_text = text_box.get("1.0", "end").strip()
            updated = publish_package_mod.edit_caption(package, new_text)
            dialog.destroy()
            self._save_and_render_publish_package(project, updated)
            self._set_status("Caption updated.", kind="success")

        ctk.CTkButton(inner, text="Save", command=on_save).pack(fill="x")

    def _render_publish_package_hashtags_section(
        self, parent: ctk.CTkFrame, project: ReelProject, package: publish_package_mod.PublishPackage, is_locked: bool,
    ) -> None:
        """#️⃣ HASHTAGS (module brief section 1D): editable text,
        GENERATED vs USER EDITED, regenerate."""
        section = self._publish_package_section_card(parent, "#️⃣ HASHTAGS")
        state_label = "✏️ USER EDITED" if package.hashtags_edited else "🤖 GENERATED"
        ctk.CTkLabel(
            section, text=state_label,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkLabel(
            section, text=package.hashtags_text or "⚠️ No hashtags yet.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_PRIMARY if package.hashtags_text else theme.DANGER,
            anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        button_row = ctk.CTkFrame(section, fg_color="transparent")
        button_row.pack(anchor="w")
        ctk.CTkButton(
            button_row, text="✏️ Edit Hashtags", command=lambda: self._do_open_edit_hashtags_dialog(project),
            state="disabled" if is_locked else "normal",
            width=140, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="🔄 Regenerate", command=lambda: self._on_generate_publish_package_clicked(project),
            state="disabled" if is_locked else "normal",
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _do_open_edit_hashtags_dialog(self, project: ReelProject) -> None:
        package = self._current_publish_package or publish_package_mod.PublishPackage()
        dialog = ctk.CTkToplevel(self)
        dialog.title("Edit Hashtags")
        dialog.geometry("480x220")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="HASHTAGS", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w")
        text_box = ctk.CTkTextbox(inner, height=90, wrap="word")
        text_box.pack(fill="both", expand=True, pady=(theme.SPACE_XS, theme.SPACE_SM))
        text_box.insert("1.0", package.hashtags_text)

        def on_save() -> None:
            new_text = text_box.get("1.0", "end").strip()
            updated = publish_package_mod.edit_hashtags(package, new_text)
            dialog.destroy()
            self._save_and_render_publish_package(project, updated)
            self._set_status("Hashtags updated.", kind="success")

        ctk.CTkButton(inner, text="Save", command=on_save).pack(fill="x")

    def _render_publish_package_audio_section(self, parent: ctk.CTkFrame, record: db.ProjectRecord) -> None:
        """🎵 AUDIO (module brief section 1E): whether the Reel contains
        audio, whether a voiceover exists - read-only status, never
        auto-downloads or attaches any music (module brief's own hard
        rule)."""
        section = self._publish_package_section_card(parent, "🎵 AUDIO")
        has_voiceover = record.voiceover_path is not None and Path(record.voiceover_path).is_file()
        export_has_audio = None
        if record.export_path is not None and Path(record.export_path).is_file():
            try:
                from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

                export_has_audio = probe_video(Path(record.export_path)).has_audio
            except FFmpegError:
                export_has_audio = None
            except Exception:
                logger.exception("Couldn't probe the Reel export for audio info")
        for line in (
            f"Voiceover: {'✅ Included' if has_voiceover else '— None'}",
            (
                "Reel audio track: ✅ Present" if export_has_audio
                else ("Reel audio track: — None" if export_has_audio is False else "Reel audio track: unknown")
            ),
            "Music: no copyrighted music is attached automatically.",
        ):
            ctk.CTkLabel(
                section, text=line,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")

    def _render_publish_package_posting_time_section(
        self, parent: ctk.CTkFrame, project: ReelProject, package: publish_package_mod.PublishPackage, is_locked: bool,
    ) -> None:
        """⏰ PUBLISHING TIME (module brief sections 1F/2): a
        RECOMMENDATION only - never triggers any scheduling/publishing
        by itself."""
        section = self._publish_package_section_card(parent, "⏰ PUBLISHING TIME")
        time_text = package.suggested_posting_time or "No suggestion available yet."
        tz_text = f" ({package.posting_timezone})" if package.posting_timezone else ""
        ctk.CTkLabel(
            section, text=f"{time_text}{tz_text}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkLabel(
            section, text="Recommendation only - publishing is never scheduled automatically.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkButton(
            section, text="✏️ Edit Posting Time", command=lambda: self._do_open_edit_posting_time_dialog(project),
            state="disabled" if is_locked else "normal",
            width=160, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w")

    def _do_open_edit_posting_time_dialog(self, project: ReelProject) -> None:
        package = self._current_publish_package or publish_package_mod.PublishPackage()
        dialog = ctk.CTkToplevel(self)
        dialog.title("Edit Suggested Publishing Time")
        dialog.geometry("400x220")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="SUGGESTED POSTING TIME", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w")
        time_entry = ctk.CTkEntry(inner)
        time_entry.pack(fill="x", pady=(theme.SPACE_XS, theme.SPACE_SM))
        time_entry.insert(0, package.suggested_posting_time or "")
        ctk.CTkLabel(
            inner, text="TIMEZONE (optional)", anchor="w",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.ACCENT_PRIMARY,
        ).pack(anchor="w")
        tz_entry = ctk.CTkEntry(inner)
        tz_entry.pack(fill="x", pady=(theme.SPACE_XS, theme.SPACE_SM))
        tz_entry.insert(0, package.posting_timezone)

        def on_save() -> None:
            updated = publish_package_mod.edit_posting_time(
                package, posting_time=time_entry.get().strip(), timezone=tz_entry.get().strip(),
            )
            dialog.destroy()
            self._save_and_render_publish_package(project, updated)
            self._set_status("Suggested publishing time updated.", kind="success")

        ctk.CTkButton(inner, text="Save", command=on_save).pack(fill="x")

    def _render_publish_package_ready_to_publish(
        self, parent: ctk.CTkFrame, project: ReelProject, record: db.ProjectRecord,
        package: publish_package_mod.PublishPackage, is_locked: bool,
    ) -> None:
        """READY TO PUBLISH summary + APPROVE FOR PUBLISHING (module
        brief sections 3-4). The button itself is only ENABLED when the
        package is genuinely ready (module brief section 5's own
        invalid-transition rules: "cannot approve publishing if...
        required caption/content metadata is missing"/"...if the Reel is
        missing") - re-validated again inside _on_approve_for_publishing_
        clicked() itself before ever writing PUBLISH_APPROVED, matching
        this view's own established defense-in-depth convention."""
        has_export = record.export_path is not None and Path(record.export_path).is_file()
        can_approve = package.is_ready and has_export and not is_locked

        section = ctk.CTkFrame(parent, fg_color="transparent")
        section.pack(fill="x", pady=(theme.SPACE_SM, 0))
        if is_locked:
            ctk.CTkLabel(
                section, text="✅ PUBLISH APPROVED - READY FOR INSTAGRAM PUBLISHING",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.SUCCESS, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        elif package.is_ready and has_export:
            ctk.CTkLabel(
                section, text="✅ READY TO PUBLISH - review everything above, then approve.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.ACCENT_PRIMARY, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        else:
            missing = []
            if not has_export:
                missing.append("Reel export")
            if not package.caption_text.strip():
                missing.append("caption")
            if package.cover_path is None:
                missing.append("cover")
            ctk.CTkLabel(
                section, text=f"⚠️ Not ready yet - missing: {', '.join(missing) if missing else 'content'}.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))

        ctk.CTkButton(
            section, text="✅ APPROVE FOR PUBLISHING",
            command=lambda: self._on_approve_for_publishing_clicked(project),
            state="disabled" if not can_approve else "normal", width=220,
        ).pack(anchor="w")

    def _on_approve_for_publishing_clicked(self, project: ReelProject) -> None:
        """APPROVE FOR PUBLISHING (module brief section 4) - moves the
        package's own status to PUBLISH_APPROVED. Does NOT call any
        Instagram API, does NOT send anything to Instagram AI Manager,
        and does NOT touch jarvis.reel_generator.instagram_handoff in
        any way - module brief's own hard rule ("It must NOT call
        Instagram APIs yet")."""
        record = db.get_project(project.project_id)
        if record is None:
            self._set_status("This Reel could no longer be found.", kind="error")
            return
        package = self._current_publish_package
        has_export = record.export_path is not None and Path(record.export_path).is_file()
        if package is None or not package.is_ready:
            self._set_status(
                "Generate a complete content package first - caption and cover are both required.",
                kind="error",
            )
            return
        if not has_export:
            self._set_status("No Reel export exists - cannot approve for publishing.", kind="error")
            return
        updated = publish_package_mod.approve_for_publishing(package, now_iso=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self._save_and_render_publish_package(project, updated)
        self._set_status(
            "Publish approved - ready for a future Instagram publishing stage. Nothing was sent or published.",
            kind="success",
        )

    def _save_and_render_publish_package(self, project: ReelProject, package: publish_package_mod.PublishPackage) -> None:
        db.save_publish_package(project.project_id, dataclasses.asdict(package))
        self._current_publish_package = package
        record = db.get_project(project.project_id)
        if record is not None:
            self._render_publish_package(project, record)
        self._refresh_recent_reels()
        self._render_step_indicator()

    def _run_and_render_quality_control(
        self, export_path: Path, container: ctk.CTkFrame, *, target_duration_seconds: float | None,
        scenes: tuple[Scene, ...] | None = None, visuals: list[SceneVisual] | None = None,
        plans_by_number: dict[int, ScenePlan] | None = None,
    ) -> None:
        """`scenes`/`visuals`/`plans_by_number` are only ever passed by
        Mode B (idea-based, storyboard-driven) call sites - Mode A
        (from-my-footage) has no Storyboard/ScenePlan of this shape, so
        its own call site leaves these at their None default, exactly
        as before this parameter existed - check_scene_visuals()/
        check_no_hashtags_in_scene_text() are simply skipped in that
        case (run_quality_control()'s own "only run checks whose inputs
        are available" convention), not a regression for Mode A."""
        cover_path_str = self._current_project_cover_path()
        record = db.get_project(self._current_project.project_id) if self._current_project is not None else None
        cover_integration_mode = record.cover_integration_mode if record is not None else None
        report = run_quality_control(
            export_path=export_path, target_duration_seconds=target_duration_seconds,
            scenes=scenes, visuals=visuals, plans_by_number=plans_by_number,
            check_cover_and_caption_presence=True,
            cover_path=Path(cover_path_str) if cover_path_str else None,
            caption_text=self._current_project_caption_text(),
            cover_integration_mode=cover_integration_mode,
        )
        self._render_quality_report(report, container)

    def _current_project_cover_path(self) -> str | None:
        if self._current_project is None:
            return None
        record = db.get_project(self._current_project.project_id)
        return record.cover_path if record is not None else None

    def _current_project_caption_text(self) -> str | None:
        if self._current_caption_package is not None and self._current_caption_package.caption is not None:
            return self._current_caption_package.caption.get("medium_caption")
        return None

    def _render_quality_report(self, report: QualityReport, container: ctk.CTkFrame) -> None:
        self._clear_container(container)
        if not report.issues:
            return
        card = Card(container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="✅ QUALITY CHECK" if report.passed else "⚠️ QUALITY CHECK",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.SUCCESS if report.passed else theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        color_by_severity = {"fail": theme.DANGER, "warning": theme.ACCENT_PRIMARY, "info": theme.TEXT_MUTED}
        for issue in report.issues:
            if issue.severity == "info":
                continue  # informational notes aren't shown by default - only fail/warning need attention
            text = issue.message
            if issue.suggested_action:
                text += f" → {issue.suggested_action}"
            ctk.CTkLabel(
                inner, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=color_by_severity[issue.severity], anchor="w", wraplength=520, justify="left",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        if report.passed:
            ctk.CTkLabel(
                inner, text="No issues found in the checks this codebase can perform.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w")

    # --- Content Package -------------------------------------------------------------------

    def _on_create_content_package_clicked(self) -> None:
        project = self._current_project
        brief = self._current_brief
        if project is None or brief is None:
            self._set_status(
                "No Reel brief is loaded yet - create or open a Reel first.", kind="error",
            )
            return
        headline = self._current_storyboard.scenes[0].on_screen_text if self._current_storyboard is not None and self._current_storyboard.scenes else brief.topic
        self._set_status("Creating your content package...", kind="loading")
        style = brief.style
        run_generation_in_background(
            lambda: _create_content_package(project, headline, brief.cta, style),
            self._result_queue, source=("content_package", self),
        )

    def _render_content_package(self, package: content_package_mod.ContentPackage) -> None:
        self._clear_container(self._content_package_container)
        card = Card(self._content_package_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="✨ CONTENT PACKAGE READY" if package.all_succeeded else "✨ CONTENT PACKAGE (partial)",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x")
        for piece in package.pieces:
            piece_frame = ctk.CTkFrame(row, fg_color="transparent")
            piece_frame.pack(side="left", padx=(0, theme.SPACE_SM))
            if piece.render_result is not None:
                try:
                    from PIL import Image

                    pil_image = Image.open(piece.render_result.output_path)
                    preview_height = 120
                    preview_width = int(preview_height * piece.render_result.width / piece.render_result.height)
                    ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(preview_width, preview_height))
                    ctk.CTkLabel(piece_frame, image=ctk_image, text="").pack()
                except Exception:
                    pass
            ctk.CTkLabel(
                piece_frame, text=piece.label if piece.error is None else f"{piece.label} (failed)",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED if piece.error is None else theme.DANGER, anchor="w",
            ).pack()

    # --- REEL_READY -> REEL_APPROVED (Reel Generation Workflow stage) ----------------------

    def _reel_ready_validation_error(self, project: ReelProject) -> str | None:
        """Reel Preview + Approve Reel stage's own safety check (module
        brief section 8: "Do not allow APPROVE REEL when: Reel
        generation is still running; required scenes are missing; Reel
        assembly failed; required assets are missing"). Returns a
        human-readable error string if the Reel is NOT genuinely in a
        REEL_READY state, or None if it's safe to approve - checked
        BEFORE db.approve_reel() ever runs, so an invalid state is never
        silently persisted as approved.

        Deliberately re-reads the real, persisted record (not just this
        view's own in-memory state) since a person could reopen a
        project and click APPROVE REEL without ever re-running
        GENERATE SCENE VISUALS in this same session - self._current_
        visuals may be empty/stale in that case, but the record's own
        export_path (a real file on disk) is still the true signal."""
        if self._is_generating_reel:
            return "The Reel is still being generated - wait for it to finish before approving."
        if self._is_generating:
            return "Scene visuals are still being generated - wait for them to finish before approving."
        record = db.get_project(project.project_id)
        if record is None:
            return "This Reel project could no longer be found."
        if record.storyboard_data is None:
            return "No storyboard exists yet for this Reel - nothing to approve."
        if record.export_path is None or not Path(record.export_path).is_file():
            return "No Reel has been exported yet - click GENERATE REEL / Export Final Reel first."
        failed = [v for v in self._current_visuals if v.error is not None]
        if failed:
            return f"{len(failed)} scene(s) failed to render - fix them and regenerate the Reel before approving."
        # Quality Control stage's own hard gate (module brief section 20:
        # "Before final export check... If something fails, show the
        # problem and offer: [Fix automatically] [Edit manually]" -
        # applied here as "before APPROVE REEL", the actual final
        # confirmation step, not just an informational card shown after
        # export that a person could ignore). Only a `fail`-severity
        # issue blocks approval - a `warning` (e.g. "no caption yet") or
        # `info` note is shown in the Quality Control card but never
        # prevents approving a Reel that's otherwise genuinely ready,
        # matching run_quality_control()'s own severity model. Real
        # measurable checks only (resolution/duration/black frames/
        # hashtags-in-video/etc.) - never a subjective judgment call.
        #
        # cover_integration_mode (cover-in-export bug fix, requirement 7):
        # included here too, not just the informational card above, so a
        # Reel with "Use cover as: Intro/Both" set but whose exported
        # video does NOT actually contain the cover (e.g. an export made
        # before the setting was changed, or a genuinely failed splice)
        # is a real `fail` that blocks APPROVE REEL, not just a note
        # someone could scroll past.
        export_path = Path(record.export_path)
        plans_by_number = (
            {p.scene_number: p for p in self._current_visual_plan.scenes}
            if self._current_visual_plan is not None else None
        )
        report = run_quality_control(
            export_path=export_path,
            target_duration_seconds=self._current_brief.duration_seconds if self._current_brief is not None else None,
            scenes=self._current_storyboard.scenes if self._current_storyboard is not None else None,
            visuals=self._current_visuals, plans_by_number=plans_by_number,
            cover_path=Path(record.cover_path) if record.cover_path else None,
            cover_integration_mode=record.cover_integration_mode,
        )
        failures = [i for i in report.issues if i.severity == "fail"]
        if failures:
            first = failures[0]
            more = f" (+{len(failures) - 1} more issue(s) - see the Quality Control card above)" if len(failures) > 1 else ""
            action = f" {first.suggested_action}" if first.suggested_action else ""
            return f"Quality check failed: {first.message}{action}{more}"
        return None

    def _on_approve_reel_clicked(self) -> None:
        """Locks in the REEL_APPROVED checkpoint (module brief: "APPROVE
        REEL") - does NOT itself send anything to Instagram AI Manager
        or publish anything anywhere; it only unlocks the already-
        existing "Send to Instagram Manager" button, a separate,
        explicit click that itself only ever creates DRAFT content
        there (see jarvis.reel_generator.instagram_handoff's own
        docstring) - publishing remains the person's own later, manual,
        separate action, matching module brief section 5's own hard
        rule. Requires the Reel to be genuinely REEL_READY first (module
        brief section 8) - see _reel_ready_validation_error() above."""
        project = self._current_project
        if project is None:
            self._set_status("No Reel is loaded yet - create or open a Reel first.", kind="error")
            return
        validation_error = self._reel_ready_validation_error(project)
        if validation_error is not None:
            self._set_status(validation_error, kind="error")
            return
        try:
            db.approve_reel(project.project_id)
            record = db.get_project(project.project_id)
            if record is not None and record.export_path is not None:
                self._render_export(_export_result_from_path(Path(record.export_path)))
            self._set_status(
                "Reel approved - it's now ready for publishing. You can send it to Instagram AI Manager "
                "as a draft whenever you're ready.", kind="success",
            )
            self._refresh_recent_reels()
            self._render_step_indicator()
        except Exception as e:
            logger.exception("APPROVE REEL click handler raised an unexpected exception")
            self._set_status(f"Couldn't approve the Reel: {e}", kind="error")

    # --- Instagram AI Manager hand-off ------------------------------------------------------

    def _on_send_to_instagram_clicked(self) -> None:
        llm = self._llm
        project = self._current_project
        brief = self._current_brief
        script = self._current_script
        if llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        if project is None or brief is None or script is None:
            self._set_status(
                "No Reel brief/script is loaded yet - create or open a Reel first.", kind="error",
            )
            return
        record = db.get_project(project.project_id)
        if record is None or not record.reel_approved:
            # Defense in depth - the button itself is already disabled
            # in the GUI until reel_approved is set (see _render_export()
            # above), but this handler double-checks the real persisted
            # flag directly rather than trusting button state alone,
            # matching this module's own established "never silently
            # fail, and never trust UI state as the sole guard for an
            # approval gate" convention (see approve_storyboard's own
            # lock-checking callers for the same pattern).
            self._set_status(
                "Approve the Reel first (click APPROVE REEL) before sending it to Instagram Manager.",
                kind="error",
            )
            return
        caption_package = self._current_caption_package or caption_mod.ReelCaptionPackage(caption=None, hashtags=None)
        record = db.get_project(project.project_id)
        cover_path = record.cover_path if record is not None else None
        export_path = record.export_path if record is not None else None
        self._set_status("Sending to Instagram AI Manager...", kind="loading")
        run_generation_in_background(
            lambda: _send_idea_reel_handoff(llm, project, brief, script, caption_package, cover_path, export_path),
            self._result_queue, source=("handoff", self),
        )

    def _render_handoff_result(self, result: instagram_handoff.HandoffResult, container: ctk.CTkFrame) -> None:
        self._clear_container(container)
        card = Card(container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        if result.insufficient_data:
            ctk.CTkLabel(
                inner, text=f"📤 Instagram hand-off: {result.message}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w", wraplength=520, justify="left",
            ).pack(anchor="w")
            return
        ctk.CTkLabel(
            inner, text="📤 Sent to Instagram AI Manager",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.SUCCESS, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        sent_parts = []
        if result.hook_set_id is not None:
            sent_parts.append("hooks")
        if result.caption_id is not None:
            sent_parts.append("caption")
        if result.cta_set_id is not None:
            sent_parts.append("CTAs")
        if result.hashtag_set_id is not None:
            sent_parts.append("hashtags")
        ctk.CTkLabel(
            inner, text=f"Sent: {', '.join(sent_parts) if sent_parts else 'nothing'}. Review and publish it yourself in Instagram AI Manager - nothing is posted automatically.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=520, justify="left",
        ).pack(anchor="w")
        if result.suggested_posting_time:
            ctk.CTkLabel(
                inner, text=f"Suggested posting time: {result.suggested_posting_time}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def _open_folder(self, path: Path) -> None:
        import os

        try:
            os.startfile(path.parent)  # noqa: S606 - opening a folder the person's own export was just saved into, not an arbitrary/untrusted path
        except OSError as e:
            self._set_status(f"Couldn't open the folder: {e}", kind="error")

    def _open_video(self, path: Path) -> None:
        """Reel Preview stage's own "Play / Pause... allow replay from
        the beginning" requirement - rather than embedding a real video
        decoder inside this CustomTkinter window (which would require
        adding a brand-new heavy dependency - opencv-python/python-vlc/
        moviepy - none of which this codebase uses anywhere; see
        jarvis.video_studio.ffmpeg_utils's own docstring for why this
        codebase deliberately keeps its Python dependency footprint to
        "FFmpeg on PATH" rather than a compiled video-decoding
        extension), this opens the REAL, just-exported MP4 in the
        person's own default OS video player - real Play/Pause/Replay/
        scrubbing via that player's own native controls, not a fake/
        static placeholder. Same os.startfile() pattern _open_folder()
        above already establishes, just on the file itself rather than
        its parent folder."""
        import os

        if not path.is_file():
            self._set_status("This Reel's video file could not be found on disk.", kind="error")
            return
        try:
            os.startfile(path)  # noqa: S606 - opening the person's own just-exported Reel file, not an arbitrary/untrusted path
        except OSError as e:
            self._set_status(f"Couldn't open the Reel for playback: {e}", kind="error")

    # --- queue polling -------------------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                result = self._result_queue.get_nowait()
                self._handle_result(result)
        except queue.Empty:
            pass
        self.after(_QUEUE_POLL_INTERVAL_MS, self._poll_queue)

    def _handle_result(self, result: Any) -> None:
        if not isinstance(result, GenerationTaskResult) or not isinstance(result.source, tuple):
            return
        kind, owner = result.source
        if owner is not self:
            return
        if kind == "create":
            self._handle_create_result(result)
        elif kind == "regenerate_script":
            self._handle_regenerate_script_result(result)
        elif kind == "storyboard":
            self._handle_storyboard_result(result)
        elif kind == "visual_plan":
            self._handle_visual_plan_result(result)
        elif kind == "visuals":
            self._handle_visuals_result(result)
        elif kind == "motion":
            self._handle_motion_result(result)
        elif kind == "regenerate_motion":
            self._handle_regenerate_motion_result(result)
        elif kind == "regenerate_scene":
            self._handle_regenerate_scene_result(result)
        elif kind == "change_camera":
            self._handle_change_camera_result(result)
        elif kind == "change_style":
            self._handle_change_style_result(result)
        elif kind == "change_visual":
            self._handle_change_visual_result(result)
        elif kind == "cover":
            self._handle_cover_result(result)
        elif kind == "cover_single":
            self._handle_single_cover_candidate_result(result)
        elif kind == "caption":
            self._handle_caption_result(result)
        elif kind == "voiceover":
            self._handle_voiceover_result(result)
        elif kind == "export":
            self._handle_export_result(result)
        elif kind == "footage":
            self._handle_footage_result(result)
        elif kind == "footage_export":
            self._handle_footage_export_result(result)
        elif kind == "content_package":
            self._handle_content_package_result(result)
        elif kind == "publish_package":
            self._handle_publish_package_result(result)
        elif kind == "handoff":
            self._handle_handoff_result(result)
        elif kind == "footage_handoff":
            self._handle_footage_handoff_result(result)
        # Re-rendered after EVERY background result this view's own
        # queue dispatches (whether it succeeded, failed, or changed
        # nothing state-relevant) - cheap (local reads only) and never
        # needs to be remembered at each individual call site the way
        # sprinkling this call across 10+ handler methods would risk
        # missing one.
        self._render_step_indicator()

    def _handle_create_result(self, result: GenerationTaskResult) -> None:
        self._create_button.configure(state="normal")
        if result.error:
            self._set_status(f"Reel concept creation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            # _create_reel_concept() returns a plain error string
            # instead of a (project, brief, script) tuple when creation
            # failed - checked BEFORE unpacking, per the same
            # unpacking-a-str-silently-"succeeds" hazard
            # jarvis.gui.views.design_studio.dashboard's own
            # _handle_create_result() documents for the identical
            # pattern.
            self._set_status(result.value, kind="error")
            return

        project, brief, script = result.value
        self._current_project = project
        self._current_brief = brief
        self._current_script = script
        self._current_storyboard = None
        self._current_visuals = []
        self._current_motion_clips = []
        self._is_generating_motion = False
        self._cover_attempt_count = 0
        self._cover_previous_titles = []
        self._current_cover_candidates = []
        self._selected_cover_candidate_attempt = None
        self._cover_candidate_next_attempt = 0
        self._cover_candidate_all_titles = []
        self._clear_status()
        self._render_brief(brief)
        self._render_script(project, script, approved=False)
        self._clear_container(self._storyboard_container)
        self._clear_container(self._cover_container)
        self._clear_container(self._caption_container)
        self._clear_container(self._export_container)
        self._refresh_recent_reels()

    def _handle_regenerate_script_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Script regeneration failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return

        project, script = result.value
        self._current_script = script
        self._current_storyboard = None
        self._current_visuals = []
        self._clear_status()
        self._render_script(project, script, approved=False)
        self._clear_container(self._storyboard_container)
        self._clear_container(self._cover_container)
        self._clear_container(self._caption_container)
        self._clear_container(self._export_container)
        self._refresh_recent_reels()

    def _handle_storyboard_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Storyboard generation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return

        project, storyboard = result.value
        self._current_storyboard = storyboard
        self._current_visual_plan = None
        self._current_visuals = []
        # Storyboard Creative Controls stage: this handler runs for BOTH
        # the very first storyboard generation (triggered from
        # _on_approve_clicked() right after script approval) AND every
        # REGENERATE ALL - either way, a genuinely NEW storyboard now
        # exists, so it becomes the new "original" RESET SCENE baseline
        # (see db.save_original_storyboard_snapshot()'s own docstring)
        # and any PREVIOUS storyboard approval no longer applies to this
        # new content - a person must explicitly re-approve it.
        storyboard_dict = dataclasses.asdict(storyboard)
        db.save_original_storyboard_snapshot(project.project_id, storyboard_dict)
        db.save_original_visual_plan_snapshot(project.project_id, None)
        db.unlock_storyboard(project.project_id)
        self._clear_status()
        self._render_storyboard(project, storyboard)
        self._refresh_recent_reels()

    def _handle_visual_plan_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Smart Visual Director failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return
        project, visual_plan = result.value
        self._current_visual_plan = visual_plan
        # Same "this run's own result becomes the new RESET SCENE
        # baseline" reasoning as _handle_storyboard_result() above -
        # running Smart Visual Director (whether for the first time, or
        # again after a REGENERATE ALL cleared the previous snapshot)
        # establishes the current plan as what RESET SCENE restores a
        # scene back to.
        db.save_original_visual_plan_snapshot(project.project_id, dataclasses.asdict(visual_plan))
        self._clear_status()
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)
        self._refresh_recent_reels()

    def _handle_visuals_result(self, result: GenerationTaskResult) -> None:
        self._is_generating = False
        self._scenes_generating = set()
        if result.error:
            self._set_status(f"Scene visual rendering failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            # Neither _create_scene_visuals() nor
            # _create_scene_visuals_with_auto_plan() currently return an
            # error string (the latter DEGRADES to a plain text-card
            # render instead of erroring when its own implicit plan
            # generation fails - see its own docstring) - this guard is
            # kept anyway, checked BEFORE unpacking, as the same
            # defensive "string on failure" convention every other
            # _handle_*_result() in this module follows, so a future
            # change to either function that reintroduces an error-string
            # return can never be silently misread as a tuple here.
            self._set_status(result.value, kind="error")
            return
        auto_generated_plan: VisualPlan | None = None
        if len(result.value) == 3:
            # _create_scene_visuals_with_auto_plan()'s own 3-tuple (see
            # its docstring) - the implicit Smart Visual Director step
            # succeeded, so its plan is stored here exactly as if SMART
            # VISUAL DIRECTOR had been clicked directly, and becomes the
            # new RESET SCENE baseline (same reasoning as
            # _handle_visual_plan_result() above).
            project, visuals, auto_generated_plan = result.value
            self._current_visual_plan = auto_generated_plan
            db.save_original_visual_plan_snapshot(project.project_id, dataclasses.asdict(auto_generated_plan))
        else:
            project, visuals = result.value
        self._current_visuals = visuals
        self._clear_status()
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)
        failed = [v for v in visuals if v.error is not None]
        if failed:
            self._set_status(
                f"{len(failed)} of {len(visuals)} scene visuals couldn't be rendered - see the storyboard above.",
                kind="error",
            )
        elif auto_generated_plan is not None:
            # The implicit-auto-plan path just completed successfully
            # (_do_generate_visuals()'s own fix for the reported bug) -
            # tell the person plainly that real AI-generated images were
            # produced, so this success is never mistaken for the plain
            # text-card fallback below.
            self._set_status(
                f"Generated {len(visuals)} scene visual(s) with AI images from each scene's visual description.",
                kind="muted",
            )
        elif self._current_visual_plan is None:
            # A real, reported failure mode: no LLM is configured at all
            # (_do_generate_visuals() can only auto-run the Smart Visual
            # Director when self._llm is available), or a person runs
            # SMART VISUAL DIRECTOR directly, its LLM call fails (even
            # after generate_visual_plan()'s own retries - a genuine,
            # hand-tested intermittent LLM output issue, see that
            # function's own docstring), sees the resulting error status,
            # but then clicks GENERATE SCENE VISUALS anyway (the button
            # is never disabled/hidden while no plan exists) - it
            # silently renders through the ORIGINAL, unenriched
            # jarvis.reel_generator.scenes.render_all_scenes() path
            # instead (module brief's own hard requirement: never block
            # the original working path), producing a plain text card
            # with no sticker/text-cue enrichment, and no real AI image.
            # Previously nothing distinguished this outcome from "SMART
            # VISUAL DIRECTOR was never run at all" - telling the person
            # explicitly here means a plain result is never mistaken for
            # a rendering bug when it's actually just "no visual plan was
            # loaded for this render".
            self._set_status(
                "Rendered with the standard text-card look (no Smart Visual Director plan was "
                "loaded). Run SMART VISUAL DIRECTOR first for stickers/text overlays/visual variety.",
                kind="muted",
            )

        if not failed:
            self._maybe_start_motion_generation(project)

    def _confirm_paid_motion_generation(self, scene_count: int, on_confirm) -> None:
        """Real-money confirmation gate: every call site that is about to
        trigger a REAL Runway API call (a paid, per-clip external
        service - see jarvis.reel_generator.video_generation's own
        docstring) shows this modal first and only proceeds if the
        person explicitly clicks CONFIRM. Never shown at all if Runway
        isn't configured (there is no paid call to confirm in that case
        - the scene(s) simply fall back to their still image, with the
        existing "not configured" notice already covering that case)."""
        if not motion_engine.video_generation.is_configured():
            on_confirm()
            return
        dialog = ctk.CTkToplevel(self)
        dialog.title("Confirm Motion Generation")
        dialog.geometry("420x220")
        dialog.transient(self.winfo_toplevel())
        inner = ctk.CTkFrame(dialog, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="💳 This will generate real AI video clips",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w", wraplength=380, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner,
            text=(
                f"This will call the Runway API to generate {scene_count} real video clip"
                f"{'s' if scene_count != 1 else ''}. Runway charges per generation - this is a "
                "paid, real request, not a preview.\n\nContinue?"
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=380, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        def confirm() -> None:
            dialog.destroy()
            on_confirm()

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(
            button_row, text="✅ Confirm & Generate", command=confirm, width=170,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="Cancel", command=dialog.destroy, width=100,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _maybe_start_motion_generation(self, project: ReelProject) -> None:
        """NATURAL MOTION / HYBRID modes' own generation trigger - called
        right after still-image generation finishes successfully (never
        instead of it: every scene ALWAYS gets its still image first,
        exactly as in Static mode - a real clip is generated FROM that
        still, and it also serves as the Static/Hybrid-static fallback).
        A no-op for a Static-mode project (the overwhelming majority -
        every project until this control is explicitly changed) or one
        with no scenes marked to move yet. Shows a real-money
        confirmation dialog (see _confirm_paid_motion_generation()'s own
        docstring) before ever calling Runway."""
        storyboard = self._current_storyboard
        if storyboard is None:
            return
        record = db.get_project(project.project_id)
        if record is None or record.reel_mode == db.REEL_MODE_STATIC:
            return
        visual_plan = self._current_visual_plan
        if visual_plan is None:
            self._set_status(
                "Natural Motion/Hybrid needs a Smart Visual Director plan - run it before generating motion.",
                kind="muted",
            )
            return

        global_settings = self._current_global_motion_settings(project)
        motion_data = self._current_motion_settings_data(project)
        if record.reel_mode == db.REEL_MODE_HYBRID:
            moving_scene_numbers = set(motion_data.get("moving_scene_numbers") or [])
            settings_by_scene = {n: global_settings for n in moving_scene_numbers}
        else:
            settings_by_scene = {s.number: global_settings for s in storyboard.scenes}
        if not settings_by_scene:
            return

        plans_by_number = {p.scene_number: p for p in visual_plan.scenes}
        scenes = storyboard.scenes
        visuals = list(self._current_visuals)

        def start() -> None:
            self._is_generating_motion = True
            self._set_status("Generating motion clips...", kind="loading")
            self._render_step_indicator()
            run_generation_in_background(
                lambda: _create_motion_clips(project, scenes, plans_by_number, visuals, settings_by_scene),
                self._result_queue, source=("motion", self),
            )

        self._confirm_paid_motion_generation(len(settings_by_scene), start)

    def _handle_motion_result(self, result: GenerationTaskResult) -> None:
        self._is_generating_motion = False
        if result.error:
            self._set_status(f"Motion generation failed: {result.error}", kind="error")
            return
        project, clips = result.value
        self._current_motion_clips = clips
        db.save_motion_clips(project.project_id, [_serialize_motion_clip(c) for c in clips])
        failed = [c for c in clips if c.error is not None]
        if failed:
            self._set_status(
                f"{len(failed)} of {len(clips)} scene(s) couldn't get a real motion clip - they'll use "
                "their still image instead. See each scene for the specific reason.",
                kind="error" if len(failed) == len(clips) else "muted",
            )
        else:
            self._set_status(f"Generated {len(clips)} real motion clip(s).", kind="muted")
        if self._current_storyboard is not None:
            self._render_storyboard(project, self._current_storyboard)

    def _handle_cover_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Cover generation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return
        project, candidates = result.value
        if not candidates:
            self._set_status("JARVIS couldn't generate any cover variants for this Reel - try again.", kind="error")
            return
        self._current_cover_candidates = candidates
        self._cover_candidate_next_attempt = max(c.attempt for c in candidates) + 1
        self._cover_candidate_all_titles.extend(c.cover_text.title for c in candidates)
        # A fresh GENERATE 3 COVERS/REGENERATE ALL batch replaces
        # whichever candidate was selected before - the person must
        # explicitly SELECT again from the new batch, matching module
        # brief's own "never silently keep an old selection pointing at
        # a candidate that's no longer even shown" reasoning (same as
        # _handle_storyboard_result()'s own "a new storyboard clears the
        # previous approval" precedent elsewhere in this module).
        self._selected_cover_candidate_attempt = None
        self._save_current_cover_candidates(project)
        self._clear_status()
        self._render_cover_candidates()
        self._refresh_recent_reels()

    def _handle_single_cover_candidate_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Cover variant regeneration failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return
        project, new_candidate = result.value
        if new_candidate is None:
            self._set_status("JARVIS couldn't regenerate that cover variant - try again.", kind="error")
            return
        # Replace only this one candidate - every other already-shown
        # candidate is left completely untouched, same "regenerate only
        # the one thing that was clicked" convention as REGENERATE
        # SCENE.
        self._current_cover_candidates = [
            new_candidate if c.attempt == new_candidate.attempt else c for c in self._current_cover_candidates
        ]
        self._cover_candidate_all_titles.append(new_candidate.cover_text.title)
        if self._selected_cover_candidate_attempt == new_candidate.attempt:
            # This candidate was the SELECTED one before being
            # regenerated - re-save cover_path so the export/publish
            # hand-off picks up the new image instead of a stale one.
            db.save_cover_path(project.project_id, str(new_candidate.image_path))
        self._save_current_cover_candidates(project)
        self._clear_status()
        self._render_cover_candidates()
        self._set_status(f"Cover variant {new_candidate.attempt + 1} regenerated.", kind="success")
        self._refresh_recent_reels()

    def _handle_caption_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Caption generation failed: {result.error}", kind="error")
            return
        package = result.value
        self._current_caption_package = package
        self._clear_status()
        self._render_caption(package)

    def _handle_export_result(self, result: GenerationTaskResult) -> None:
        self._is_generating_reel = False
        if result.error:
            self._set_status(f"Export failed: {result.error}", kind="error")
            self._render_step_indicator()
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            self._render_step_indicator()
            return
        export_result = result.value
        self._clear_status()
        # A fresh export already clears reel_approved (db.save_export_
        # path()'s own existing behavior, unmodified) - if a PREVIOUSLY
        # approved-for-publishing content package exists for this
        # project, its own publish_approved flag is now stale too (the
        # Reel it was approved for no longer exists) - same "a fresh
        # result must be re-approved, never silently inherit an old
        # approval" reasoning as unlock_reel_approval() itself.
        if self._current_publish_package is not None and self._current_publish_package.publish_approved:
            unapproved = publish_package_mod.unapprove_for_publishing(self._current_publish_package)
            if self._current_project is not None:
                db.save_publish_package(self._current_project.project_id, dataclasses.asdict(unapproved))
            self._current_publish_package = unapproved
        self._render_export(export_result)
        self._refresh_recent_reels()
        self._render_step_indicator()

    def _handle_footage_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Couldn't analyze this footage: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            # _create_footage_reel() returns a plain error string
            # instead of a (project_id, filename, FootageReelResult)
            # tuple when creation failed before any Video Studio
            # project could even be made - checked BEFORE unpacking,
            # per this module's own established "string on failure,
            # tuple on success" convention (see _handle_create_result()
            # above).
            self._set_status(result.value, kind="error")
            return

        project_id, filename, footage_result = result.value
        self._current_footage_filename = filename
        self._current_footage_video_project_id = footage_result.video_project_id
        self._current_footage_plan = footage_result.plan
        db.create_footage_project_record(
            project_id, filename, video_studio_project_id=footage_result.video_project_id,
            video_studio_filename=filename,
        )
        db.save_footage_plan(project_id, dataclasses.asdict(footage_result.plan))
        self._current_project = storage.project_paths(project_id)
        self._clear_status()
        self._render_footage_plan(footage_result.plan)
        self._refresh_recent_reels()
        if footage_result.insufficient_data:
            self._set_status(
                footage_result.message or "JARVIS couldn't build a Reel from this footage.", kind="error",
            )

    def _handle_footage_export_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Export failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return
        export_result = result.value
        if self._current_project is not None:
            db.save_export_path(self._current_project.project_id, str(export_result.output_path))
        self._clear_status()
        self._render_footage_export(export_result)
        self._refresh_recent_reels()

    def _handle_content_package_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Content package creation failed: {result.error}", kind="error")
            return
        package = result.value
        self._clear_status()
        self._render_content_package(package)

    def _handle_handoff_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Instagram hand-off failed: {result.error}", kind="error")
            return
        handoff_result = result.value
        if self._current_project is not None:
            db.save_handoff(self._current_project.project_id, dataclasses.asdict(handoff_result))
        self._clear_status()
        self._render_handoff_result(handoff_result, self._handoff_container)

    def _handle_footage_handoff_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Instagram hand-off failed: {result.error}", kind="error")
            return
        handoff_result = result.value
        if self._current_project is not None:
            db.save_handoff(self._current_project.project_id, dataclasses.asdict(handoff_result))
        self._clear_status()
        self._render_handoff_result(handoff_result, self._footage_handoff_container)

    # --- recent reels ----------------------------------------------------------------------

    def _refresh_recent_reels(self) -> None:
        self._clear_container(self._recent_reels_container)
        records = db.list_projects(limit=_RECENT_REELS_LIMIT)
        if not records:
            ctk.CTkLabel(
                self._recent_reels_container, text="No Reels yet - describe what you want to create above.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w")
            return

        grid = ctk.CTkFrame(self._recent_reels_container, fg_color="transparent")
        grid.pack(fill="x")
        columns = 3
        for i in range(columns):
            grid.columnconfigure(i, weight=1)
        for index, record in enumerate(records):
            row, col = divmod(index, columns)
            # The derived DRAFT/STORYBOARD APPROVED/GENERATING/READY FOR
            # REVIEW/EXPORTED label (Stage C) is only meaningful for
            # mode="idea" projects (compute_status() reads
            # script_approved/storyboard_data, both of which a
            # mode="footage" project either doesn't use the same way or
            # pre-sets unconditionally at creation - see
            # create_footage_project_record()'s own docstring) - a
            # footage-mode card keeps its ORIGINAL free-text status
            # display instead of a misleading pipeline label.
            pipeline_status = str(project_status.compute_status(record)) if record.mode == "idea" else None
            card = ReelProjectCard(
                grid, idea=record.original_idea, status=record.status, script_approved=record.script_approved,
                created_at=record.created_at, on_click=lambda r=record: self._open_reel(r.id),
                pipeline_status=pipeline_status,
            )
            card.grid(row=row, column=col, sticky="nsew", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

    def _open_reel(self, project_id: str) -> None:
        record = db.get_project(project_id)
        if record is None:
            self._set_status("That Reel could no longer be found.", kind="error")
            return
        project = storage.project_paths(project_id)
        self._current_project = project
        self._current_brief = None
        self._current_script = None
        self._current_storyboard = None
        self._current_visual_plan = None
        self._current_visuals = []
        self._current_motion_clips = (
            [_deserialize_motion_clip(c) for c in record.motion_clips_data] if record.motion_clips_data else []
        )
        self._is_generating_motion = False
        self._current_footage_plan = None
        self._current_footage_video_project_id = None
        self._current_footage_filename = None
        self._current_caption_package = None
        self._current_voiceover_path = None
        self._current_voiceover_text = ""
        self._cover_attempt_count = 0
        self._cover_previous_titles = []
        self._current_cover_candidates = []
        self._selected_cover_candidate_attempt = None
        self._cover_candidate_next_attempt = 0
        self._cover_candidate_all_titles = []
        # Reel Preview stage's own purely in-memory scene selection -
        # reopening a project always starts back at the whole-Reel
        # default thumbnail, never a stale selection from a previous
        # project/session.
        self._reel_preview_selected_scene = None
        self._current_publish_package = None
        self._is_generating_publish_package = False
        # Stage D: restored BEFORE _render_storyboard() below (which
        # reads them via _render_text_mode_controls()) - text_mode
        # defaults to "baked_in" for a record that predates this
        # column (db._row_to_record()'s own `or "baked_in"` fallback),
        # so an old project opens exactly as it always has.
        self._current_text_mode = record.text_mode
        self._current_caption_style = (
            export_mod.CaptionStyle(**record.caption_style_data)
            if record.caption_style_data is not None else export_mod.CaptionStyle()
        )

        self._clear_status()
        self._clear_container(self._brief_container)
        self._clear_container(self._script_container)
        self._clear_container(self._storyboard_container)
        self._clear_container(self._cover_container)
        self._clear_container(self._caption_container)
        self._clear_container(self._voiceover_container)
        self._clear_container(self._export_container)
        self._clear_container(self._quality_container)
        self._clear_container(self._publish_package_container)
        self._clear_container(self._content_package_container)
        self._clear_container(self._handoff_container)
        self._clear_container(self._footage_plan_container)
        self._clear_container(self._footage_export_container)
        self._clear_container(self._footage_quality_container)
        self._clear_container(self._footage_handoff_container)

        if record.mode == "footage":
            self._open_footage_reel(record)
            return

        if record.brief_data is not None:
            brief = _brief_from_dict(record.brief_data)
            self._current_brief = brief
            self._render_brief(brief)
        if record.script_data is not None:
            script = _script_from_dict(record.script_data)
            self._current_script = script
            self._render_script(project, script, approved=record.script_approved)
        if record.storyboard_data is not None:
            storyboard = _storyboard_from_dict(record.storyboard_data)
            self._current_storyboard = storyboard
            if record.visual_plan_data is not None:
                self._current_visual_plan = _visual_plan_from_dict(record.visual_plan_data)
            self._current_visuals = _discover_scene_visuals(project, storyboard)
            self._render_storyboard(project, storyboard)
        if record.cover_candidates_data:
            # 3-cover picker stage: restores the whole candidate set (so
            # SELECT/EDIT/REGENERATE keep working exactly as they did
            # before the project was closed), skipping any candidate
            # whose own image file no longer exists on disk (a project
            # folder moved/partially deleted outside this app) rather
            # than crashing on a missing file - same "a partial result
            # is still useful" convention as every other reopen-restore
            # path in this module.
            restored_candidates = [
                cover_mod.CoverCandidate(
                    attempt=c["attempt"], style_name=c["style_name"],
                    cover_text=cover_mod.CoverText(**c["cover_text"]), image_path=Path(c["image_path"]),
                )
                for c in record.cover_candidates_data
                if Path(c["image_path"]).is_file()
            ]
            self._current_cover_candidates = restored_candidates
            if restored_candidates:
                self._cover_candidate_next_attempt = max(c.attempt for c in restored_candidates) + 1
                self._cover_candidate_all_titles = [c.cover_text.title for c in restored_candidates]
                if record.cover_path is not None:
                    selected = next(
                        (c.attempt for c in restored_candidates if str(c.image_path) == record.cover_path), None,
                    )
                    self._selected_cover_candidate_attempt = selected
                self._render_cover_candidates()
            elif record.cover_path is not None and Path(record.cover_path).is_file():
                self._render_cover(Path(record.cover_path))
        elif record.cover_path is not None and Path(record.cover_path).is_file():
            # A project predating the 3-cover picker stage (or one whose
            # candidate set is genuinely gone from disk) - falls back to
            # the original single-cover display, exactly as before this
            # stage existed.
            self._render_cover(Path(record.cover_path))
        if record.caption_data is not None:
            caption_package = _caption_package_from_dict(record.caption_data)
            self._current_caption_package = caption_package
            self._render_caption(caption_package)
        if record.voiceover_path is not None and Path(record.voiceover_path).is_file():
            self._current_voiceover_path = Path(record.voiceover_path)
            self._current_voiceover_text = record.voiceover_text or ""
            self._render_voiceover(
                voiceover_result=voiceover_mod.VoiceoverResult(
                    ok=True, output_path=self._current_voiceover_path, narration_text=self._current_voiceover_text,
                ),
                output_path=self._current_voiceover_path,
            )
        if record.export_path is not None and Path(record.export_path).is_file():
            self._render_export(_export_result_from_path(Path(record.export_path)))
        # Content Package + Ready to Publish stage: restored regardless
        # of whether an export currently exists (a package generated
        # before a Reel export was later regenerated should still show
        # its own real persisted edits when reopened - the package's own
        # PUBLISH_APPROVED/READY_TO_PUBLISH status derivation, not this
        # restore step, is what correctly reports REEL_APPROVED again if
        # the export path it depended on is now stale - see
        # jarvis.reel_generator.publish_package.compute_publish_status()'s
        # own docstring).
        if record.publish_package_data is not None:
            self._current_publish_package = publish_package_mod.PublishPackage(**record.publish_package_data)
            self._render_publish_package(project, record)
        if record.handoff_data is not None:
            self._render_handoff_result(_handoff_result_from_dict(record.handoff_data), self._handoff_container)
        self._render_step_indicator()

    def _open_footage_reel(self, record: db.ProjectRecord) -> None:
        """Reopens a Mode-A ("create from footage") project - reads the
        linked jarvis.video_studio project's OWN db row directly (that
        project's transcript/highlights/reel_plan_data is the real
        source of truth, not this record's own footage_plan_data cache
        - see jarvis.reel_generator.footage's own docstring for why)."""
        from jarvis.video_studio import db as video_db

        if record.video_studio_project_id is None or record.video_studio_filename is None:
            self._set_status("This Reel's linked footage project is missing some information.", kind="error")
            return
        self._current_footage_filename = record.video_studio_filename
        self._current_footage_video_project_id = record.video_studio_project_id

        video_record = video_db.get_project(record.video_studio_project_id)
        if video_record is None or video_record.reel_plan_data is None:
            self._set_status("This Reel's linked footage project could no longer be found.", kind="error")
            return

        plan = video_reel.ReelEditPlan.from_dict(video_record.reel_plan_data)
        self._current_footage_plan = plan
        self._render_footage_plan(plan)

        exports = video_db.list_exports(record.video_studio_project_id)  # newest-first
        if exports:
            latest_export = exports[0]
            export_path = Path(latest_export.file_path)
            if export_path.is_file():
                self._render_footage_export(_export_result_from_path(export_path))

        if record.handoff_data is not None:
            self._render_handoff_result(_handoff_result_from_dict(record.handoff_data), self._footage_handoff_container)


def _brief_from_dict(data: dict) -> ReelBrief:
    return ReelBrief(**data)


def _script_from_dict(data: dict) -> ReelScript:
    # dataclasses.asdict() recursively flattens nested dataclasses
    # (ScriptSegment) to plain dicts - reconstruct explicitly rather
    # than a bare ReelScript(**data), matching the documented, real bug
    # jarvis.video_studio.db's own "never a bare Dataclass(**data)"
    # convention exists to prevent.
    from jarvis.reel_generator.script import ScriptSegment

    segments = tuple(ScriptSegment(**s) for s in data["segments"])
    return ReelScript(segments=segments)


def _create_reel_concept(
    llm: LLMClient, request_text: str, duration_seconds: int, style_key: str, language: str,
):
    """Runs on a background thread - creates a new project, generates
    the Reel brief, then the script. Returns (project, brief, script)
    on success, or a plain error string on any failure - mirrors
    jarvis.gui.views.design_studio.dashboard._create_design()'s exact
    "string on failure, tuple on success" convention, including the
    caller-side type check that convention's own comment there explains
    is required BEFORE unpacking - see _handle_create_result() above."""
    try:
        project = storage.create_project()
    except StorageError as e:
        return str(e)

    db.create_project_record(project.project_id, request_text)

    brief = generate_reel_brief(
        llm, request_text, duration_seconds=duration_seconds,
        forced_style=style_key if style_key != "automatic" else None, language=language,
    )
    if brief is None:
        return "JARVIS couldn't create a Reel brief for that idea - try rephrasing it."
    db.save_brief(project.project_id, dataclasses.asdict(brief))

    script = generate_reel_script(llm, brief)
    if script is None:
        return "JARVIS created a Reel brief but couldn't fit a script into the selected duration - try a shorter idea or a longer duration."
    db.save_script(project.project_id, dataclasses.asdict(script))

    return project, brief, script


def _regenerate_script(llm: LLMClient, project: ReelProject, brief: ReelBrief):
    """Runs on a background thread. Returns (project, script) on
    success, or a plain error string on failure - same convention as
    _create_reel_concept() above."""
    script = generate_reel_script(llm, brief)
    if script is None:
        return "JARVIS couldn't fit a new script into the selected duration - try a shorter idea or a longer duration."
    db.save_script(project.project_id, dataclasses.asdict(script))
    return project, script


def _storyboard_from_dict(data: dict) -> Storyboard:
    # Same "never a bare Dataclass(**data)" reconstruction as
    # _script_from_dict() above - Storyboard.scenes is a tuple of
    # nested Scene dataclasses, flattened to plain dicts by
    # dataclasses.asdict().
    scenes = tuple(Scene(**s) for s in data["scenes"])
    return Storyboard(scenes=scenes)


def _visual_plan_from_dict(data: dict) -> VisualPlan:
    # Same "never a bare Dataclass(**data)" reconstruction as
    # _storyboard_from_dict() above - VisualPlan.scenes is a tuple of
    # nested ScenePlan dataclasses (each with its own nested tuple of
    # TextCue dataclasses), flattened to plain dicts by
    # dataclasses.asdict().
    from jarvis.reel_generator.visual_plan import ScenePlan, TextCue

    plans = tuple(
        ScenePlan(**{**p, "text_cues": tuple(TextCue(**c) for c in p["text_cues"])})
        for p in data["scenes"]
    )
    return VisualPlan(scenes=plans, visual_style=data.get("visual_style", ""))


def _brand_kit_style_transform():
    """Returns a style_transform callable applying the person's saved
    Brand Kit (a no-op if none is saved), matching
    jarvis.gui.views.design_studio.dashboard's own
    apply_brand_kit()/get_brand_kit() wiring for the same reason: scene
    text-cards and the Reel cover should look consistent with the rest
    of what JARVIS renders for this person."""
    brand_kit = get_brand_kit()
    return lambda style: apply_brand_kit(style, brand_kit)


def _discover_scene_visuals(project: ReelProject, storyboard: Storyboard) -> list[SceneVisual]:
    """Reconstructs SceneVisual entries for an already-rendered
    storyboard by checking which scene image files actually exist on
    disk under the project's scenes_dir - used when reopening a saved
    Reel (jarvis.reel_generator.db has no dedicated scene-visuals table;
    the rendered files themselves ARE the record, matching
    jarvis.design_studio's own "the file on disk is the source of
    truth for what was rendered" convention). A scene whose file is
    missing is simply left out (not reported as a render error - it may
    never have been rendered yet, not necessarily failed)."""
    visuals: list[SceneVisual] = []
    for scene in storyboard.scenes:
        path = project.scenes_dir / f"scene_{scene.number:02d}.jpg"
        if path.is_file():
            visuals.append(SceneVisual(scene_number=scene.number, source="text_card", image_path=path, error=None))
    return visuals


def _caption_package_from_dict(data: dict) -> caption_mod.ReelCaptionPackage:
    return caption_mod.ReelCaptionPackage(caption=data.get("caption"), hashtags=data.get("hashtags"))


def _handoff_result_from_dict(data: dict) -> instagram_handoff.HandoffResult:
    return instagram_handoff.HandoffResult(**data)


def _format_mmss(seconds: float) -> str:
    """Reel Preview stage's own "current time / total duration" display
    - plain M:SS (never negative, always at least "0:00") for a real,
    already-computed duration/position value; not a live playback
    clock (see _render_reel_preview()'s own docstring for why no live
    in-app playback exists)."""
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes}:{secs:02d}"


def _export_result_from_path(path: Path):
    """Reconstructs a lightweight export-result-shaped object (width/
    height/duration_seconds/file_size_bytes/output_path) for
    _render_export() from just a saved path, by re-probing the file -
    jarvis.reel_generator.db only stores the export PATH, not its
    dimensions (the file itself is the source of truth, same reasoning
    as _discover_scene_visuals() above)."""
    from jarvis.reel_generator.export import ReelExportResult
    from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

    try:
        probe = probe_video(path)
        return ReelExportResult(
            output_path=path, duration_seconds=probe.duration_seconds,
            width=probe.width or 1080, height=probe.height or 1920, file_size_bytes=probe.file_size_bytes,
        )
    except FFmpegError:
        return ReelExportResult(
            output_path=path, duration_seconds=0.0, width=1080, height=1920,
            file_size_bytes=path.stat().st_size,
        )


def _create_storyboard(llm: LLMClient, project: ReelProject, brief: ReelBrief, script: ReelScript):
    """Runs on a background thread - generates the storyboard, then
    renders every scene's visual immediately (module brief section 6
    directly follows section 5 in the same flow) using each scene's
    default text-card treatment; a person can regenerate any individual
    outcome afterward. Returns (project, storyboard) on success, or a
    plain error string on failure - same "string on failure, tuple on
    success" convention as _create_reel_concept()."""
    storyboard = generate_storyboard(llm, brief, script)
    if storyboard is None:
        return "JARVIS couldn't build a storyboard for this script - try regenerating the script or approving a simpler one."
    db.save_storyboard(project.project_id, dataclasses.asdict(storyboard))
    return project, storyboard


def _create_visual_plan(
    llm: LLMClient, project: ReelProject, brief: ReelBrief, storyboard: Storyboard, visual_style: str,
):
    """Runs on a background thread - the Smart Visual Director
    (requirements 1-8, 18): generates a VisualPlan for an already-
    generated Storyboard via jarvis.reel_generator.storyboard
    .generate_visual_plan() (a SEPARATE call from storyboard generation
    itself - see that function's own docstring for why). Returns
    (project, visual_plan) on success, or a plain error string on
    failure - same "string on failure, tuple on success" convention as
    this module's other _create_*() functions."""
    visual_plan = generate_visual_plan(llm, brief, storyboard, visual_style=visual_style)
    if visual_plan is None:
        return "JARVIS couldn't generate a visual plan for this storyboard - try again."
    db.save_visual_plan(project.project_id, dataclasses.asdict(visual_plan), visual_style=visual_style)
    return project, visual_plan


def _create_scene_visuals_with_auto_plan(
    llm: LLMClient, project: ReelProject, brief: ReelBrief, storyboard: Storyboard, visual_style: str,
    render_textless: bool = False,
):
    """Runs on a background thread. The implicit "auto Smart Visual
    Director" step _do_generate_visuals() now takes when GENERATE SCENE
    VISUALS is clicked with no visual plan loaded yet (see that method's
    own docstring for the full root-cause explanation). Reuses
    generate_visual_plan() exactly as _create_visual_plan() does
    (including persisting it via db.save_visual_plan() and the same
    "None on failure" handling), then renders through the SAME
    render_all_story_scenes() path _create_scene_visuals() already uses
    when a plan is present - no new rendering logic, only a fused
    "plan, then render" call for this one call site.

    Returns (project, visuals, visual_plan) on success - a 3-tuple,
    deliberately distinct from _create_scene_visuals()'s own 2-tuple
    (project, visuals) - so _handle_visuals_result() can tell them apart
    and additionally store the auto-generated plan into
    self._current_visual_plan (matching what clicking SMART VISUAL
    DIRECTOR directly would have done, so RESET SCENE/per-scene
    regenerate/persistence all work identically afterwards).

    If the plan itself couldn't be generated (generate_visual_plan()
    returns None only after exhausting its own retries - a real,
    hand-tested intermittent LLM output failure, see that function's own
    docstring), this DEGRADES to the plain, pre-existing
    _create_scene_visuals(visual_plan=None) render instead of failing
    the whole call - returning that function's own 2-tuple (project,
    visuals) unchanged. Never returns an error string: exactly like
    _create_scene_visuals() itself, a rendering outcome is always
    produced one way or another, matching this module's own "never
    silently fail to produce SOME visuals" convention, and matching pre-
    fix behavior for every caller that never particularly cared whether
    an enriched plan existed (the many existing tests/flows that treat
    GENERATE SCENE VISUALS purely as "make some visuals exist" as a
    precondition for something else)."""
    visual_plan = generate_visual_plan(llm, brief, storyboard, visual_style=visual_style)
    if visual_plan is None:
        return _create_scene_visuals(project, brief, storyboard, None, render_textless)
    db.save_visual_plan(project.project_id, dataclasses.asdict(visual_plan), visual_style=visual_style)
    plans_by_number = {p.scene_number: p for p in visual_plan.scenes}
    visuals = render_all_story_scenes(
        brief, storyboard.scenes, plans_by_number, output_dir=project.scenes_dir,
        style_transform=_brand_kit_style_transform(), visual_style=visual_plan.visual_style,
        render_textless=render_textless,
    )
    return project, visuals, visual_plan


def _create_scene_visuals(
    project: ReelProject, brief: ReelBrief, storyboard: Storyboard, visual_plan: VisualPlan | None,
    render_textless: bool = False,
):
    """Runs on a background thread. Always returns (project, visuals) -
    a per-scene render failure is recorded on that scene's own
    SceneVisual.error rather than failing the whole call (see
    jarvis.reel_generator.scenes.render_all_scenes()'s own docstring),
    so there is no error-string return path here, unlike this module's
    other _create_*() functions.

    When `visual_plan` is given (the Smart Visual Director has run for
    this project), renders through
    jarvis.reel_generator.scene_render.render_all_story_scenes() instead
    of jarvis.reel_generator.scenes.render_all_scenes() - the SAME base
    render approach, enriched with each scene's own ScenePlan (stickers/
    text cues/supporting visuals/visual-style-aware treatment - see that
    module's own docstring). A project that never ran the Smart Visual
    Director (visual_plan is None) renders through the ORIGINAL,
    unmodified render_all_scenes() exactly as before this stage existed
    - AI Reel Generator's own pre-existing behavior is completely
    unaffected unless a person explicitly clicks SMART VISUAL DIRECTOR
    first.

    `render_textless` (Stage D), if True, produces scenes with NO
    on-screen text baked into their pixels - the project's own
    persisted text_mode ("overlay") decides this at the call site (see
    _do_generate_visuals()'s own docstring), never guessed here.
    Defaults to False, keeping every existing call site's output
    unaffected."""
    if visual_plan is not None:
        plans_by_number = {p.scene_number: p for p in visual_plan.scenes}
        visuals = render_all_story_scenes(
            brief, storyboard.scenes, plans_by_number, output_dir=project.scenes_dir,
            style_transform=_brand_kit_style_transform(), visual_style=visual_plan.visual_style,
            render_textless=render_textless,
        )
    else:
        visuals = render_all_scenes(
            brief, storyboard.scenes, output_dir=project.scenes_dir, style_transform=_brand_kit_style_transform(),
            render_textless=render_textless,
        )
    return project, visuals


def _regenerate_scene_visual(
    project: ReelProject, brief: ReelBrief, scene: Scene, plan: ScenePlan | None, visual_style: str = "",
    render_textless: bool = False,
):
    """Runs on a background thread. Regenerates ONLY this one scene's
    visual (module brief's own "regenerate only one scene, not the
    entire Reel" requirement) - via
    jarvis.reel_generator.scene_render.render_story_scene_visual(),
    writing to the SAME predictable filename
    (scene_{number:02d}.jpg under project.scenes_dir) that
    render_all_story_scenes()/render_all_scenes() already use, so the
    updated file is picked up by every other part of the app (export,
    storyboard thumbnail) without any further wiring. `visual_style` is
    the project's own whole-Reel VisualPlan.visual_style (e.g.
    "modern"/"bold"), not ScenePlan.visual_type - same field
    render_all_story_scenes() itself passes through. `render_textless`
    (Stage D) mirrors the project's own current text_mode - regenerating
    ONE scene never changes its OWN text (scene.on_screen_text is never
    read/written here beyond what render_story_scene_visual() itself
    needs to lay it out - see that function's own docstring for the
    "regenerate visual only, never scene text" guarantee this satisfies)
    but does re-render it in whichever mode (baked-in/textless) the
    project is currently set to, matching every OTHER scene in a
    "GENERATE SCENE VISUALS"-rendered storyboard. Always returns
    (project, SceneVisual) - a render failure is recorded on the
    returned SceneVisual's own .error field, never raised, matching
    this module's other visual-rendering functions' own convention."""
    output_path = project.scenes_dir / f"scene_{scene.number:02d}.jpg"
    visual = render_story_scene_visual(
        brief, scene, plan, output_path=output_path, style_transform=_brand_kit_style_transform(),
        visual_style=visual_style, render_textless=render_textless,
    )
    return project, visual


def _change_scene_camera(
    llm: LLMClient, project: ReelProject, scene: Scene, plan: ScenePlan,
    camera_shot: str | None, camera_movement_override: str | None,
):
    """Runs on a background thread - CHANGE CAMERA. Calls
    jarvis.reel_generator.storyboard.regenerate_scene_camera()
    (a real, focused LLM call), then applies `camera_movement_override`
    if the person picked one of the dialog's own "movement" options
    (Low angle/High angle/Tracking shot/Slow push-in - see
    _do_open_camera_choice_dialog()'s own docstring for why those map to
    camera_movement rather than camera_shot). Always returns
    (project, ScenePlan | None) - None means every LLM attempt failed
    (never raises, matching regenerate_scene_camera()'s own contract);
    this does NOT persist anything itself - the GUI's own
    _handle_change_camera_result() does that via _replace_scene_plan()."""
    new_plan = regenerate_scene_camera(llm, scene=scene, plan=plan, camera_shot=camera_shot)
    if new_plan is None:
        return project, None
    if camera_movement_override is not None:
        new_plan = dataclasses.replace(new_plan, camera_movement=camera_movement_override)
    return project, new_plan


def _change_scene_style(llm: LLMClient, project: ReelProject, scene: Scene, plan: ScenePlan, visual_style: str):
    """Runs on a background thread - CHANGE STYLE. Calls
    jarvis.reel_generator.storyboard.regenerate_scene_style(). Always
    returns (project, ScenePlan | None) - same contract as
    _change_scene_camera() above."""
    new_plan = regenerate_scene_style(llm, scene=scene, plan=plan, visual_style=visual_style)
    return project, new_plan


def _change_scene_visual(llm: LLMClient, project: ReelProject, scene: Scene, plan: ScenePlan):
    """Runs on a background thread - CHANGE VISUAL. Calls
    jarvis.reel_generator.storyboard.regenerate_scene_visual_plan() (the
    module-level function imported under that alias here to avoid
    colliding with this module's own private per-scene image-rendering
    _regenerate_scene_visual() above - same underlying-name-collision
    reasoning documented on that import line itself). Always returns
    (project, ScenePlan | None) - same contract as _change_scene_camera()
    above."""
    new_plan = regenerate_scene_visual_plan(llm, scene=scene, plan=plan)
    return project, new_plan


def _create_cover(
    llm: LLMClient, project: ReelProject, brief: ReelBrief, script: ReelScript, *,
    attempt: int = 0, previous_titles: tuple[str, ...] = (),
):
    """Runs on a background thread. Returns (project, cover_path,
    cover_title) on success, or a plain error string on failure.

    `attempt`/`previous_titles` (real, reported bug fix - see
    cover_mod.render_cover()'s/.generate_cover_text()'s own docstrings)
    are threaded straight through from _on_generate_cover_clicked()'s
    own self._cover_attempt_count/self._cover_previous_titles, so each
    REGENERATE COVER click produces a genuinely different style and
    title instead of repeating the same result."""
    cover_text = cover_mod.generate_cover_text(llm, brief, script, previous_titles=previous_titles)
    if cover_text is None:
        return "JARVIS couldn't write a cover title for this Reel - try again."
    output_path = project.cover_dir / "cover.jpg"
    try:
        result = cover_mod.render_cover(
            brief, cover_text, output_path=output_path, style_transform=_brand_kit_style_transform(),
            attempt=attempt,
        )
    except cover_mod.CoverError as e:
        return f"Couldn't render the cover: {e}"
    db.save_cover_path(project.project_id, str(result.output_path))
    return project, result.output_path, cover_text.title


def _create_cover_candidates(
    llm: LLMClient, project: ReelProject, brief: ReelBrief, script: ReelScript, *,
    start_attempt: int = 0, previous_titles: tuple[str, ...] = (),
):
    """Runs on a background thread - GENERATE 3 COVERS (module brief's
    own requirement). Always returns (project, candidates) - a list of
    0-3 cover_mod.CoverCandidate (never an error string; a candidate
    that failed to generate/render is simply absent from the list, see
    cover_mod.generate_cover_candidates()'s own "one failure doesn't
    stop the others" docstring) - _handle_cover_result() itself reports
    an empty list as an error, since "every candidate failed" IS
    genuinely worth surfacing even though this function's own contract
    never raises for it."""
    candidates = cover_mod.generate_cover_candidates(
        llm, brief, script, output_dir=project.cover_dir, style_transform=_brand_kit_style_transform(),
        start_attempt=start_attempt, previous_titles=previous_titles,
    )
    return project, candidates


def _create_single_cover_candidate(
    llm: LLMClient, project: ReelProject, brief: ReelBrief, script: ReelScript, *,
    attempt: int, previous_titles: tuple[str, ...] = (),
):
    """Runs on a background thread - REGENERATE for just ONE cover
    candidate (module brief's own per-candidate REGENERATE). Returns
    (project, candidate) on success, candidate=None if this one attempt
    failed (text generation or render) - same "None on failure, never
    raise" contract as every other single-item regenerate in this
    module (e.g. _regenerate_scene_visual())."""
    cover_text = cover_mod.generate_cover_text(llm, brief, script, previous_titles=previous_titles)
    if cover_text is None:
        return project, None
    output_path = project.cover_dir / f"cover_{attempt}.jpg"
    try:
        cover_mod.render_cover(
            brief, cover_text, output_path=output_path, style_transform=_brand_kit_style_transform(),
            attempt=attempt,
        )
    except cover_mod.CoverError:
        return project, None
    candidate = cover_mod.CoverCandidate(
        attempt=attempt, style_name=cover_mod.style_for_attempt(brief.style, attempt),
        cover_text=cover_text, image_path=output_path,
    )
    return project, candidate


def _create_caption(llm: LLMClient, project: ReelProject, brief: ReelBrief, script: ReelScript):
    """Runs on a background thread - always returns a
    ReelCaptionPackage (never an error string; a generator's own
    None-on-failure result is carried through on the package itself,
    see jarvis.reel_generator.caption's own docstring for why)."""
    package = caption_mod.generate_reel_caption_package(llm, brief, script)
    db.save_caption(project.project_id, dataclasses.asdict(package))
    return package


def _create_publish_package(
    llm: LLMClient, project: ReelProject, brief: ReelBrief, script: ReelScript,
    existing: publish_package_mod.PublishPackage | None, cover_path: str | None,
):
    """Runs on a background thread. Returns (project, PublishPackage,
    ReelCaptionPackage) - never an error string (matching
    jarvis.reel_generator.publish_package.generate_publish_package()'s
    own "never raises" contract). Persists BOTH results: the
    ReelCaptionPackage via the SAME db.save_caption() the Reel's own
    standalone caption step already calls (so this stage's own caption/
    hashtag generation is never a second, drifting copy of "this Reel's
    caption"), and the PublishPackage via db.save_publish_package() -
    see that module's own docstring for the full reasoning."""
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    package, caption_package = publish_package_mod.generate_publish_package(
        llm, brief=brief, script=script, project_id=project.project_id,
        existing=existing, cover_path=cover_path, now_iso=now_iso,
    )
    db.save_caption(project.project_id, dataclasses.asdict(caption_package))
    db.save_publish_package(project.project_id, dataclasses.asdict(package))
    return project, package, caption_package


def _create_voiceover(project: ReelProject, scenes: tuple[Scene, ...], language: str):
    """Runs on a background thread - always returns (project,
    VoiceoverResult), never an error string (the result itself carries
    ok/error - see jarvis.reel_generator.voiceover.VoiceoverResult's own
    docstring), matching this module's own "string on failure, tuple on
    success" convention's SPIRIT while keeping the richer result object
    (narration_text is useful even on failure, for a GUI preview of what
    WOULD be spoken) rather than collapsing it to a bare string.
    Persists the voiceover path+text to the database on success;
    clears any previously-recorded voiceover on failure, so a stale
    path from a PREVIOUS successful generation is never left pointing
    at content that no longer matches - a fresh regeneration attempt
    that fails must not silently keep exporting with the OLD voiceover
    audio."""
    output_path = project.voiceover_dir / "narration.wav"
    result = voiceover_mod.generate_voiceover(scenes, output_path=output_path, language=language)
    if result.ok:
        assert result.output_path is not None
        db.save_voiceover(project.project_id, str(result.output_path), result.narration_text)
    else:
        db.clear_voiceover(project.project_id)
    return project, result


def _create_motion_clips(
    project: ReelProject, scenes: tuple[Scene, ...], plans_by_number: dict[int, ScenePlan],
    visuals: list[SceneVisual], settings_by_scene: dict[int, MotionSettings],
):
    """Runs on a background thread. Always returns (project, clips) -
    same "one failure doesn't abort the batch" convention as
    _create_scene_visuals() above (jarvis.reel_generator.motion_engine
    .generate_motion_for_scenes()'s own contract - a per-scene failure
    is recorded on that scene's own SceneMotionClip.error, never raised
    past this function). Clips are saved under this project's own
    root_dir/"motion" directory - a new subdirectory, never colliding
    with scenes_dir/cover_dir/exports_dir/voiceover_dir."""
    output_dir = project.root_dir / "motion"
    clips = motion_engine.generate_motion_for_scenes(
        scenes, plans_by_number, visuals, settings_by_scene, output_dir=output_dir,
    )
    return project, clips


def _regenerate_motion_clip(
    project: ReelProject, scene: Scene, plan: ScenePlan, visual: SceneVisual,
    settings: MotionSettings, output_path: Path,
):
    """Runs on a background thread - REGENERATE MOTION for just ONE
    scene (requirement 6). Returns (project, SceneMotionClip) - same
    never-raise contract as jarvis.reel_generator.motion_engine
    .generate_motion_for_scene() itself, which this simply calls
    directly for a single scene rather than the whole-batch
    generate_motion_for_scenes()."""
    clip = motion_engine.generate_motion_for_scene(scene, plan, visual, settings, output_path=output_path)
    return project, clip


def _serialize_motion_clip(clip: SceneMotionClip) -> dict:
    """Converts one SceneMotionClip's Path fields to plain strings for
    JSON storage in db.save_motion_clips() - the GUI's own
    responsibility per that function's own docstring."""
    return {
        "scene_number": clip.scene_number,
        "video_path": str(clip.video_path) if clip.video_path is not None else None,
        "source_image_path": str(clip.source_image_path) if clip.source_image_path is not None else None,
        "error": clip.error,
        "provider_task_id": clip.provider_task_id,
        "duration_seconds": clip.duration_seconds,
    }


def _deserialize_motion_clip(data: dict) -> SceneMotionClip:
    return SceneMotionClip(
        scene_number=data["scene_number"],
        video_path=Path(data["video_path"]) if data.get("video_path") else None,
        source_image_path=Path(data["source_image_path"]) if data.get("source_image_path") else None,
        error=data.get("error"), provider_task_id=data.get("provider_task_id"),
        duration_seconds=data.get("duration_seconds", 5.0),
    )


def _create_export(
    project: ReelProject, scenes: tuple[Scene, ...], visuals: list[SceneVisual],
    voiceover_path: Path | None = None, caption_style: export_mod.CaptionStyle | None = None,
    cover_intro_path: Path | None = None,
    clip_by_scene: dict[int, Path] | None = None,
):
    """Runs on a background thread. Returns a ReelExportResult on
    success, or a plain error string on failure. Always writes to a
    NEW, timestamped file under exports_dir - never overwrites a
    previous export (module brief section 14). `voiceover_path`, if
    given AND the file still exists on disk (a project's own recorded
    voiceover_path in the database could point at a file that was since
    moved/deleted outside the app - checked here rather than trusted
    blindly), is muxed into the export as a real audio track (Stage B -
    see export_reel_video()'s own docstring). A missing voiceover file
    silently falls back to a silent export rather than raising - the
    export itself is still valid without it; a caller/GUI can show a
    separate notice if voiceover-specific feedback is needed.

    `caption_style` (Stage D) is only ever passed by the GUI when the
    project's own text_mode is "overlay" - export_reel_video() itself
    auto-derives whether to burn captions in at all from the visuals'
    own has_baked_in_text (see that function's own docstring), so
    passing a style for a "baked_in"-mode project (caption_style=None,
    the default here) simply has no effect, never a risk of duplicating
    text.

    `cover_intro_path` (cover-in-export bug fix, requirement 2/3):
    already re-checked by the caller (_on_export_clicked()) against the
    project's own cover_integration_mode AND that the file genuinely
    exists on disk before ever being passed in here - this function
    simply threads it straight through to export_reel_video(), which
    inserts it as the exported video's real first 1-2 seconds and
    shifts scene/voiceover/subtitle timing forward to match (see that
    function's own docstring for the full mechanism). None (the
    default) reproduces the exact previous export behavior byte-for-
    byte - an Instagram-Cover-only project's export is completely
    unaffected by this fix.

    `clip_by_scene` (NATURAL MOTION / HYBRID modes): already built by
    the caller (_on_export_clicked()) from this project's own currently-
    persisted reel_mode/motion_clips_data, containing only scene numbers
    whose real clip genuinely exists on disk - this function simply
    threads it straight through to export_reel_video(), which does the
    real too-short-clip verification and splicing (see that function's
    own docstring). None (the default, and always the value for a
    Static-mode project) reproduces the exact previous export behavior
    byte-for-byte."""
    import time

    output_path = project.exports_dir / f"reel_{int(time.time())}.mp4"
    resolved_voiceover_path = voiceover_path if voiceover_path is not None and voiceover_path.is_file() else None
    try:
        result = export_reel_video(
            scenes, visuals, output_path=output_path, voiceover_path=resolved_voiceover_path,
            caption_style=caption_style, cover_intro_path=cover_intro_path, clip_by_scene=clip_by_scene,
        )
    except ExportError as e:
        return f"Export failed: {e}"
    db.save_export_path(project.project_id, str(result.output_path))
    return result


def _create_footage_reel(llm: LLMClient, source_path: Path, duration_seconds: int, style: str, pacing: str):
    """Runs on a background thread - the Mode A entry point (module
    brief section 19). Returns (reel_generator_project_id, filename,
    FootageReelResult) on success - "success" here means a NEW jarvis
    .video_studio project was created and the pipeline ran to
    completion, NOT that a usable plan was found (a plan with
    insufficient_data=True/message set is still a "success" from this
    function's own error-handling point of view - see
    jarvis.reel_generator.footage.create_reel_from_footage()'s own
    docstring for why: the caller can show exactly where the pipeline
    stopped). Returns a plain error string only for a failure BEFORE
    any project could be created (bad file/unsupported format) - same
    "string on failure, tuple on success" convention as this module's
    other _create_*() functions, checked BEFORE unpacking in
    _handle_footage_result() above."""
    result = footage.create_reel_from_footage(
        llm, source_path, target_duration_seconds=duration_seconds, style=style, pacing=pacing,
    )
    if isinstance(result, str):
        return result
    reel_generator_project_id = uuid.uuid4().hex
    return reel_generator_project_id, source_path.name, result


def _export_footage(video_project_id: str, filename: str, plan):
    """Runs on a background thread. Returns an ExportResult on success,
    or a plain error string on failure - same convention as this
    module's other _create_*()/export functions."""
    return footage.export_footage_reel(video_project_id, filename, plan)


def _create_content_package(project: ReelProject, headline: str, cta: str, style: str):
    """Runs on a background thread. Always returns a ContentPackage
    (never an error string) - one piece's own RenderError is recorded
    on that piece, not the whole call, see
    jarvis.reel_generator.content_package's own docstring."""
    return content_package_mod.generate_content_package(
        headline=headline, supporting_text="", cta=cta, style=style,
        output_dir=project.root_dir / "content_package", style_transform=_brand_kit_style_transform(),
    )


def _send_idea_reel_handoff(
    llm: LLMClient, project: ReelProject, brief: ReelBrief, script: ReelScript,
    caption_package: caption_mod.ReelCaptionPackage, cover_path: str | None, export_path: str | None,
):
    """Runs on a background thread. Always returns a HandoffResult
    (never an error string - see jarvis.reel_generator.instagram_handoff's
    own "never raise" contract)."""
    return instagram_handoff.send_idea_reel_to_instagram_manager(
        llm, brief=brief, script=script, caption_package=caption_package,
        cover_path=cover_path, export_path=export_path,
    )


def _send_footage_reel_handoff(llm: LLMClient, plan, export_path: str | None):
    """Runs on a background thread. Always returns a HandoffResult."""
    return instagram_handoff.send_footage_reel_to_instagram_manager(llm, plan=plan, export_path=export_path)
