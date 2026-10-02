"""Auto Reel Creator + Export panel (module brief, sections 4 and 12):
shown below the Transcript & Highlights panel once a project has
highlight candidates. Lets a person choose target duration/style/
pacing, click [✨ Create Reel] to run
jarvis.video_studio.reel.generate_reel_edit() (background, since it's
a real LLM call), review the resulting plan (hook/CTA/clip list/
captions), then pick an export format and click [Export Reel] to run
jarvis.video_studio.export.export_reel() (background - real,
possibly-slow FFmpeg rendering).

Per the module's brief: "Never overwrite the original video. Always
create a new project/export." - export_reel() itself enforces this
(always writes to the project's exports_dir under a fresh filename;
see that function's own docstring), and this panel never offers any
control that could point an export at the original file's own path.

After a successful export, shows filename/duration/resolution/file
size plus [Open File]/[Create Another]/[Create Caption]/[Open Instagram
Manager] per the module's brief section 12. [Open Instagram Manager]
runs jarvis.video_studio.instagram_handoff.send_to_instagram_manager()
in the background (a real LLM call) and then navigates to the
Instagram AI Manager view via the `navigate` callback this panel is
given - see set_handoff_context()'s docstring for how the transcript
text/cover path it needs are supplied. [Create Caption] runs the same
hand-off but stays on this view rather than navigating away, showing a
confirmation instead - useful when a person wants the caption saved
without leaving Video Studio."""

from __future__ import annotations

import dataclasses
import os
import queue
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.video_studio.common import (
    LabeledDropdown,
    format_duration,
    format_file_size,
    status_label,
)
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.video_studio import db
from jarvis.video_studio.export import EXPORT_FORMATS, ExportResult, export_reel
from jarvis.video_studio.highlights import HighlightCandidate, HighlightResult
from jarvis.video_studio.instagram_handoff import HandoffResult, send_to_instagram_manager
from jarvis.video_studio.reel import (
    PACING_OPTIONS,
    STYLES,
    TARGET_DURATIONS,
    PlannedClip,
    ReelEditPlan,
    generate_reel_edit,
)
from jarvis.video_studio.storage import VideoProject

_QUEUE_POLL_INTERVAL_MS = 100


class ReelCreatorPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None, navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._navigate = navigate
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._project: VideoProject | None = None
        self._candidates: list[HighlightCandidate] = []
        self._plan: ReelEditPlan | None = None
        self._transcript_text: str = ""
        self._cover_path: str | None = None
        self._pending_handoff_navigate_after = False

        SectionHeader(self, "✨ Create Reel").pack(anchor="w", pady=(0, theme.SPACE_SM))

        form = ctk.CTkFrame(self, fg_color="transparent")
        form.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._duration_dropdown = LabeledDropdown(
            form, "Target duration:", tuple(f"{d} sec" for d in TARGET_DURATIONS),
        )
        self._duration_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._style_dropdown = LabeledDropdown(form, "Style:", STYLES)
        self._style_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._pacing_dropdown = LabeledDropdown(form, "Pacing:", PACING_OPTIONS)
        self._pacing_dropdown.pack(side="left")

        self._create_button = ctk.CTkButton(
            self, text="✨ Create Reel", command=self._on_create_clicked, state="disabled",
        )
        self._create_button.pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(0, theme.SPACE_SM))

        self._plan_container = ctk.CTkFrame(self, fg_color="transparent")
        self._plan_container.pack(fill="x", pady=(0, theme.SPACE_MD))

        self._export_section = ctk.CTkFrame(self, fg_color="transparent")
        # Packed only once a plan exists - see _render_plan().

        self._poll_queue()

    # --- project wiring --------------------------------------------------------------------

    def load_project(self, project: VideoProject, record: db.ProjectRecord) -> None:
        """Called by the dashboard whenever a project is opened/
        uploaded - mirrors jarvis.gui.views.video_studio.transcript_panel
        .TranscriptPanel.load_project()'s contract."""
        self._project = project
        self._clear_status()
        self._clear_container(self._plan_container)
        self._export_section.pack_forget()
        for widget in self._export_section.winfo_children():
            widget.destroy()

        self._candidates = []
        if record.highlights_data is not None:
            highlights = HighlightResult.from_dict(record.highlights_data)
            if not highlights.insufficient_data:
                self._candidates = highlights.candidates

        self._transcript_text = ""
        if record.transcript_data is not None:
            from jarvis.video_studio.transcribe import TranscriptionResult

            transcription = TranscriptionResult.from_dict(record.transcript_data)
            if not transcription.error:
                self._transcript_text = transcription.full_text
        self._cover_path = record.cover_path

        self._plan = None
        if record.reel_plan_data is not None:
            self._plan = ReelEditPlan.from_dict(record.reel_plan_data)
            self._render_plan(self._plan)

        self._update_button_states()

    def set_cover_path(self, cover_path: str) -> None:
        """Called by the dashboard (wired to
        jarvis.gui.views.video_studio.cover_panel.CoverPanel, once a
        cover is rendered) so a hand-off in the SAME session picks up
        the just-rendered cover without needing a full project reload -
        same reasoning as set_candidates()'s own docstring."""
        self._cover_path = cover_path

    def set_transcript_text(self, transcript_text: str) -> None:
        """Called by the dashboard (wired to
        jarvis.gui.views.video_studio.transcript_panel.TranscriptPanel's
        on_transcript_updated callback) so a hand-off in the SAME
        session has the transcript text without needing a full project
        reload - see that callback's own docstring for the bug this
        fixes (found by hand-testing the full upload -> transcribe ->
        ... -> hand-off pipeline in one session)."""
        self._transcript_text = transcript_text

    def set_candidates(self, highlights: HighlightResult) -> None:
        """Called by the dashboard (wired to
        jarvis.gui.views.video_studio.transcript_panel.TranscriptPanel's
        on_highlights_updated callback) whenever highlights are
        (re)computed in the SAME session, without a full project
        reload - see that callback's own docstring for the bug this
        fixes. Does not touch self._plan/the export section; a
        previously-created plan (from stale candidates) stays visible
        until the person clicks Create Reel again."""
        self._candidates = [] if highlights.insufficient_data else highlights.candidates
        self._update_button_states()

    def _update_button_states(self) -> None:
        can_create = bool(self._candidates) and self._llm is not None and self._project is not None
        self._create_button.configure(state="normal" if can_create else "disabled")

    # --- status helpers --------------------------------------------------------------------

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        self._clear_status()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _clear_container(self, container: ctk.CTkFrame) -> None:
        for widget in container.winfo_children():
            widget.destroy()

    # --- create reel ---------------------------------------------------------------------

    def _on_create_clicked(self) -> None:
        if self._project is None or self._llm is None or not self._candidates:
            return
        target_duration = int(self._duration_dropdown.get().split()[0])
        style = self._style_dropdown.get()
        pacing = self._pacing_dropdown.get()

        self._set_status("Creating your Reel edit plan...", kind="loading")
        self._create_button.configure(state="disabled")
        llm = self._llm
        candidates = self._candidates
        run_generation_in_background(
            lambda: generate_reel_edit(
                llm, candidates, target_duration_seconds=target_duration, style=style, pacing=pacing,
            ),
            self._result_queue, source=("create_reel", self),
        )

    def _render_plan(self, plan: ReelEditPlan) -> None:
        self._clear_container(self._plan_container)
        if plan.insufficient_data:
            card = Card(self._plan_container)
            card.pack(fill="x")
            ctk.CTkLabel(
                card, text=plan.message or "Couldn't create a Reel edit plan.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
            self._export_section.pack_forget()
            return

        card = Card(self._plan_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text=f"HOOK: {plan.hook}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkLabel(
            inner,
            text=(
                f"{len(plan.clips)} clip(s), {format_duration(plan.total_duration_seconds)} total "
                f"(target: {plan.target_duration_seconds}s, {plan.style}, {plan.pacing} pacing)"
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        # A minimal Timeline Editor (module brief, section 11) over the
        # already-generated plan - delete a clip or reorder it, per the
        # brief's own "keep the editor simple and reliable" instruction
        # (not a full trim/split/volume/per-clip-mute editor; this is
        # the smallest useful review step between AI-generated plan and
        # export). Re-clicking Create Reel is this editor's "undo" (it
        # regenerates the plan from scratch) - there is no separate
        # undo/redo stack.
        ctk.CTkLabel(
            inner, text="TIMELINE (delete or reorder clips before exporting)",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_SM, theme.SPACE_XS))
        for i, clip in enumerate(plan.clips):
            self._render_clip_row(inner, i, clip, len(plan.clips))

        ctk.CTkLabel(
            inner, text=f"CTA: {plan.cta}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.SUCCESS, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

        if plan.silence_removal_suggested or plan.zoom_crop_suggested:
            suggestions = []
            if plan.silence_removal_suggested:
                suggestions.append("removing silence")
                # (optional, not applied automatically - a future increment)
            if plan.zoom_crop_suggested:
                suggestions.append("a zoom/crop pass for a tighter 9:16 frame")
            ctk.CTkLabel(
                inner, text=f"Suggested (not applied automatically): {' and '.join(suggestions)}.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

        self._build_export_section()

    def _render_clip_row(self, master, index: int, clip: PlannedClip, clip_count: int) -> None:
        row = ctk.CTkFrame(master, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
        row.pack(fill="x", pady=(0, theme.SPACE_XS))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_XS)

        ctk.CTkLabel(
            inner,
            text=(
                f"{index + 1}. {format_duration(clip.source_start_seconds)} → "
                f"{format_duration(clip.source_end_seconds)}   —   \"{clip.caption_text}\""
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_PRIMARY, anchor="w", justify="left", wraplength=420,
        ).pack(side="left", fill="x", expand=True)

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(side="right")
        ctk.CTkButton(
            button_row, text="↑", width=28, height=24, command=lambda: self._move_clip(index, -1),
            state="normal" if index > 0 else "disabled",
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE, font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="↓", width=28, height=24, command=lambda: self._move_clip(index, 1),
            state="normal" if index < clip_count - 1 else "disabled",
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE, font=ctk.CTkFont(size=theme.FONT_SIZE_SMALL),
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="Delete", width=60, height=24, command=lambda: self._delete_clip(index),
            state="normal" if clip_count > 1 else "disabled",
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1,
            border_color=theme.BORDER_SUBTLE, font=ctk.CTkFont(size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left")

    def _delete_clip(self, index: int) -> None:
        # Refuses to delete the last remaining clip (an empty plan has
        # nothing to export - export_reel() itself also refuses an
        # empty clip list, but this stops it at the UI before that
        # error round-trips through a background task) - the Delete
        # button is already disabled for a single-clip plan (see
        # _render_clip_row()), this is a defensive second check.
        if self._plan is None or len(self._plan.clips) <= 1:
            return
        new_clips = list(self._plan.clips)
        del new_clips[index]
        self._apply_edited_plan(new_clips)

    def _move_clip(self, index: int, direction: int) -> None:
        if self._plan is None:
            return
        new_index = index + direction
        if not (0 <= new_index < len(self._plan.clips)):
            return
        new_clips = list(self._plan.clips)
        new_clips[index], new_clips[new_index] = new_clips[new_index], new_clips[index]
        self._apply_edited_plan(new_clips)

    def _apply_edited_plan(self, new_clips: list[PlannedClip]) -> None:
        """Rebuilds self._plan with the edited clip list (ReelEditPlan
        is frozen, so dataclasses.replace() is used rather than
        mutating in place) and persists it, so a person's Timeline
        edits survive navigating away and back - exactly like every
        other saved project state in this package."""
        if self._plan is None:
            return
        total_duration = sum(c.duration_seconds for c in new_clips)
        self._plan = dataclasses.replace(self._plan, clips=new_clips, total_duration_seconds=total_duration)
        if self._project is not None:
            db.save_reel_plan(self._project.project_id, dataclasses.asdict(self._plan))
        self._render_plan(self._plan)

    # --- export --------------------------------------------------------------------------

    def _build_export_section(self) -> None:
        for widget in self._export_section.winfo_children():
            widget.destroy()
        self._export_section.pack(fill="x")

        SectionHeader(self._export_section, "Export Reel").pack(anchor="w", pady=(theme.SPACE_MD, theme.SPACE_SM))

        self._export_format_dropdown = LabeledDropdown(
            self._export_section, "Format:", tuple(f.label for f in EXPORT_FORMATS.values()),
        )
        self._export_format_dropdown.pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._export_button = ctk.CTkButton(
            self._export_section, text="EXPORT REEL", command=self._on_export_clicked,
        )
        self._export_button.pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._export_result_container = ctk.CTkFrame(self._export_section, fg_color="transparent")
        self._export_result_container.pack(fill="x")

    def _on_export_clicked(self) -> None:
        if self._project is None or self._plan is None or self._plan.insufficient_data:
            return
        label = self._export_format_dropdown.get()
        format_key = next((k for k, f in EXPORT_FORMATS.items() if f.label == label), "instagram_reel")

        self._set_status("Exporting your Reel...", kind="loading")
        self._export_button.configure(state="disabled")
        project = self._project
        plan = self._plan
        run_generation_in_background(
            lambda: _export_for_project(project, plan, format_key), self._result_queue,
            source=("export", self),
        )

    def _render_export_result(self, result: ExportResult) -> None:
        self._clear_container(self._export_result_container)
        card = Card(self._export_result_container)
        card.pack(fill="x", pady=(theme.SPACE_SM, 0))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        rows = [
            ("Filename", result.output_path.name),
            ("Duration", format_duration(result.duration_seconds)),
            ("Resolution", f"{result.width}×{result.height}"),
            ("File size", format_file_size(result.file_size_bytes)),
        ]
        for label, value in rows:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=1)
            ctk.CTkLabel(
                row, text=f"{label}:", width=100,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=value,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(side="left")

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(
            button_row, text="Open File", command=lambda: self._open_file(result.output_path),
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="Create Another", command=self._on_create_clicked,
            width=130, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="Create Caption", command=lambda: self._on_handoff_clicked(navigate_after=False),
            width=130, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="Open Instagram Manager", command=lambda: self._on_handoff_clicked(navigate_after=True),
            width=170, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    # --- Instagram AI Manager hand-off ----------------------------------------------------

    def _on_handoff_clicked(self, *, navigate_after: bool) -> None:
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        if not self._transcript_text and (self._plan is None or not self._plan.clips):
            self._set_status(
                "Transcribe the video (and ideally create a Reel) first, so the hand-off has real content to work from.",
                kind="error",
            )
            return

        self._set_status("Sending hook/caption/CTA/hashtags to Instagram AI Manager...", kind="loading")
        self._pending_handoff_navigate_after = navigate_after
        llm = self._llm
        transcript_text = self._transcript_text
        plan = self._plan
        cover_path = self._cover_path
        run_generation_in_background(
            lambda: send_to_instagram_manager(
                llm, transcript_text=transcript_text, plan=plan, cover_path=cover_path,
            ),
            self._result_queue, source=("handoff", self),
        )

    def _handle_handoff_result(self, result: GenerationTaskResult) -> None:
        navigate_after = self._pending_handoff_navigate_after
        if result.error:
            self._set_status(f"Hand-off failed: {result.error}", kind="error")
            return
        handoff: HandoffResult = result.value
        if self._project is not None:
            db.save_handoff(
                self._project.project_id,
                {
                    "hook_set_id": handoff.hook_set_id, "caption_id": handoff.caption_id,
                    "cta_set_id": handoff.cta_set_id, "hashtag_set_id": handoff.hashtag_set_id,
                    "cover_path": handoff.cover_path,
                },
            )
        if handoff.insufficient_data:
            self._set_status(handoff.message or "Hand-off didn't produce usable content.", kind="error")
            return

        sent = [
            name for name, value in (
                ("hooks", handoff.hook_set_id), ("caption", handoff.caption_id),
                ("CTAs", handoff.cta_set_id), ("hashtags", handoff.hashtag_set_id),
            ) if value is not None
        ]
        posting_time_note = f" Suggested posting time: {handoff.suggested_posting_time}." if handoff.suggested_posting_time else ""
        self._set_status(
            f"Sent {', '.join(sent)} to Instagram AI Manager's Content Studio.{posting_time_note}",
            kind="muted",
        )
        if navigate_after and self._navigate is not None:
            self._navigate("instagram_ai_manager")

    def _open_file(self, path) -> None:
        try:
            os.startfile(path)  # noqa: S606 - opening a file the person just exported themselves via their OS default handler, not an arbitrary/untrusted path
        except OSError as e:
            self._set_status(f"Couldn't open the file: {e}", kind="error")

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
        if kind == "create_reel":
            self._handle_create_reel_result(result)
        elif kind == "export":
            self._handle_export_result(result)
        elif kind == "handoff":
            self._handle_handoff_result(result)

    def _handle_create_reel_result(self, result: GenerationTaskResult) -> None:
        self._update_button_states()
        if result.error:
            self._set_status(f"Reel creation failed: {result.error}", kind="error")
            return
        plan: ReelEditPlan = result.value
        self._plan = plan
        if self._project is not None:
            db.save_reel_plan(self._project.project_id, dataclasses.asdict(plan))
        if plan.insufficient_data:
            self._set_status(plan.message or "Couldn't create a Reel edit plan.", kind="error")
        else:
            self._clear_status()
        self._render_plan(plan)

    def _handle_export_result(self, result: GenerationTaskResult) -> None:
        self._export_button.configure(state="normal")
        if result.error:
            self._set_status(f"Export failed: {result.error}", kind="error")
            return
        export_result: ExportResult = result.value
        if self._project is not None:
            db.save_export_record(self._project.project_id, export_result)
        self._clear_status()
        self._render_export_result(export_result)


def _export_for_project(project: VideoProject, plan: ReelEditPlan, export_format: str) -> ExportResult:
    """Runs on a background thread - always writes into the project's
    OWN exports_dir under a fresh, collision-avoiding filename (never
    the original video's path), per this module's own docstring and
    jarvis.video_studio.export.export_reel()'s "always a new file"
    rule."""
    import time

    filename = f"{export_format}_{int(time.time())}.mp4"
    output_path = project.exports_dir / filename
    return export_reel(project.original_path, plan, export_format=export_format, output_path=output_path)
