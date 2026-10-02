"""AI Storytelling Generator dashboard: the top-level view for the
sidebar's "📖 AI Storytelling Generator" nav item.

Reuses AI Reel Generator's proven video export pipeline UNMODIFIED
(jarvis.reel_generator.export.export_reel_video(), including its
optional `motion_by_scene` Ken Burns parameter - see that function's
own docstring for why passing it never affects Reel Generator's own
calls). Scene VISUALS are rendered by this package's own
jarvis.story_generator.scene_render.render_all_story_scenes() - a
separate module, not an edit to jarvis.reel_generator.scenes, that
reuses that module's exact same base-render approach and then
composites each scene's own ScenePlan (visual type, supporting visuals,
text cues, sticker) on top - see that module's own docstring for the
full reasoning. This view generates what's genuinely new for a story:
the 8-beat narrative structure (jarvis.story_generator.structure), the
story's own 5-10 scenes, and each scene's visual plan
(jarvis.story_generator.story_scenes, jarvis.story_generator.visual_plan).

The module brief's hard approval gate (requirement 6: "APPROVE STORY")
is enforced structurally, exactly like AI Reel Generator's own script-
approval gate (see that module's dashboard docstring for the identical
reasoning): scene generation is only ever reachable from the APPROVE
STORY button's own callback - there is no path in this view that
reaches _on_generate_scenes_clicked() without a structure having been
approved first.

Every LLM call and every Pillow/ffmpeg-blocking call (structure, scene
splitting, scene visual rendering, video export) runs through
jarvis.gui.worker.run_generation_in_background() on this view's own
polled result queue, exactly like every other feature module's
generation panels in this codebase - see
jarvis.gui.views.reel_generator.dashboard's own docstring for why a
dedicated queue per feature area.

Every click handler that starts real work is wrapped in try/except +
logging (module brief requirement 12: failures visible in both UI and
terminal) - mirrors jarvis.gui.views.reel_generator.dashboard's own
_on_generate_visuals_clicked()/_do_generate_visuals() split, added
there specifically because customtkinter's CTkButton has no try/except
of its own around its `command=` callback (confirmed by reading
ctk_button.py in the installed package - an unhandled exception there
only ever reaches Tkinter's default report_callback_exception, a bare
stderr traceback invisible without an attached console)."""

from __future__ import annotations

import dataclasses
import logging
import queue
import time
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.design_studio.brand_kit import apply_brand_kit, get_brand_kit
from jarvis.gui import theme
from jarvis.gui.views.story_generator.common import LabeledDropdown, StoryProjectCard, format_file_size, status_label
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.reel_generator.export import ExportError, export_reel_video
from jarvis.reel_generator.scenes import SceneVisual
from jarvis.reel_generator.storyboard import Scene, Storyboard
from jarvis.story_generator import db, storage
from jarvis.story_generator.scene_render import render_all_story_scenes
from jarvis.story_generator.storage import StorageError, StoryProject
from jarvis.story_generator.story_scenes import generate_story_scenes
from jarvis.story_generator.structure import (
    LANGUAGE_ENGLISH,
    LANGUAGE_LITHUANIAN,
    STORY_TYPE_CHOICES,
    StoryStructure,
    generate_story_structure,
)
from jarvis.story_generator.visual_plan import ScenePlan, TextCue, VisualPlan

logger = logging.getLogger(__name__)
# Standard library logging - see this module's own docstring for why
# (matches jarvis.gui.views.reel_generator.dashboard's own logger, same
# reasoning). No handler is configured here - jarvis.cli.main/jarvis
# .gui.app own the process-wide logging setup (or its absence); this
# module only ever calls logger.exception()/.error().

_QUEUE_POLL_INTERVAL_MS = 100
_RECENT_STORIES_LIMIT = 12

_STORY_TYPE_LABELS = {name: name.replace("_", " ").title() for name in STORY_TYPE_CHOICES}
_STORY_TYPE_DROPDOWN_VALUES = tuple(_STORY_TYPE_LABELS[t] for t in STORY_TYPE_CHOICES)
_STORY_TYPE_LABEL_TO_KEY = {v: k for k, v in _STORY_TYPE_LABELS.items()}

