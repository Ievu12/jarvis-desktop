"""Cover Generator panel (module brief, section 7): shown below the
Reel Creator panel once a project's original video is loaded. Extracts
candidate frames (jarvis.video_studio.cover.extract_cover_candidates()),
shows them as clickable thumbnails, generates cover text grounded in
the video's transcript (jarvis.video_studio.cover.generate_cover_text()
- background, real LLM call), lets the person edit that text and pick
a template, then renders the final 1080x1920 cover
(jarvis.video_studio.cover.render_cover() - background, real FFmpeg
render) into the project's cover/ directory.

Thumbnails use customtkinter's CTkImage (Pillow-backed - see this
package's own dependency notes: Pillow was added specifically for this,
confirmed importable on the development machine unlike a few other
compiled-extension packages tried for earlier stages) - the only place
in jarvis.gui.views.video_studio that loads an actual image file for
display rather than just probing/generating video.
"""

from __future__ import annotations

import queue
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk
from PIL import Image

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.video_studio.common import LabeledDropdown, status_label
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background
from jarvis.video_studio import db
from jarvis.video_studio.cover import (
    COVER_TEMPLATES,
    CoverError,
    extract_cover_candidates,
    generate_cover_text,
    render_cover,
)
from jarvis.video_studio.storage import VideoProject
from jarvis.video_studio.transcribe import TranscriptionResult

_QUEUE_POLL_INTERVAL_MS = 100
_THUMBNAIL_SIZE = (108, 192)  # 9:16, small enough for a row of 5


class CoverPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None,
        on_cover_rendered: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._on_cover_rendered = on_cover_rendered
        """Called with the rendered cover's path whenever a cover is
        (re)generated - lets
        jarvis.gui.views.video_studio.reel_creator_panel.ReelCreatorPanel
        pick up a just-rendered cover for the Instagram AI Manager
        hand-off in the SAME session, without a full project reload -
        same "session-local callback, not just load_project()" pattern
        jarvis.gui.views.video_studio.transcript_panel.TranscriptPanel's
        on_highlights_updated already established (see that callback's
        own docstring for the bug this pattern fixes)."""
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._project: VideoProject | None = None
        self._transcript_text: str = ""
        self._candidate_frames: list[Path] = []
        self._selected_frame: Path | None = None
        self._rendered_cover_path: Path | None = None

        SectionHeader(self, "🖼️ Cover Generator").pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._extract_button = ctk.CTkButton(
            self, text="Extract Frame Candidates", command=self._on_extract_clicked, state="disabled",
        )
        self._extract_button.pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._frames_container = ctk.CTkFrame(self, fg_color="transparent")
        self._frames_container.pack(fill="x", pady=(0, theme.SPACE_SM))

        self._text_entry = ctk.CTkEntry(self, placeholder_text="Cover text (auto-filled once a frame is picked)")
        self._text_entry.pack(fill="x", pady=(0, theme.SPACE_SM))

        controls_row = ctk.CTkFrame(self, fg_color="transparent")
        controls_row.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._template_dropdown = LabeledDropdown(
            controls_row, "Template:", tuple(COVER_TEMPLATES.keys()),
        )
        self._template_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        button_row = ctk.CTkFrame(self, fg_color="transparent")
        button_row.pack(anchor="w", pady=(0, theme.SPACE_SM))
        self._generate_button = ctk.CTkButton(
            button_row, text="Generate Cover", command=self._on_generate_cover_clicked, state="disabled",
        )
        self._generate_button.pack(side="left", padx=(0, theme.SPACE_SM))
        self._change_frame_button = ctk.CTkButton(
            button_row, text="Change Frame", command=self._on_extract_clicked, state="disabled",
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        )
        self._change_frame_button.pack(side="left")

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(0, theme.SPACE_SM))

        self._preview_container = ctk.CTkFrame(self, fg_color="transparent")
        self._preview_container.pack(fill="x")

        self._poll_queue()

    # --- project wiring --------------------------------------------------------------------

    def load_project(self, project: VideoProject, record: db.ProjectRecord) -> None:
        """Called by the dashboard whenever a project is opened/
        uploaded - mirrors jarvis.gui.views.video_studio.transcript_panel
        .TranscriptPanel.load_project()'s contract. Extraction/
        generation are NOT re-run automatically (matches every other
        panel in this package's "load saved state, don't redo work"
        convention) - only a previously rendered cover (if any) is
        shown, and Extract Frame Candidates becomes available."""
        self._project = project
        self._candidate_frames = []
        self._selected_frame = None
        self._rendered_cover_path = None
        self._clear_status()
        self._clear_container(self._frames_container)
        self._clear_container(self._preview_container)
        self._text_entry.delete(0, "end")

        self._transcript_text = ""
        if record.transcript_data is not None:
            transcription = TranscriptionResult.from_dict(record.transcript_data)
            if not transcription.error:
                self._transcript_text = transcription.full_text

        if record.cover_path:
            cover_path = Path(record.cover_path)
            if cover_path.is_file():
                self._rendered_cover_path = cover_path
                self._render_cover_preview(cover_path)

        self._update_button_states()

    def set_transcript_text(self, transcript_text: str) -> None:
        """Called by the dashboard (wired to
        jarvis.gui.views.video_studio.transcript_panel.TranscriptPanel's
        on_transcript_updated callback) so a cover-text suggestion in
        the SAME session has the transcript to work from without a
        full project reload - see that callback's own docstring for
        the bug this fixes (found by hand-testing the full pipeline in
        one session: the auto-suggested cover text stayed silently
        empty right after transcribing, since this panel had only ever
        seen a transcript that existed BEFORE the project was opened)."""
        self._transcript_text = transcript_text

    def _update_button_states(self) -> None:
        has_project = self._project is not None
        self._extract_button.configure(state="normal" if has_project else "disabled")
        can_generate = self._selected_frame is not None and self._llm is not None
        self._generate_button.configure(state="normal" if can_generate else "disabled")
        self._change_frame_button.configure(state="normal" if self._candidate_frames else "disabled")

    # --- status/container helpers ----------------------------------------------------------

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        self._clear_status()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _clear_container(self, container: ctk.CTkFrame) -> None:
        for widget in container.winfo_children():
            widget.destroy()

    # --- extract candidates ------------------------------------------------------------------

    def _on_extract_clicked(self) -> None:
        if self._project is None:
            return
        self._set_status("Extracting frame candidates...", kind="loading")
        self._extract_button.configure(state="disabled")
        project = self._project
        run_generation_in_background(
            lambda: extract_cover_candidates(
                project.original_path, duration_seconds=_probe_duration(project.original_path),
                output_dir=project.cover_dir, count=5,
            ),
            self._result_queue, source=("extract", self),
        )

    def _render_frame_thumbnails(self, frames: list[Path]) -> None:
        self._clear_container(self._frames_container)
        row = ctk.CTkFrame(self._frames_container, fg_color="transparent")
        row.pack(fill="x")
        for frame_path in frames:
            try:
                pil_image = Image.open(frame_path)
                ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=_THUMBNAIL_SIZE)
            except Exception:
                continue
            is_selected = frame_path == self._selected_frame
            button = ctk.CTkButton(
                row, image=ctk_image, text="", width=_THUMBNAIL_SIZE[0], height=_THUMBNAIL_SIZE[1],
                fg_color=theme.ACCENT_PRIMARY if is_selected else "transparent",
                hover_color=theme.BG_CARD_HOVER, border_width=2 if is_selected else 0,
                border_color=theme.ACCENT_PRIMARY,
                command=lambda p=frame_path: self._on_frame_selected(p),
            )
            button.pack(side="left", padx=(0, theme.SPACE_SM))

    def _on_frame_selected(self, frame_path: Path) -> None:
        self._selected_frame = frame_path
        self._render_frame_thumbnails(self._candidate_frames)
        self._update_button_states()

        if not self._text_entry.get().strip() and self._llm is not None and self._transcript_text:
            self._set_status("Generating cover text suggestion...", kind="loading")
            llm = self._llm
            transcript = self._transcript_text
            run_generation_in_background(
                lambda: generate_cover_text(llm, transcript), self._result_queue, source=("generate_text", self),
            )

    # --- generate cover ----------------------------------------------------------------------

    def _on_generate_cover_clicked(self) -> None:
        if self._project is None or self._selected_frame is None:
            return
        text = self._text_entry.get().strip()
        if not text:
            self._set_status("Enter cover text first (or pick a frame to get a suggestion).", kind="error")
            return

        template_name = self._template_dropdown.get()
        self._set_status("Rendering cover...", kind="loading")
        self._generate_button.configure(state="disabled")
        project = self._project
        frame = self._selected_frame
        run_generation_in_background(
            lambda: render_cover(
                frame, text, template_name=template_name, output_path=project.cover_dir / "cover.jpg",
            ),
            self._result_queue, source=("render", self),
        )

    def _render_cover_preview(self, cover_path: Path) -> None:
        self._clear_container(self._preview_container)
        card = Card(self._preview_container)
        card.pack(fill="x")
        try:
            pil_image = Image.open(cover_path)
            ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(180, 320))
            ctk.CTkLabel(card, image=ctk_image, text="").pack(padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        except Exception:
            ctk.CTkLabel(
                card, text=f"Cover saved: {cover_path.name}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY,
            ).pack(padx=theme.SPACE_MD, pady=theme.SPACE_MD)

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
        if kind == "extract":
            self._handle_extract_result(result)
        elif kind == "generate_text":
            self._handle_generate_text_result(result)
        elif kind == "render":
            self._handle_render_result(result)

    def _handle_extract_result(self, result: GenerationTaskResult) -> None:
        self._extract_button.configure(state="normal")
        if result.error:
            self._set_status(f"Couldn't extract frames: {result.error}", kind="error")
            return
        self._candidate_frames = result.value
        self._selected_frame = None
        self._clear_status()
        self._render_frame_thumbnails(self._candidate_frames)
        self._update_button_states()

    def _handle_generate_text_result(self, result: GenerationTaskResult) -> None:
        if result.error or not result.value:
            self._clear_status()
            return
        self._text_entry.delete(0, "end")
        self._text_entry.insert(0, result.value)
        self._clear_status()

    def _handle_render_result(self, result: GenerationTaskResult) -> None:
        self._generate_button.configure(state="normal")
        if result.error:
            self._set_status(f"Couldn't render cover: {result.error}", kind="error")
            return
        cover_path: Path = result.value
        self._rendered_cover_path = cover_path
        if self._project is not None:
            db.save_cover(self._project.project_id, str(cover_path))
        self._clear_status()
        self._render_cover_preview(cover_path)
        if self._on_cover_rendered is not None:
            self._on_cover_rendered(str(cover_path))


def _probe_duration(path: Path) -> float:
    from jarvis.video_studio.ffmpeg_utils import probe_video

    return probe_video(path).duration_seconds
