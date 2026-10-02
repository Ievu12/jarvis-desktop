"""Transcript & Highlights panel (module brief, sections 3 and 5's
transcript half): shown below the Video Analysis panel once a project
is open. Two actions:

  - [Transcribe] - runs jarvis.video_studio.transcribe.transcribe_video()
    in the background (real, possibly-slow ffmpeg+whisper.cpp work -
    see that module's docstring) and renders the resulting transcript
    with per-segment timestamps, an [Edit transcript]-equivalent plain
    text view, [Copy], and [Regenerate].
  - [Find Highlights] - enabled once a transcript exists; runs
    jarvis.video_studio.highlights.find_highlights() in the background
    and renders each HighlightCandidate as a card with its timestamp
    range, transcript excerpt, reason, suggested hook, and confidence,
    plus [Preview]/[Use Clip]/[Trim]/[Reject] buttons per the module's
    brief (module brief, section 3's example buttons) - Preview/Use
    Clip/Trim are later-stage actions (the timeline editor and Reel
    creator this stage doesn't build yet) and say so plainly rather
    than silently doing nothing; Reject only removes the card from
    THIS view (a person deciding a candidate isn't useful) - it never
    deletes the project, the transcript, or touches Instagram, matching
    the module's brief's rule that only an explicit, confirmed action
    may ever be destructive/outward-facing, and a plain "hide this
    suggestion" is neither.

Owns its own result queue and polls it exactly like
jarvis.gui.views.instagram_ai_manager.content_studio - see that
module's docstring for why a dedicated queue per feature area.
"""

from __future__ import annotations

import dataclasses
import queue
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.video_studio.common import format_duration, status_label
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.video_studio import db
from jarvis.video_studio.highlights import HighlightCandidate, HighlightResult, find_highlights
from jarvis.video_studio.storage import VideoProject
from jarvis.video_studio.transcribe import TranscriptionResult, transcribe_video

_QUEUE_POLL_INTERVAL_MS = 100

_CONFIDENCE_COLORS = {"low": theme.TEXT_MUTED, "medium": theme.ACCENT_PRIMARY, "high": theme.SUCCESS}


class TranscriptPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None,
        on_highlights_updated: Callable[[HighlightResult], None] | None = None,
        on_transcript_updated: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._on_highlights_updated = on_highlights_updated
        """Called with the fresh HighlightResult whenever highlights are
        (re)computed by _on_find_highlights_clicked() (NOT by
        load_project() - that path already hands the caller the same
        data directly via the ProjectRecord it's given) - lets
        jarvis.gui.views.video_studio.reel_creator_panel.ReelCreatorPanel
        pick up newly-found candidates without needing its own separate
        find_highlights() call or a full project reload. A real bug,
        found by hand-testing the full upload -> transcribe -> find
        highlights -> create reel pipeline in one session: without
        this, ReelCreatorPanel only ever saw highlights that existed
        BEFORE the project was opened (from load_project()), leaving
        its candidate list empty (and Create Reel disabled) right after
        a person had just successfully found highlights in the same
        session."""
        self._on_transcript_updated = on_transcript_updated
        """Called with the fresh transcript's full_text whenever
        transcription completes (NOT by load_project()) - same bug/fix
        shape as on_highlights_updated above, but for the transcript
        text itself: without this, ReelCreatorPanel.set_cover_path()'s
        sibling panels (ReelCreatorPanel and
        jarvis.gui.views.video_studio.cover_panel.CoverPanel) only ever
        saw a transcript that existed BEFORE the project was opened,
        leaving Cover Generator's auto-suggested cover text silently
        empty right after a person had just transcribed the video in
        the same session - found by the same full-pipeline hand-testing
        that caught the highlights version of this bug."""
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._project: VideoProject | None = None
        self._transcription: TranscriptionResult | None = None
        self._rejected_candidate_keys: set[tuple[float, float]] = set()

        SectionHeader(self, "Transcript & Highlights").pack(anchor="w", pady=(0, theme.SPACE_SM))

        action_row = ctk.CTkFrame(self, fg_color="transparent")
        action_row.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._transcribe_button = ctk.CTkButton(
            action_row, text="Transcribe", command=self._on_transcribe_clicked,
        )
        self._transcribe_button.pack(side="left", padx=(0, theme.SPACE_SM))
        self._regenerate_button = ctk.CTkButton(
            action_row, text="Regenerate", command=self._on_transcribe_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        )
        self._regenerate_button.pack(side="left", padx=(0, theme.SPACE_SM))
        self._copy_button = ctk.CTkButton(
            action_row, text="Copy", command=self._on_copy_transcript_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        )
        self._copy_button.pack(side="left", padx=(0, theme.SPACE_SM))
        self._find_highlights_button = ctk.CTkButton(
            action_row, text="✨ Find Highlights", command=self._on_find_highlights_clicked,
        )
        self._find_highlights_button.pack(side="left")

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(0, theme.SPACE_SM))

        self._transcript_container = ctk.CTkFrame(self, fg_color="transparent")
        self._transcript_container.pack(fill="x", pady=(0, theme.SPACE_MD))

        self._highlights_container = ctk.CTkFrame(self, fg_color="transparent")
        self._highlights_container.pack(fill="x")

        self._update_button_states()
        self._poll_queue()

    # --- project wiring --------------------------------------------------------------------

    def set_on_highlights_updated(self, callback: Callable[[HighlightResult], None]) -> None:
        """Public setter for on_highlights_updated (see __init__'s
        docstring for what it's for) - used by
        jarvis.gui.views.video_studio.dashboard, which must wire this
        AFTER constructing both this panel and ReelCreatorPanel (a
        constructor-time callback isn't possible since neither panel
        exists yet when the other's __init__ runs)."""
        self._on_highlights_updated = callback

    def set_on_transcript_updated(self, callback: Callable[[str], None]) -> None:
        """Public setter for on_transcript_updated (see __init__'s
        docstring for what it's for) - see set_on_highlights_updated()'s
        own docstring for why this is a post-construction setter, not a
        constructor argument. jarvis.gui.views.video_studio.dashboard
        fans this single callback out to BOTH ReelCreatorPanel and
        CoverPanel (each needs the transcript text), by passing a small
        wrapper function that calls each panel's own setter in turn -
        this method itself only supports one callback at a time, same
        as set_on_highlights_updated()."""
        self._on_transcript_updated = callback

    def load_project(self, project: VideoProject, record: db.ProjectRecord) -> None:
        """Called by the dashboard whenever a project is opened/
        uploaded - swaps in this project's already-saved transcript/
        highlights (if any) without re-running anything, matching how
        jarvis.gui.views.video_studio.dashboard itself treats a saved
        VideoAnalysis as already-computed rather than re-analyzing on
        every open."""
        self._project = project
        self._rejected_candidate_keys = set()
        self._clear_status()

        if record.transcript_data is not None:
            self._transcription = TranscriptionResult.from_dict(record.transcript_data)
            self._render_transcript(self._transcription)
        else:
            self._transcription = None
            self._clear_container(self._transcript_container)

        if record.highlights_data is not None:
            self._render_highlights(HighlightResult.from_dict(record.highlights_data))
        else:
            self._clear_container(self._highlights_container)

        self._update_button_states()

    def _update_button_states(self) -> None:
        has_project = self._project is not None
        has_transcript = self._transcription is not None and not self._transcription.error
        self._transcribe_button.configure(state="normal" if has_project else "disabled")
        self._regenerate_button.configure(state="normal" if has_transcript else "disabled")
        self._copy_button.configure(state="normal" if has_transcript else "disabled")
        self._find_highlights_button.configure(
            state="normal" if (has_transcript and self._llm is not None) else "disabled"
        )

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

    # --- transcribe ------------------------------------------------------------------------

    def _on_transcribe_clicked(self) -> None:
        if self._project is None:
            return
        self._set_status("Transcribing... this can take a while for longer videos.", kind="loading")
        self._transcribe_button.configure(state="disabled")
        self._regenerate_button.configure(state="disabled")
        project = self._project
        run_generation_in_background(
            lambda: transcribe_video(project.original_path), self._result_queue, source=("transcribe", self),
        )

    def _render_transcript(self, transcription: TranscriptionResult) -> None:
        self._clear_container(self._transcript_container)
        card = Card(self._transcript_container)
        card.pack(fill="x")
        body = ctk.CTkTextbox(card, height=180, wrap="word", fg_color=theme.BG_SURFACE)
        body.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        if transcription.error:
            body.insert("1.0", transcription.error)
        else:
            lines = [
                f"[{format_duration(s.start_seconds)} - {format_duration(s.end_seconds)}]  {s.text}"
                for s in transcription.segments
            ]
            body.insert("1.0", "\n".join(lines))
        body.configure(state="disabled")

    def _on_copy_transcript_clicked(self) -> None:
        if self._transcription is None or self._transcription.error:
            return
        self.clipboard_clear()
        self.clipboard_append(self._transcription.full_text)
        self._set_status("Transcript copied to clipboard.", kind="muted")

    # --- highlights ------------------------------------------------------------------------

    def _on_find_highlights_clicked(self) -> None:
        if self._project is None or self._transcription is None or self._llm is None:
            return
        self._set_status("Finding highlight candidates...", kind="loading")
        self._find_highlights_button.configure(state="disabled")
        llm = self._llm
        project = self._project
        transcription = self._transcription
        run_generation_in_background(
            lambda: _find_highlights_for_project(llm, project, transcription),
            self._result_queue, source=("highlights", self),
        )

    def _render_highlights(self, result: HighlightResult) -> None:
        self._clear_container(self._highlights_container)
        if result.insufficient_data:
            card = Card(self._highlights_container)
            card.pack(fill="x")
            ctk.CTkLabel(
                card, text=result.message or "No highlight candidates found.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
            return

        visible = [c for c in result.candidates if (c.start_seconds, c.end_seconds) not in self._rejected_candidate_keys]
        for i, candidate in enumerate(visible, start=1):
            self._render_candidate_card(i, candidate)

    def _render_candidate_card(self, index: int, candidate: HighlightCandidate) -> None:
        card = Card(self._highlights_container)
        card.pack(fill="x", pady=(0, theme.SPACE_SM))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x")
        ctk.CTkLabel(
            header, text=f"CLIP #{index}   {format_duration(candidate.start_seconds)} → {format_duration(candidate.end_seconds)}   ({format_duration(candidate.duration_seconds)})",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=candidate.confidence.upper(),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=_CONFIDENCE_COLORS.get(candidate.confidence, theme.TEXT_MUTED),
        ).pack(side="right")

        ctk.CTkLabel(
            inner, text=f"Why: {candidate.reason}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", pady=(theme.SPACE_SM, theme.SPACE_XS))
        ctk.CTkLabel(
            inner, text=f"Suggested hook: \"{candidate.suggested_hook}\"",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        ctk.CTkLabel(
            inner, text=candidate.transcript_text,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_MONO, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x")
        for label, handler in (
            ("Preview", self._make_not_yet_available("Preview")),
            ("Use Clip", self._make_not_yet_available("Use Clip")),
            ("Trim", self._make_not_yet_available("Trim")),
            ("Reject", lambda c=candidate: self._on_reject_clicked(c)),
        ):
            ctk.CTkButton(
                button_row, text=label, command=handler, width=90, height=28,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS))

    def _make_not_yet_available(self, action: str) -> Callable[[], None]:
        def _handler() -> None:
            self._set_status(
                f"{action} is part of the Reel creator / timeline editor, coming in a later update.",
                kind="muted",
            )
        return _handler

    def _on_reject_clicked(self, candidate: HighlightCandidate) -> None:
        # Only hides this candidate from THIS view's current render -
        # never deletes the project, the transcript, or the saved
        # highlights_data row (re-opening the project would show it
        # again) - see this module's own docstring for why this is
        # deliberately not a destructive/persisted action.
        self._rejected_candidate_keys.add((candidate.start_seconds, candidate.end_seconds))
        record = db.get_project(self._project.project_id) if self._project else None
        if record is not None and record.highlights_data is not None:
            self._render_highlights(HighlightResult.from_dict(record.highlights_data))

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
        if kind == "transcribe":
            self._handle_transcribe_result(result)
        elif kind == "highlights":
            self._handle_highlights_result(result)

    def _handle_transcribe_result(self, result: GenerationTaskResult) -> None:
        self._update_button_states()
        if result.error:
            self._set_status(f"Transcription failed: {result.error}", kind="error")
            return
        transcription: TranscriptionResult = result.value
        self._transcription = transcription
        if self._project is not None:
            db.save_transcript(self._project.project_id, dataclasses.asdict(transcription))
        self._update_button_states()

        if transcription.error:
            self._set_status(transcription.error, kind="error")
        else:
            self._clear_status()
        self._render_transcript(transcription)
        if not transcription.error and self._on_transcript_updated is not None:
            self._on_transcript_updated(transcription.full_text)

    def _handle_highlights_result(self, result: GenerationTaskResult) -> None:
        self._update_button_states()
        if result.error:
            self._set_status(f"Highlight detection failed: {result.error}", kind="error")
            return
        highlight_result: HighlightResult = result.value
        if self._project is not None:
            db.save_highlights(self._project.project_id, dataclasses.asdict(highlight_result))
        self._rejected_candidate_keys = set()
        self._clear_status()
        self._render_highlights(highlight_result)
        if self._on_highlights_updated is not None:
            self._on_highlights_updated(highlight_result)


def _find_highlights_for_project(llm: LLMClient, project: VideoProject, transcription: TranscriptionResult):
    """Runs on a background thread - needs the project's VideoAnalysis
    (for scene-change data) freshly loaded from the database rather
    than trusting a possibly-stale in-memory copy, since transcription
    may have happened in a separate session/visit from analysis."""
    from jarvis.video_studio.analysis import VideoAnalysis

    record = db.get_project(project.project_id)
    if record is None or record.analysis_data is None:
        from jarvis.video_studio.highlights import HighlightResult

        return HighlightResult(
            candidates=[], insufficient_data=True,
            message="This project's analysis data is missing - try re-analyzing the video first.",
        )
    analysis = VideoAnalysis.from_dict(record.analysis_data)
    return find_highlights(llm, analysis, transcription)