_LANGUAGE_DROPDOWN_VALUES = ("English", "Lithuanian")
_LANGUAGE_LABEL_TO_KEY = {"English": LANGUAGE_ENGLISH, "Lithuanian": LANGUAGE_LITHUANIAN}


class StoryGeneratorView(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None, navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._navigate = navigate
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._current_project: StoryProject | None = None
        self._current_structure: StoryStructure | None = None
        self._current_storyboard: Storyboard | None = None
        self._current_visual_plan: VisualPlan | None = None
        self._current_visuals: list[SceneVisual] = []

        SectionHeader(self, "AI Storytelling Generator").pack(
            anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM),
        )

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        self._build_prompt_area()

        self._status_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._structure_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._structure_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._storyboard_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._storyboard_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._export_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._export_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._recent_section_label = SectionHeader(self._scroll, "Recent Stories")
        self._recent_section_label.pack(anchor="w", pady=(theme.SPACE_LG, theme.SPACE_SM))
        self._recent_stories_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._recent_stories_container.pack(fill="x")

        self._refresh_recent_stories()
        self._poll_queue()

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate()
        calls on every panel it shows - see
        jarvis.gui.views.reel_generator.dashboard.ReelGeneratorView
        .refresh()'s own identical docstring/reasoning."""
        self._refresh_recent_stories()

    def open_project(self, project_id: str) -> None:
        """Public wrapper over _open_story() - same "external caller
        can open a specific project without reaching into a private
        method" reasoning as
        jarvis.gui.views.reel_generator.dashboard.ReelGeneratorView
        .open_project()'s own docstring."""
        self._open_story(project_id)

    # --- prompt area ---------------------------------------------------------------------

    def _build_prompt_area(self) -> None:
        card = Card(self._scroll)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="What story do you want to tell?",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._prompt_entry = ctk.CTkTextbox(inner, height=70, wrap="word", fg_color=theme.BG_SURFACE)
        self._prompt_entry.pack(fill="x", pady=(0, theme.SPACE_SM))

        ctk.CTkLabel(
            inner,
            text='Example: "A founder\'s first product launch failed, and how they recovered."',
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        controls_row = ctk.CTkFrame(inner, fg_color="transparent")
        controls_row.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._story_type_dropdown = LabeledDropdown(controls_row, "Story type:", _STORY_TYPE_DROPDOWN_VALUES)
        self._story_type_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._language_dropdown = LabeledDropdown(controls_row, "Language:", _LANGUAGE_DROPDOWN_VALUES)
        self._language_dropdown.pack(side="left")

        self._create_button = ctk.CTkButton(
            inner, text="📖 GENERATE STORY", command=self._on_generate_structure_clicked, height=40,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
        )
        self._create_button.pack(anchor="w")

    def _on_generate_structure_clicked(self) -> None:
        try:
            self._do_generate_structure()
        except Exception as e:
            logger.exception("GENERATE STORY click handler raised an unexpected exception")
            self._set_status(f"Couldn't start story generation: {e}", kind="error")

    def _do_generate_structure(self) -> None:
        idea = self._prompt_entry.get("1.0", "end").strip()
        if not idea:
            self._set_status("Describe the story you want to create first.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        story_type = _STORY_TYPE_LABEL_TO_KEY.get(self._story_type_dropdown.get(), STORY_TYPE_CHOICES[0])
        language = _LANGUAGE_LABEL_TO_KEY.get(self._language_dropdown.get(), LANGUAGE_ENGLISH)

        self._set_status("Generating story...", kind="loading")
        self._create_button.configure(state="disabled")
        self._clear_container(self._structure_container)
        self._clear_container(self._storyboard_container)
        self._clear_container(self._export_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _create_story(llm, idea, story_type, language),
            self._result_queue, source=("create", self),
        )

    def _on_regenerate_structure_clicked(self) -> None:
        try:
            self._do_regenerate_structure()
        except Exception as e:
            logger.exception("REGENERATE STORY click handler raised an unexpected exception")
            self._set_status(f"Couldn't start story regeneration: {e}", kind="error")

    def _do_regenerate_structure(self) -> None:
        project = self._current_project
        if project is None:
            self._set_status("No story is loaded yet - generate a story first.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        record = db.get_project(project.project_id)
        if record is None:
            self._set_status("That story could no longer be found.", kind="error")
            return
        idea = record.original_idea
        story_type = record.story_type
        language = self._current_structure.language if self._current_structure is not None else LANGUAGE_ENGLISH

        self._set_status("Generating story...", kind="loading")
        self._clear_container(self._storyboard_container)
        self._clear_container(self._export_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _regenerate_structure(llm, project, idea, story_type, language),
            self._result_queue, source=("regenerate_structure", self),
        )

    # --- structure display + edit/approve ---------------------------------------------------

    def _render_structure(self, project: StoryProject, structure: StoryStructure, *, approved: bool) -> None:
        self._clear_container(self._structure_container)
        card = Card(self._structure_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        header_row = ctk.CTkFrame(inner, fg_color="transparent")
        header_row.pack(fill="x", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            header_row, text="STORY STRUCTURE",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(side="left")
        if approved:
            ctk.CTkLabel(
                header_row, text="✅ Approved",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.SUCCESS, anchor="w",
            ).pack(side="left", padx=(theme.SPACE_SM, 0))

        # Requirement 5: allow the person to edit/review each beat's
        # text before approval - one CTkTextbox per beat, pre-filled,
        # editable in place. Edits are only picked up when the person
        # clicks APPROVE STORY (see _on_approve_clicked() below), same
        # "nothing is finalized until approval" spirit as AI Reel
        # Generator's own script review step.
        self._beat_textboxes: dict[str, ctk.CTkTextbox] = {}
        for beat in structure.beats:
            row = ctk.CTkFrame(inner, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)
            ctk.CTkLabel(
                row_inner, text=beat.label,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")
            textbox = ctk.CTkTextbox(row_inner, height=54, wrap="word", fg_color=theme.BG_CARD)
            textbox.insert("1.0", beat.text)
            textbox.configure(state="normal" if not approved else "disabled")
            textbox.pack(fill="x", pady=(theme.SPACE_XS, 0))
            self._beat_textboxes[beat.kind] = textbox

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        if not approved:
            ctk.CTkButton(
                button_row, text="✅ APPROVE STORY", command=lambda: self._on_approve_clicked(project, structure),
                width=150,
            ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🔄 REGENERATE", command=self._on_regenerate_structure_clicked,
            width=130, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _on_approve_clicked(self, project: StoryProject, structure: StoryStructure) -> None:
        try:
            self._do_approve(project, structure)
        except Exception as e:
            logger.exception("APPROVE STORY click handler raised an unexpected exception")
            self._set_status(f"Couldn't approve this story: {e}", kind="error")

    def _do_approve(self, project: StoryProject, structure: StoryStructure) -> None:
        # Pick up any in-place edits (requirement 5) before persisting/
        # approving - a beat left blank falls back to its last generated
        # text rather than being saved empty.
        edited_beats = []
        for beat in structure.beats:
            textbox = self._beat_textboxes.get(beat.kind)
            edited_text = textbox.get("1.0", "end").strip() if textbox is not None else ""
            edited_beats.append(dataclasses.replace(beat, text=edited_text or beat.text))
        edited_structure = dataclasses.replace(structure, beats=tuple(edited_beats))

        db.save_structure(project.project_id, dataclasses.asdict(edited_structure))
        db.approve_structure(project.project_id)
        self._current_structure = edited_structure
        self._render_structure(project, edited_structure, approved=True)
        self._refresh_recent_stories()
        # Requirement 6-7's gate is structural: scene generation is only
        # ever triggered from HERE, right after db.approve_structure()
        # has actually run - there is no other path in this view that
        # reaches _on_generate_scenes_clicked().
        self._on_generate_scenes_clicked(project, edited_structure)

    # --- scenes (requirements 2-3, 7) -------------------------------------------------------

    def _on_generate_scenes_clicked(self, project: StoryProject, structure: StoryStructure) -> None:
        try:
            self._do_generate_scenes(project, structure)
        except Exception as e:
            logger.exception("Scene generation click handler raised an unexpected exception")
            self._set_status(f"Couldn't start scene generation: {e}", kind="error")

    def _do_generate_scenes(self, project: StoryProject, structure: StoryStructure) -> None:
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        self._set_status("Generating scene visuals...", kind="loading")
        self._clear_container(self._storyboard_container)
        self._clear_container(self._export_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _create_story_scenes(llm, project, structure),
            self._result_queue, source=("scenes", self),
        )

    def _render_storyboard(self, project: StoryProject, storyboard: Storyboard) -> None:
        self._clear_container(self._storyboard_container)
        card = Card(self._storyboard_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="STORYBOARD PREVIEW",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        visuals_by_number = {v.scene_number: v for v in self._current_visuals}
        plans_by_number = (
            {p.scene_number: p for p in self._current_visual_plan.scenes}
            if self._current_visual_plan is not None else {}
        )
        # Requirement 5's edit widgets are collected here so
        # _on_save_plan_edits_clicked() can read them back - same "edit
        # in place, picked up on an explicit save/approve action" spirit
        # as _render_structure()'s own self._beat_textboxes.
        self._scene_plan_widgets: dict[int, dict[str, Any]] = {}

        for scene in storyboard.scenes:
            plan = plans_by_number.get(scene.number)
            row = ctk.CTkFrame(inner, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

            header = ctk.CTkFrame(row_inner, fg_color="transparent")
            header.pack(fill="x")
            visual = visuals_by_number.get(scene.number)
            if visual is not None and visual.error is None and visual.image_path is not None:
                try:
                    from PIL import Image

                    pil_image = Image.open(visual.image_path)
                    ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(68, 121))
                    ctk.CTkLabel(header, image=ctk_image, text="").pack(side="left", padx=(0, theme.SPACE_SM))
                except Exception:
                    pass

            details = ctk.CTkFrame(header, fg_color="transparent")
            details.pack(side="left", fill="x", expand=True)

            ctk.CTkLabel(
                details,
                text=(
                    f"SCENE {scene.number}  ·  {scene.duration_seconds:g} sec  ·  "
                    f"{scene.segment_kind.replace('_', ' ').upper()}  ·  {scene.mood or 'no mood set'}"
                ),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")
            ctk.CTkLabel(
                details, text=f"Narration: {scene.voice_text}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=430, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
            ctk.CTkLabel(
                details, text=f"On-screen text: {scene.on_screen_text}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=430, justify="left",
            ).pack(anchor="w")

            if visual is not None and visual.error is not None:
                ctk.CTkLabel(
                    details, text=f"Couldn't render this scene's visual: {visual.error}",
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                    text_color=theme.DANGER, anchor="w", wraplength=430, justify="left",
                ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

            if plan is not None:
                self._render_scene_plan_summary(row_inner, scene, plan)

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(
            button_row, text="🖼️ REGENERATE SCENE VISUALS",
            command=lambda: self._on_generate_scenes_clicked(project, self._current_structure)
            if self._current_structure is not None else None,
            width=210,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        ctk.CTkButton(
            button_row, text="🎬 GENERATE STORY VIDEO", command=self._on_export_clicked,
            width=190, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _render_scene_plan_summary(self, row_inner: ctk.CTkFrame, scene: Scene, plan: ScenePlan) -> None:
        """Requirement 8's own storyboard UI fields, shown for one
        scene: visual type, stickers, animation, motion, duration -
        exactly the emoji-labeled line the requirement's own mockup
        specifies. Requirement 5's on-screen text cue editing lives here
        too - one editable CTkEntry per cue, picked up by
        _on_save_plan_edits_clicked() below."""
        plan_row = ctk.CTkFrame(row_inner, fg_color=theme.BG_CARD, corner_radius=theme.RADIUS_BUTTON)
        plan_row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        plan_inner = ctk.CTkFrame(plan_row, fg_color="transparent")
        plan_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        sticker_display = f"{plan.sticker_glyph} {plan.sticker}" if plan.sticker != "none" else "none"
        ctk.CTkLabel(
            plan_inner,
            text=(
                f"🎨 {plan.visual_type.replace('_', ' ')}   "
                f"🎀 {sticker_display}   "
                f"🎥 {plan.motion.replace('_', ' ')}   "
                f"⏱ {scene.duration_seconds:g}s"
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=430, justify="left",
        ).pack(anchor="w")

        if plan.main_visual_prompt:
            ctk.CTkLabel(
                plan_inner, text=f"Main visual: {plan.main_visual_prompt}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w", wraplength=430, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        if plan.supporting_visuals:
            ctk.CTkLabel(
                plan_inner, text="Supporting: " + ", ".join(plan.supporting_visuals),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w", wraplength=430, justify="left",
            ).pack(anchor="w")

        cue_entries: list[ctk.CTkEntry] = []
        for cue in plan.text_cues:
            cue_row = ctk.CTkFrame(plan_inner, fg_color="transparent")
            cue_row.pack(fill="x", pady=(theme.SPACE_XS, 0))
            ctk.CTkLabel(
                cue_row, text=f"✨ {cue.position} {cue.start_seconds:g}-{cue.end_seconds:g}s ({cue.animation}):",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w", width=190,
            ).pack(side="left")
            entry = ctk.CTkEntry(cue_row, fg_color=theme.BG_SURFACE)
            entry.insert(0, cue.text)
            entry.pack(side="left", fill="x", expand=True, padx=(theme.SPACE_XS, 0))
            cue_entries.append(entry)

        self._scene_plan_widgets[scene.number] = {"cue_entries": cue_entries}

    # --- video export (requirements 8-10) ---------------------------------------------------

    def _on_export_clicked(self) -> None:
        try:
            self._do_export()
        except Exception as e:
            logger.exception("GENERATE STORY VIDEO click handler raised an unexpected exception")
            self._set_status(f"Couldn't start video generation: {e}", kind="error")

    def _do_export(self) -> None:
        project = self._current_project
        storyboard = self._current_storyboard
        if project is None or storyboard is None:
            self._set_status(
                "No scenes are loaded yet - approve a story and generate its scenes first.",
                kind="error",
            )
            return
        incomplete = [v for v in self._current_visuals if v.error is not None or v.image_path is None]
        if incomplete or len(self._current_visuals) != len(storyboard.scenes):
            self._set_status(
                "Every scene needs a successfully rendered visual before generating the video - "
                "regenerate any failed scenes first.", kind="error",
            )
            return
        self._set_status("Rendering story video...", kind="loading")
        scenes = storyboard.scenes
        visuals = list(self._current_visuals)
        motion_by_scene = (
            {p.scene_number: p.motion for p in self._current_visual_plan.scenes}
            if self._current_visual_plan is not None else {}
        )
        run_generation_in_background(
            lambda: _create_export(project, scenes, visuals, motion_by_scene),
            self._result_queue, source=("export", self),
        )

    def _render_export(self, result) -> None:
        self._clear_container(self._export_container)
        card = Card(self._export_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            inner, text="✅ COMPLETED - STORY VIDEO",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.SUCCESS, anchor="w",
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
        ctk.CTkButton(
            inner, text="Open Folder", command=lambda: self._open_folder(result.output_path),
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w")

    def _open_folder(self, path: Path) -> None:
        try:
            import os
            import subprocess
            import sys

            if sys.platform == "win32":
                os.startfile(path.parent)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", str(path.parent)], check=False)
        except Exception as e:
            logger.exception("Couldn't open the export folder")
            self._set_status(f"Couldn't open the folder: {e}", kind="error")

    # --- status/container helpers ------------------------------------------------------------

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

    # --- background result handling -----------------------------------------------------------

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
        elif kind == "regenerate_structure":
            self._handle_regenerate_structure_result(result)
        elif kind == "scenes":
            self._handle_scenes_result(result)
        elif kind == "export":
            self._handle_export_result(result)

    def _handle_create_result(self, result: GenerationTaskResult) -> None:
        self._create_button.configure(state="normal")
        if result.error:
            self._set_status(f"Story generation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            # _create_story() returns a plain error string instead of a
            # (project, structure) tuple when creation failed - checked
            # BEFORE unpacking, per this codebase's established
            # "unpacking a string silently succeeds" hazard (see
            # jarvis.gui.views.reel_generator.dashboard
            # ._handle_create_result()'s own identical comment).
            self._set_status(result.value, kind="error")
            return

        project, structure = result.value
        self._current_project = project
        self._current_structure = structure
        self._current_storyboard = None
        self._current_visual_plan = None
        self._current_visuals = []
        self._clear_status()
        self._render_structure(project, structure, approved=False)
        self._clear_container(self._storyboard_container)
        self._clear_container(self._export_container)
        self._refresh_recent_stories()

    def _handle_regenerate_structure_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Story regeneration failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return

        project, structure = result.value
        self._current_structure = structure
        self._current_storyboard = None
        self._current_visual_plan = None
        self._current_visuals = []
        self._clear_status()
        self._render_structure(project, structure, approved=False)
        self._clear_container(self._storyboard_container)
        self._clear_container(self._export_container)
        self._refresh_recent_stories()

    def _handle_scenes_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Scene generation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return

        project, storyboard, visual_plan, visuals = result.value
        self._current_storyboard = storyboard
        self._current_visual_plan = visual_plan
        self._current_visuals = visuals
        self._clear_status()
        self._render_storyboard(project, storyboard)
        self._refresh_recent_stories()
        failed = [v for v in visuals if v.error is not None]
        if failed:
            self._set_status(
                f"{len(failed)} of {len(visuals)} scene visuals couldn't be rendered - see the storyboard above.",
                kind="error",
            )

    def _handle_export_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Video generation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            self._set_status(result.value, kind="error")
            return
        export_result = result.value
        self._clear_status()
        self._render_export(export_result)
        self._refresh_recent_stories()

    # --- recent stories ----------------------------------------------------------------------

    def _refresh_recent_stories(self) -> None:
        self._clear_container(self._recent_stories_container)
        records = db.list_projects(limit=_RECENT_STORIES_LIMIT)
        if not records:
            ctk.CTkLabel(
                self._recent_stories_container,
                text="No stories yet - describe the story you want to create above.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w")
            return

        grid = ctk.CTkFrame(self._recent_stories_container, fg_color="transparent")
        grid.pack(fill="x")
        columns = 3
        for i in range(columns):
            grid.columnconfigure(i, weight=1)
        for index, record in enumerate(records):
            row, col = divmod(index, columns)
            card = StoryProjectCard(
                grid, idea=record.original_idea, status=record.status,
                structure_approved=record.structure_approved, created_at=record.created_at,
                on_click=lambda r=record: self._open_story(r.id),
            )
            card.grid(row=row, column=col, sticky="nsew", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

    def _open_story(self, project_id: str) -> None:
        record = db.get_project(project_id)
        if record is None:
            self._set_status("That story could no longer be found.", kind="error")
            return
        project = storage.project_paths(project_id)
        self._current_project = project
        self._current_structure = None
        self._current_storyboard = None
        self._current_visual_plan = None
        self._current_visuals = []

        self._clear_status()
        self._clear_container(self._structure_container)
        self._clear_container(self._storyboard_container)
        self._clear_container(self._export_container)

        if record.structure_data is not None:
            structure = _structure_from_dict(record.structure_data)
            self._current_structure = structure
            self._render_structure(project, structure, approved=record.structure_approved)
        if record.storyboard_data is not None:
            storyboard = _storyboard_from_dict(record.storyboard_data)
            self._current_storyboard = storyboard
            if record.visual_plan_data is not None:
                self._current_visual_plan = _visual_plan_from_dict(record.visual_plan_data)
            self._current_visuals = _discover_scene_visuals(project, storyboard)
            self._render_storyboard(project, storyboard)
        if record.export_path is not None and Path(record.export_path).is_file():
            self._render_export(_export_result_from_path(Path(record.export_path)))
        if record.status == "failed" and record.error_message:
            self._set_status(record.error_message, kind="error")


def _structure_from_dict(data: dict) -> StoryStructure:
    # dataclasses.asdict() recursively flattens nested dataclasses
    # (StoryBeat) to plain dicts - reconstruct explicitly rather than a
    # bare StoryStructure(**data), matching jarvis.reel_generator
    # .dashboard's own "never a bare Dataclass(**data)" convention (see
    # that module's _script_from_dict() docstring for the full
    # rationale).
    from jarvis.story_generator.structure import StoryBeat

    beats = tuple(StoryBeat(**b) for b in data["beats"])
    return StoryStructure(story_type=data["story_type"], language=data["language"], beats=beats)


def _storyboard_from_dict(data: dict) -> Storyboard:
    scenes = tuple(Scene(**s) for s in data["scenes"])
    return Storyboard(scenes=scenes)


def _visual_plan_from_dict(data: dict) -> VisualPlan:
    # Same "never a bare Dataclass(**data)" reconstruction as
    # _storyboard_from_dict() above - VisualPlan.scenes is a tuple of
    # nested ScenePlan dataclasses (each with its own nested tuple of
    # TextCue dataclasses), flattened to plain dicts by
    # dataclasses.asdict().
    plans = tuple(
        ScenePlan(**{**p, "text_cues": tuple(TextCue(**c) for c in p["text_cues"])})
        for p in data["scenes"]
    )
    return VisualPlan(scenes=plans)


def _discover_scene_visuals(project: StoryProject, storyboard: Storyboard) -> list[SceneVisual]:
    """Reconstructs SceneVisual entries for an already-rendered
    storyboard by checking which scene image files actually exist on
    disk under the project's scenes_dir - same convention as
    jarvis.gui.views.reel_generator.dashboard._discover_scene_visuals()'s
    own docstring (the rendered files themselves ARE the record)."""
    visuals: list[SceneVisual] = []
    for scene in storyboard.scenes:
        path = project.scenes_dir / f"scene_{scene.number:02d}.jpg"
        if path.is_file():
            visuals.append(SceneVisual(scene_number=scene.number, source="text_card", image_path=path, error=None))
    return visuals


def _export_result_from_path(path: Path):
    """Reconstructs a lightweight export-result-shaped object by
    re-probing the file - same convention as
    jarvis.gui.views.reel_generator.dashboard._export_result_from_path()'s
    own docstring."""
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


def _brand_kit_style_transform():
    """Returns a style_transform callable applying the person's saved
    Brand Kit (a no-op if none is saved) - same reasoning as
    jarvis.gui.views.reel_generator.dashboard's own identical helper:
    scene text-cards should look consistent with the rest of what
    JARVIS renders for this person."""
    brand_kit = get_brand_kit()
    return lambda style: apply_brand_kit(style, brand_kit)


def _create_story(llm: LLMClient, idea: str, story_type: str, language: str):
    """Runs on a background thread - creates a new project and
    generates the 8-beat story structure. Returns (project, structure)
    on success, or a plain error string on failure - mirrors
    jarvis.gui.views.reel_generator.dashboard._create_reel_concept()'s
    exact "string on failure, tuple on success" convention."""
    try:
        project = storage.create_project()
    except StorageError as e:
        return str(e)

    db.create_project_record(project.project_id, idea, story_type=story_type)

    structure = generate_story_structure(llm, idea, story_type=story_type, language=language)
    if structure is None:
        return "JARVIS couldn't create a story structure for that idea - try rephrasing it."
    db.save_structure(project.project_id, dataclasses.asdict(structure))

    return project, structure


def _regenerate_structure(llm: LLMClient, project: StoryProject, idea: str, story_type: str, language: str):
    """Runs on a background thread. Returns (project, structure) on
    success, or a plain error string on failure - same convention as
    _create_story() above."""
    structure = generate_story_structure(llm, idea, story_type=story_type, language=language)
    if structure is None:
        return "JARVIS couldn't regenerate a story structure for this idea - try again."
    db.save_structure(project.project_id, dataclasses.asdict(structure))
    return project, structure


def _create_story_scenes(llm: LLMClient, project: StoryProject, structure: StoryStructure):
    """Runs on a background thread - generates the story's 5-10 scenes
    AND their visual plans in one call (jarvis.story_generator
    .story_scenes.generate_story_scenes() - see that function's own
    docstring), then renders every scene's visual immediately using
    jarvis.story_generator.scene_render.render_all_story_scenes() (this
    package's own richer renderer - reuses Reel Generator's exact same
    base-render approach, then composites each ScenePlan's supporting
    visuals/text cues/sticker on top - see that module's own docstring).
    Returns (project, storyboard, visual_plan, visuals) on success, or a
    plain error string on failure - same "string on failure, tuple on
    success" convention as _create_story()."""
    result = generate_story_scenes(llm, structure)
    if result is None:
        return "JARVIS couldn't split this story into scenes - try regenerating the story or approving a simpler one."
    storyboard, visual_plan = result
    db.save_storyboard(project.project_id, dataclasses.asdict(storyboard))
    db.save_visual_plan(project.project_id, dataclasses.asdict(visual_plan))

    plans_by_number = {p.scene_number: p for p in visual_plan.scenes}
    visuals = render_all_story_scenes(
        _brief_for_scene_rendering(structure), storyboard.scenes, plans_by_number,
        output_dir=project.scenes_dir, style_transform=_brand_kit_style_transform(),
    )
    db.set_status(project.project_id, "visuals_generated")
    return project, storyboard, visual_plan, visuals


def _brief_for_scene_rendering(structure: StoryStructure):
    """jarvis.story_generator.scene_render.render_all_story_scenes()
    takes a ReelBrief purely for its .style/.topic/.objective/.audience/
    .tone fields (used to build each scene's own DesignBrief - see that
    module's own docstring) - never anything Reel-specific. This
    constructs a minimal, valid ReelBrief directly from the story's own
    structure/type so that function can be called completely
    unmodified, without ever creating or touching an actual Reel
    Generator project."""
    from jarvis.reel_generator.brief import ReelBrief

    hook = structure.beat("hook")
    cta = structure.beat("cta")
    emotional_development = structure.beat("emotional_development")
    return ReelBrief(
        topic=structure.story_type.replace("_", " ") + " story",
        audience="general audience",
        objective=hook.text if hook is not None else "tell a story",
        tone=emotional_development.text if emotional_development is not None else "reflective",
        cta=cta.text if cta is not None else "Follow for more",
        style="storytelling",
        duration_seconds=30,
        language=structure.language,
    )


def _create_export(
    project: StoryProject, scenes: tuple[Scene, ...], visuals: list[SceneVisual],
    motion_by_scene: dict[int, str],
):
    """Runs on a background thread - renders the final vertical 9:16
    video using AI Reel Generator's own export_reel_video() (called
    with `motion_by_scene` for real Ken Burns pan/zoom per scene - see
    that function's own docstring for why passing this parameter never
    affects AI Reel Generator's own, unmodified calls). Returns a
    ReelExportResult on success, or a plain error string on failure.
    Always writes to a NEW, timestamped file under exports_dir - never
    overwrites a previous export, same convention as
    jarvis.reel_generator.export's own docstring."""
    output_path = project.exports_dir / f"story_{int(time.time())}.mp4"
    try:
        result = export_reel_video(scenes, visuals, output_path=output_path, motion_by_scene=motion_by_scene)
    except ExportError as e:
        db.set_failed(project.project_id, f"Video export failed: {e}")
        return f"Video export failed: {e}"
    db.save_export_path(project.project_id, str(result.output_path))
    return result
