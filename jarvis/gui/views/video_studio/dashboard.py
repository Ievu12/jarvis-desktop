"""AI Video Studio dashboard (module brief, section 1): the top-level
view for the sidebar's "🎬 AI Video Studio" nav item - an upload area,
a "Recent Projects" strip, and (once a project is selected/uploaded) a
Video Analysis panel (module brief, section 2).

Stage 1 of this feature's staged rollout: upload, storage, and
analysis. Highlight detection, transcription/subtitles, Reel creation,
hooks/cover/caption generation, the timeline editor, and export are
later stages layered onto the same project (jarvis.video_studio.db/
.storage already model the full project structure those stages need -
see those modules' docstrings) - this view's "coming in a later
update" placeholders below name each one explicitly rather than hiding
that they don't exist yet, per this feature's own brief's "Do not
claim functionality is complete unless it has actually been tested."

File selection uses tkinter.filedialog directly (there is no existing
upload/file-picker pattern anywhere else in this GUI to reuse or
follow - confirmed by this feature's own architecture inspection).
Every video-file operation (copying into a project, probing/analyzing
it) is real, possibly-slow I/O and therefore run through
jarvis.gui.worker.run_generation_in_background() on this view's own
polled result queue, exactly like jarvis.gui.views.instagram_ai_manager
.content_studio's generation panels - see that module's docstring for
why a dedicated queue per feature area, not the shared app-level one.
"""

from __future__ import annotations

import dataclasses
import queue
from pathlib import Path
from tkinter import filedialog
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.video_studio.common import ProjectCard, format_duration, format_file_size, status_label
from jarvis.gui.views.video_studio.cover_panel import CoverPanel
from jarvis.gui.views.video_studio.reel_creator_panel import ReelCreatorPanel
from jarvis.gui.views.video_studio.transcript_panel import TranscriptPanel
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.video_studio import db, storage
from jarvis.video_studio.analysis import VideoAnalysis, analyze_video
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available
from jarvis.video_studio.storage import StorageError, VideoProject

_QUEUE_POLL_INTERVAL_MS = 100
_RECENT_PROJECTS_LIMIT = 12

_FILETYPES = (("Video files", "*.mp4 *.mov *.m4v *.webm"), ("All files", "*.*"))


class VideoStudioView(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, navigate: Callable[[str], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._current_project: VideoProject | None = None

        SectionHeader(self, "AI Video Studio").pack(
            anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM),
        )

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        if not ffmpeg_available():
            self._render_ffmpeg_missing()
            self._poll_queue()
            return

        self._build_upload_area()
        self._status_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._analysis_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._analysis_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._transcript_panel = TranscriptPanel(self._scroll, llm=llm)
        self._transcript_panel.pack(fill="x", pady=(theme.SPACE_LG, 0))

        self._reel_creator_panel = ReelCreatorPanel(self._scroll, llm=llm, navigate=navigate)
        self._reel_creator_panel.pack(fill="x", pady=(theme.SPACE_LG, 0))

        self._cover_panel = CoverPanel(
            self._scroll, llm=llm, on_cover_rendered=self._reel_creator_panel.set_cover_path,
        )
        self._cover_panel.pack(fill="x", pady=(theme.SPACE_LG, 0))

        # Wired after all three panels exist (none is constructed yet
        # when an earlier one's __init__ runs) - see
        # TranscriptPanel.__init__'s on_highlights_updated/
        # on_transcript_updated docstrings for the bugs this fixes
        # (Create Reel staying disabled, and Cover Generator's
        # auto-suggested text staying empty, after finding highlights/
        # transcribing in the same session, without this wiring).
        self._transcript_panel.set_on_highlights_updated(self._reel_creator_panel.set_candidates)
        self._transcript_panel.set_on_transcript_updated(self._on_transcript_updated)

        self._recent_section_label = SectionHeader(self._scroll, "Recent Projects")
        self._recent_section_label.pack(anchor="w", pady=(theme.SPACE_LG, theme.SPACE_SM))
        self._recent_projects_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._recent_projects_container.pack(fill="x")

        self._refresh_recent_projects()
        self._poll_queue()

    def _on_transcript_updated(self, transcript_text: str) -> None:
        """Fans TranscriptPanel's single on_transcript_updated callback
        out to every sibling panel that needs the transcript text - see
        TranscriptPanel.set_on_transcript_updated()'s own docstring for
        why this fan-out lives here rather than that setter supporting
        multiple callbacks itself."""
        self._reel_creator_panel.set_transcript_text(transcript_text)
        self._cover_panel.set_transcript_text(transcript_text)

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate()
        calls on every panel it shows - re-lists recent projects (cheap,
        local SQLite read) on every visit so a project created in a
        previous visit or by a later-stage workflow (e.g. the module
        brief's "Create today's Reel" voice command) shows up without
        needing a manual reload. Does NOT re-run analysis or touch the
        currently open project's in-progress state."""
        if hasattr(self, "_recent_projects_container"):
            self._refresh_recent_projects()

    # --- ffmpeg-missing state -----------------------------------------------------------

    def _render_ffmpeg_missing(self) -> None:
        card = Card(self._scroll)
        card.pack(fill="x")
        ctk.CTkLabel(
            card, text="FFmpeg not found",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.DANGER, anchor="w",
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
        ctk.CTkLabel(
            card,
            text=(
                "AI Video Studio requires FFmpeg to be installed and on PATH "
                "(https://ffmpeg.org/download.html). Install it and restart JARVIS."
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))

    # --- upload area ---------------------------------------------------------------------

    def _build_upload_area(self) -> None:
        card = Card(self._scroll)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="DROP VIDEO HERE",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_SECONDARY,
        ).pack(pady=(theme.SPACE_MD, theme.SPACE_SM))
        # Tkinter has no built-in native drag-and-drop file target (that
        # needs a third-party extension - e.g. tkinterdnd2 - not
        # currently a dependency of this project); the module brief's
        # "DROP VIDEO HERE" box is realized here as a large, obviously
        # clickable upload target instead, with the same
        # "📁 Upload Video" action as its button - a real drop zone is a
        # reasonable later addition, not silently promised here.
        ctk.CTkButton(
            inner, text="📁  Upload Video", command=self._on_upload_clicked, height=44, width=220,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
        ).pack(pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner, text="Supported formats: MP4, MOV, M4V, WEBM",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED,
        ).pack()

    def _on_upload_clicked(self) -> None:
        selected = filedialog.askopenfilename(title="Select a video", filetypes=_FILETYPES)
        if not selected:
            return
        self._start_upload(Path(selected))

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _start_upload(self, source_path: Path) -> None:
        self._set_status(f"Uploading {source_path.name}...", kind="loading")
        run_generation_in_background(
            lambda: _create_and_analyze_project(source_path), self._result_queue, source=("upload", self),
        )

    # --- analysis display ------------------------------------------------------------------

    def _render_analysis(self, project: VideoProject, analysis: VideoAnalysis) -> None:
        for widget in self._analysis_container.winfo_children():
            widget.destroy()

        card = Card(self._analysis_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="VIDEO ANALYSIS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner, text=project.original_path.name,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        if analysis.error:
            ctk.CTkLabel(
                inner, text=f"Analysis incomplete: {analysis.error}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.DANGER, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w")
            return

        rows = [
            ("Duration", format_duration(analysis.duration_seconds)),
            (
                "Resolution",
                f"{analysis.width}×{analysis.height}" if analysis.width and analysis.height else "Unknown",
            ),
            ("Aspect ratio", analysis.aspect_ratio or "Unknown"),
            ("FPS", f"{analysis.fps:.2f}" if analysis.fps else "Unknown"),
            ("File size", format_file_size(analysis.file_size_bytes)),
            ("Audio detected", "YES" if analysis.has_audio else "NO"),
            ("Scenes detected", str(analysis.scene_count)),
        ]
        for label, value in rows:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row, text=f"{label}:", width=140,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(side="left")
            ctk.CTkLabel(
                row, text=value,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(side="left")

        if analysis.scene_changes:
            ctk.CTkLabel(
                inner, text="Potential highlight starting points (scene changes):",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w", pady=(theme.SPACE_MD, theme.SPACE_XS))
            timestamps = ", ".join(format_duration(c.timestamp_seconds) for c in analysis.scene_changes[:12])
            ctk.CTkLabel(
                inner, text=timestamps,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_MONO, size=theme.FONT_SIZE_SMALL),
                text_color=theme.ACCENT_PRIMARY, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w")
            # Per the module's brief: "Do not claim that a clip is
            # 'viral'. Describe it as a candidate based on measurable
            # characteristics." - these are exactly and only the
            # measured scene-change timestamps, with no score, ranking,
            # or "highlight" language attached to any individual one.
            ctk.CTkLabel(
                inner,
                text=(
                    "These are measured scene-change points, not scored or ranked - "
                    "AI Highlight Detection (a later update) will build on these plus "
                    "the transcript to suggest specific clip candidates."
                ),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        ctk.CTkLabel(
            inner,
            text=(
                "Highlight clips, subtitles, hooks, covers, captions, the timeline "
                "editor, and export are coming in later updates - this stage covers "
                "upload, storage, and analysis only."
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", pady=(theme.SPACE_MD, 0))

    # --- recent projects -------------------------------------------------------------------

    def _refresh_recent_projects(self) -> None:
        for widget in self._recent_projects_container.winfo_children():
            widget.destroy()

        records = db.list_projects(limit=_RECENT_PROJECTS_LIMIT)
        if not records:
            ctk.CTkLabel(
                self._recent_projects_container, text="No projects yet - upload a video to get started.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w")
            return

        grid = ctk.CTkFrame(self._recent_projects_container, fg_color="transparent")
        grid.pack(fill="x")
        columns = 3
        for i in range(columns):
            grid.columnconfigure(i, weight=1)
        for index, record in enumerate(records):
            row, col = divmod(index, columns)
            card = ProjectCard(
                grid, filename=record.original_filename, status=record.status,
                created_at=record.created_at, on_click=lambda r=record: self._open_project(r.id),
            )
            card.grid(row=row, column=col, sticky="nsew", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

    def _open_project(self, project_id: str) -> None:
        record = db.get_project(project_id)
        if record is None:
            self._set_status("That project could no longer be found.", kind="error")
            return
        project = storage.project_paths(project_id, record.original_filename)
        if not project.original_path.exists():
            self._set_status(
                f"{record.original_filename}'s file is missing from disk - it may have been moved or deleted.",
                kind="error",
            )
            return

        self._current_project = project
        self._transcript_panel.load_project(project, record)
        self._reel_creator_panel.load_project(project, record)
        self._cover_panel.load_project(project, record)
        if record.analysis_data is not None:
            analysis = VideoAnalysis.from_dict(record.analysis_data)
            self._clear_status()
            self._render_analysis(project, analysis)
        else:
            self._set_status(f"Analyzing {record.original_filename}...", kind="loading")
            run_generation_in_background(
                lambda: _analyze_existing_project(project, project_id), self._result_queue,
                source=("analyze", self),
            )

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

        if kind == "upload":
            self._handle_upload_result(result)
        elif kind == "analyze":
            self._handle_analyze_result(result)

    def _handle_upload_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Upload failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            # _create_and_analyze_project() returns a plain error string
            # (via StorageError) instead of a (project, analysis) pair
            # when the upload itself failed (e.g. unsupported format) -
            # see that function's own docstring. Checked BEFORE
            # unpacking - unpacking a str with "a, b = value" silently
            # "succeeds" by splitting its first two characters instead
            # of raising a helpful error, so the type check must come
            # first.
            self._set_status(result.value, kind="error")
            return

        project, analysis = result.value
        self._current_project = project
        record = db.get_project(project.project_id)
        if record is not None:
            self._transcript_panel.load_project(project, record)
            self._reel_creator_panel.load_project(project, record)
            self._cover_panel.load_project(project, record)
        self._clear_status()
        self._render_analysis(project, analysis)
        self._refresh_recent_projects()

    def _handle_analyze_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Analysis failed: {result.error}", kind="error")
            return
        project, analysis = result.value
        record = db.get_project(project.project_id)
        if record is not None:
            self._transcript_panel.load_project(project, record)
            self._reel_creator_panel.load_project(project, record)
            self._cover_panel.load_project(project, record)
        self._clear_status()
        self._render_analysis(project, analysis)
        self._refresh_recent_projects()


def _create_and_analyze_project(source_path: Path):
    """Runs entirely on a background thread (see
    run_generation_in_background() in _start_upload()) - copies the
    file into a new project directory, records it in the database, then
    analyzes it, returning (VideoProject, VideoAnalysis) on success or
    a plain error string on a storage failure (unsupported format,
    missing source, disk error) for the UI thread to show directly."""
    try:
        project = storage.create_project(source_path)
    except StorageError as e:
        return str(e)
    db.create_project_record(project.project_id, project.original_path.name)
    analysis = analyze_video(project.original_path)
    db.save_analysis(project.project_id, dataclasses.asdict(analysis))
    return project, analysis


def _analyze_existing_project(project: VideoProject, project_id: str):
    analysis = analyze_video(project.original_path)
    db.save_analysis(project_id, dataclasses.asdict(analysis))
    return project, analysis
