"""Video Editor dashboard - the top-level view for the sidebar's
"🎛️ Video Editor" nav item. Ties together ImportPanel (upload) +
TimelinePanel (trim/merge/retime/transitions) + ExportPanel (format/
resolution/progress/cancel) over ONE project at a time, with a
New Project / Recent Projects strip above them.

Every possibly-slow call (media import, thumbnail extraction, export)
runs through jarvis.gui.worker - import/thumbnail via the established
run_generation_in_background() (bounded, single-result calls), export
via the new run_cancelable_in_background() (see that function's own
docstring) since only export needs real progress/cancellation.

Stage 2 of jarvis.video_editor's own staged rollout - see that
package's docstring. No AI features here at all (auto-edit suggestions/
captions/effects/AI motion/templates are later stages, each adding
their own panel without modifying this file's own core wiring, same
"new panel/new optional branch, never rewrite the existing one"
discipline this codebase's other staged features already established -
see jarvis.reel_generator.dashboard's own NATURAL MOTION addition for
the precedent)."""

from __future__ import annotations

import queue
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.video_editor.captions_panel import CaptionsPanel
from jarvis.gui.views.video_editor.export_panel import ExportPanel
from jarvis.gui.views.video_editor.import_panel import ImportPanel
from jarvis.gui.views.video_editor.multitrack_view import MultiTrackView
from jarvis.gui.views.video_editor.music_panel import MusicPanel
from jarvis.gui.views.video_editor.text_overlay_panel import TextOverlayPanel
from jarvis.gui.views.video_editor.timeline_panel import TimelinePanel
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import (
    CancelableTaskResult,
    GenerationTaskResult,
    ProgressResult,
    run_cancelable_in_background,
    run_generation_in_background,
)
from jarvis.video_editor import db, media_import, multisource_export as mse, storage
from jarvis.video_editor.audio_mixing import AudioMixingError, MusicTrack, build_music_mix_filter
from jarvis.video_editor.captions import (
    CaptionError,
    CaptionLine,
    CaptionStyle,
    build_caption_filter,
    build_caption_filter_from_lines,
    generate_word_timings,
    group_words_into_lines,
)
from jarvis.video_editor.media_import import MediaImportError, MediaItem
from jarvis.video_editor.multisource_export import MultiSourceExportError
from jarvis.video_editor.storage import VideoEditorProject
from jarvis.video_editor.text_overlay import TextOverlay, TextOverlayError, build_text_overlay_filter
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

_QUEUE_POLL_INTERVAL_MS = 100
_RECENT_PROJECTS_LIMIT = 12

_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("clips", "🎬 Klipai"),
    ("animations", "✨ Animacijos"),
    ("transitions", "🔀 Perėjimai"),
    ("text", "📝 Tekstas ir subtitrai"),
    ("filters", "🎨 Filtrai ir spalvos"),
    ("music", "🎵 Muzika ir garsas"),
    ("format", "📐 Formatas ir apkarpymas"),
    ("export", "📤 Eksportas"),
)
# A real, functional grouping over the SAME panel instances this view
# already built and wired (ImportPanel/TimelinePanel/CaptionsPanel/
# TextOverlayPanel/MusicPanel/ExportPanel) - this is a visibility/
# navigation layer only, never a second copy of any control. Several
# categories ("Klipai"/"Animacijos"/"Perėjimai"/"Formatas ir
# apkarpymas") all point at the SAME TimelinePanel widget (its own
# per-row controls already separate trim/reorder, effect, transition,
# and aspect-ratio concerns - see that class's own
# _render_effect_controls()/_render_transition_controls() methods) -
# switching category never destroys/rebuilds TimelinePanel, it only
# shows/hides which already-built panel widgets are packed into the
# content area, so no state (a project's open Timeline, an in-progress
# edit) is ever lost by changing category.
_CATEGORY_PANEL_ATTRS: dict[str, tuple[str, ...]] = {
    "clips": ("_multitrack_view", "_import_panel", "_timeline_panel"),
    "animations": ("_timeline_panel",),
    "transitions": ("_timeline_panel",),
    "text": ("_captions_panel", "_text_overlay_panel"),
    "filters": ("_timeline_panel",),
    "music": ("_music_panel",),
    "format": ("_timeline_panel",),
    "export": ("_export_panel",),
}


class VideoEditorView(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None, navigate: Callable[[str], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._navigate = navigate
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._current_project: VideoEditorProject | None = None
        self._media_items: dict[str, MediaItem] = {}
        self._cancel_event: threading.Event | None = None
        self._caption_style: CaptionStyle | None = None
        self._caption_lines: list[CaptionLine] | None = None
        self._music_track: MusicTrack | None = None
        self._text_overlays: list[TextOverlay] = []
        self._pending_export_resolution_tier: str = "1080p"

        SectionHeader(self, "Video Editor").pack(
            anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM),
        )

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        if not ffmpeg_available():
            self._scroll = ctk.CTkScrollableFrame(body, fg_color="transparent")
            self._scroll.pack(fill="both", expand=True)
            self._render_ffmpeg_missing()
            self._poll_queue()
            return

        self._category_sidebar = ctk.CTkFrame(body, fg_color="transparent", width=200)
        self._category_sidebar.pack(side="left", fill="y", padx=(0, theme.SPACE_MD))
        self._category_sidebar.pack_propagate(False)
        self._category_buttons: dict[str, ctk.CTkButton] = {}
        self._active_category = "clips"
        for key, label in _CATEGORIES:
            button = ctk.CTkButton(
                self._category_sidebar, text=label, anchor="w", command=lambda k=key: self._on_category_selected(k),
            )
            button.pack(fill="x", pady=(0, theme.SPACE_XS))
            self._category_buttons[key] = button

        content = ctk.CTkFrame(body, fg_color="transparent")
        content.pack(side="left", fill="both", expand=True)

        self._scroll = ctk.CTkScrollableFrame(content, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True)

        self._build_project_bar()
        self._status_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._multitrack_view = MultiTrackView(self._scroll)

        self._import_panel = ImportPanel(
            self._scroll, on_files_chosen=self._on_files_chosen, on_add_to_timeline=self._on_add_to_timeline_clicked,
        )

        self._timeline_panel = TimelinePanel(
            self._scroll, on_timeline_changed=self._on_timeline_changed, get_thumbnail=self._get_thumbnail,
            on_preview_requested=self._on_effect_preview_requested,
        )

        self._captions_panel = CaptionsPanel(
            self._scroll, on_style_changed=self._on_caption_style_changed,
            on_generate_requested=self._on_generate_subtitles_requested,
            on_lines_changed=self._on_caption_lines_changed,
        )

        self._text_overlay_panel = TextOverlayPanel(self._scroll, on_overlays_changed=self._on_text_overlays_changed)

        self._music_panel = MusicPanel(
            self._scroll, on_file_chosen=self._on_music_file_chosen, on_track_changed=self._on_music_track_changed,
        )

        self._export_panel = ExportPanel(
            self._scroll, on_export_clicked=self._on_export_clicked, on_cancel_clicked=self._on_cancel_clicked,
        )

        self._recent_projects_container = ctk.CTkFrame(self._scroll, fg_color="transparent")

        self._set_project_controls_enabled(False)
        self._render_recent_projects()
        self._on_category_selected(self._active_category)
        self._poll_queue()

    def _on_category_selected(self, category: str) -> None:
        """Shows only the panel(s) jarvis.gui.views.video_editor
        .dashboard._CATEGORY_PANEL_ATTRS maps `category` to - never
        destroys/rebuilds a panel, only (re)packs the same already-
        constructed widget(s), so switching category can never lose an
        in-progress edit (see this module's own _CATEGORY_PANEL_ATTRS
        docstring). Recent Projects stays visible under "Klipai" only
        (its own natural home, next to Import/New Project), matching
        where it already lived before categories existed."""
        self._active_category = category
        for key, button in self._category_buttons.items():
            button.configure(fg_color=theme.ACCENT_PRIMARY if key == category else theme.BG_CARD)

        all_panels = (
            self._multitrack_view, self._import_panel, self._timeline_panel, self._captions_panel,
            self._text_overlay_panel, self._music_panel, self._export_panel,
        )
        for panel in all_panels:
            panel.pack_forget()
        self._recent_projects_container.pack_forget()

        for attr in _CATEGORY_PANEL_ATTRS.get(category, ()):
            getattr(self, attr).pack(fill="x", pady=(theme.SPACE_SM, 0))
        if category == "clips":
            self._recent_projects_container.pack(fill="x", pady=(theme.SPACE_LG, 0))

    def _render_ffmpeg_missing(self) -> None:
        card = Card(self._scroll)
        card.pack(fill="x")
        ctk.CTkLabel(
            card, text="⚠️ FFmpeg was not found on PATH. The Video Editor requires FFmpeg to be installed.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.DANGER, anchor="w", wraplength=600, justify="left",
        ).pack(padx=theme.SPACE_MD, pady=theme.SPACE_MD, anchor="w")

    # --- project lifecycle -----------------------------------------------------------------

    def _build_project_bar(self) -> None:
        row = ctk.CTkFrame(self._scroll, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkButton(row, text="➕ New Project", command=self._on_new_project_clicked, width=150).pack(side="left")
        self._project_name_label = ctk.CTkLabel(
            row, text="No project open",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self._project_name_label.pack(side="left", padx=(theme.SPACE_MD, 0))

    def _on_new_project_clicked(self) -> None:
        project = storage.create_project()
        db.create_project_record(project.project_id, f"Project {project.project_id[:8]}")
        self._open_project(project.project_id)

    def _open_project(self, project_id: str) -> None:
        record = db.get_project(project_id)
        if record is None:
            self._set_status(f"Project {project_id} could no longer be found.", kind="error")
            return
        self._current_project = storage.project_paths(project_id)
        timeline, media_items = storage.load_project(project_id)
        self._media_items = media_items
        self._timeline_panel.render(timeline or Timeline(), media_items)
        self._import_panel.render_media_list(media_items)
        self._project_name_label.configure(text=record.name)
        self._set_project_controls_enabled(True)
        self._clear_status()
        self._render_recent_projects()
        self._refresh_multitrack_view()

    def open_project(self, project_id: str | None) -> None:
        """Duck-typed hook jarvis.gui.app._navigate() calls optionally
        when navigating here with an explicit project id (e.g. from a
        "Recent Projects" entry elsewhere) - same optional-hook
        convention every other dashboard in this codebase already
        implements (see jarvis.gui.app._navigate()'s own docstring)."""
        if project_id is not None:
            self._open_project(project_id)

    def refresh(self) -> None:
        """Duck-typed hook called by jarvis.gui.app._navigate() every
        time this view is shown - re-renders the Recent Projects strip
        so a project created/exported elsewhere is reflected without
        needing to reopen the whole view."""
        self._render_recent_projects()

    def _set_project_controls_enabled(self, enabled: bool) -> None:
        """ImportPanel/TimelinePanel/ExportPanel are plain CTkFrames -
        CTkFrame itself has no `state=` option to bulk-toggle its own
        children (unlike a single CTkButton/CTkEntry), so this
        deliberately does NOT attempt a visual disable of the whole
        panel tree. The REAL guard is the `self._current_project is
        None` check already present in every click handler below
        (_on_files_chosen/_on_export_clicked/_on_timeline_changed) - no
        control can actually do anything without an open project,
        regardless of this method's own (currently absent) visual
        styling. Kept as an explicit, named method (rather than inlining
        nothing at every call site) so a future pass can add real
        per-widget enable/disable without hunting for call sites."""

    # --- media import ------------------------------------------------------------------------

    def _on_files_chosen(self, paths: list[Path]) -> None:
        if self._current_project is None:
            self._set_status("Create or open a project first.", kind="error")
            return
        project = self._current_project
        self._set_status(f"Importing {len(paths)} file(s)...", kind="loading")
        run_generation_in_background(
            lambda: _import_files(project, paths), self._result_queue, source="import",
        )

    def _handle_import_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._set_status(f"Import failed: {result.error}", kind="error")
            return
        new_items, errors = result.value
        for item in new_items:
            self._media_items[item.media_item_id] = item
        self._import_panel.render_media_list(self._media_items)
        if self._current_project is not None:
            storage.save_project(self._current_project.project_id, self._timeline_panel.timeline, self._media_items)
        if errors:
            self._set_status(f"{len(new_items)} imported, {len(errors)} failed: {errors[0]}", kind="error")
        else:
            self._clear_status()

    def _on_add_to_timeline_clicked(self, media: MediaItem) -> None:
        """ImportPanel's own "➕ Add to Timeline" button, per imported
        item - appends `media` to the end of the current timeline via
        TimelinePanel.add_clip(), which itself calls
        self._on_timeline_changed() (saving the project) as part of its
        own contract - this handler doesn't need to save anything
        itself."""
        self._timeline_panel.add_clip(media)

    # --- timeline ----------------------------------------------------------------------------

    def _on_timeline_changed(self, timeline: Timeline) -> None:
        if self._current_project is None:
            return
        storage.save_project(self._current_project.project_id, timeline, self._media_items)
        self._refresh_multitrack_view()

    def _refresh_multitrack_view(self) -> None:
        """Recomputes every track's own segments from state this
        dashboard already holds (Timeline items, MusicTrack,
        CaptionLines, TextOverlays) and redraws MultiTrackView - a pure
        read-only visualization, never itself a source of truth (see
        that widget's own docstring). Called after every action that
        changes any of those four pieces of state."""
        timeline = self._timeline_panel.timeline
        total_duration = timeline.total_duration_seconds()

        video_segments: list[tuple[float, float, str]] = []
        cursor = 0.0
        for index, item in enumerate(timeline.items):
            duration = item.on_screen_duration_seconds
            video_segments.append((cursor, cursor + duration, str(index + 1)))
            cursor += duration

        music_segment = None
        if self._music_track is not None:
            music_segment = (0.0, total_duration)

        caption_segments: list[tuple[float, float]] = []
        if self._caption_lines:
            caption_segments = [(line.start_seconds, line.end_seconds) for line in self._caption_lines]

        text_segments = [(overlay.start_seconds, overlay.end_seconds) for overlay in self._text_overlays]

        self._multitrack_view.render(
            video_segments=video_segments, music_segment=music_segment,
            caption_segments=caption_segments, text_segments=text_segments,
            total_duration_seconds=total_duration,
        )

    def _get_thumbnail(self, media: MediaItem) -> Path | None:
        """Returns a REAL, cached thumbnail path for `media` - extracted
        once (via ffmpeg_utils.extract_frame() for video, Pillow for a
        photo) and reused on every later render. Returns None (never
        raises) on any failure - a missing thumbnail is cosmetic, see
        TimelinePanel's own docstring."""
        if self._current_project is None:
            return None
        thumb_path = self._current_project.media_dir / f"{media.media_item_id}_thumb.jpg"
        if thumb_path.is_file():
            return thumb_path
        try:
            if media.kind == "video":
                from jarvis.video_studio.ffmpeg_utils import FFmpegError, extract_frame

                extract_frame(media.stored_path, timestamp_seconds=0.0, output_path=thumb_path)
            else:
                from PIL import Image

                with Image.open(media.stored_path) as image:
                    image.convert("RGB").save(thumb_path, "JPEG")
            return thumb_path if thumb_path.is_file() else None
        except Exception:
            return None

    # --- animation preview -------------------------------------------------------------------

    def _on_effect_preview_requested(self, index: int) -> None:
        """Renders a REAL, short preview clip for the timeline item at
        `index` and extracts 3 real frames (start/mid/end) showing its
        own configured zoom/pan/fade progress - a possibly-slow ffmpeg
        call, so it runs through run_generation_in_background() (the
        same convention import/captions already use), never blocking
        the GUI thread. `source=("effect_preview", index)` threads the
        item's own index through to _handle_result() below, so the
        right row's own preview_container gets the result back even if
        several previews are requested in quick succession."""
        if self._current_project is None:
            return
        timeline = self._timeline_panel.timeline
        if not (0 <= index < len(timeline.items)):
            return
        item = timeline.items[index]
        media = self._media_items.get(item.media_item_id)
        if media is None:
            self._timeline_panel.show_preview_error(index, "Media file not found for this item.")
            return

        from jarvis.video_editor.effects import render_effect_preview

        is_still = isinstance(item, TimelineStill)
        preview_dir = self._current_project.media_dir / "previews"
        duration = item.on_screen_duration_seconds
        width, height = (360, 640) if timeline.aspect_ratio == "9:16" else (640, 640) if timeline.aspect_ratio == "1:1" else (640, 360)

        run_generation_in_background(
            lambda: render_effect_preview(
                media.stored_path, spec=item.effect, width=width, height=height,
                duration_seconds=duration, is_still=is_still, output_dir=preview_dir,
            ),
            self._result_queue, source=("effect_preview", index),
        )

    def _handle_effect_preview_result(self, index: int, result: GenerationTaskResult) -> None:
        if result.error:
            self._timeline_panel.show_preview_error(index, result.error)
            return
        self._timeline_panel.show_preview_frames(index, result.value)

    # --- captions (Stage 4) ----------------------------------------------------------------

    def _on_caption_style_changed(self, style: CaptionStyle | None) -> None:
        self._caption_style = style
        # A style change invalidates any already-edited lines (they
        # were grouped/reviewed under the PREVIOUS style's own
        # animation choice) - cleared so export falls back to the
        # existing auto-transcribe-at-export-time path rather than
        # silently reusing stale, possibly-mismatched edited lines.
        self._caption_lines = None
        self._refresh_multitrack_view()

    def _on_generate_subtitles_requested(self) -> None:
        """"📝 Generate & Edit Subtitles" button - transcribes the
        first clip's own speech NOW (not at export time, unlike the
        existing auto-transcribe path still used when the person never
        clicks this button) so the person can review/edit each line's
        own text/timing before export. A real, possibly-slow
        transcription, run through the same run_generation_in_background()
        convention as every other background call in this dashboard."""
        if self._current_project is None:
            self._set_status("Create or open a project first.", kind="error")
            return
        timeline = self._timeline_panel.timeline
        first_clip_media = self._first_clip_media_item(timeline)
        if first_clip_media is None:
            self._set_status(
                "Subtitles need at least one video clip (with speech) as the timeline's first item.",
                kind="error",
            )
            return
        self._captions_panel.set_generating_state(generating=True)
        run_generation_in_background(
            lambda: generate_word_timings(first_clip_media.stored_path),
            self._result_queue, source="subtitle_generation",
        )

    def _handle_subtitle_generation_result(self, result: GenerationTaskResult) -> None:
        self._captions_panel.set_generating_state(generating=False)
        if result.error:
            self._captions_panel.set_generate_error(result.error)
            return
        lines = group_words_into_lines(result.value)
        self._captions_panel.set_lines(lines)
        self._caption_lines = lines
        self._refresh_multitrack_view()

    def _on_caption_lines_changed(self, lines: list[CaptionLine]) -> None:
        self._caption_lines = lines
        self._refresh_multitrack_view()

    # --- text overlays -----------------------------------------------------------------------

    def _on_text_overlays_changed(self, overlays: list[TextOverlay]) -> None:
        self._text_overlays = overlays
        self._refresh_multitrack_view()

    # --- music (Stage 4) -------------------------------------------------------------------

    def _on_music_file_chosen(self, path: Path) -> None:
        if self._current_project is None:
            self._set_status("Create or open a project first.", kind="error")
            return
        try:
            stored_path = storage.copy_media_into_project(self._current_project, path)
        except storage.VideoEditorStorageError as e:
            self._set_status(f"Couldn't add music: {e}", kind="error")
            return
        self._music_panel.set_imported_track(stored_path, duration_seconds=None)

    def _on_music_track_changed(self, track: MusicTrack | None) -> None:
        self._music_track = track
        self._refresh_multitrack_view()

    # --- export --------------------------------------------------------------------------------

    def _on_export_clicked(self, resolution_tier: str) -> None:
        if self._current_project is None:
            self._set_status("Create or open a project first.", kind="error")
            return
        timeline = self._timeline_panel.timeline
        problems = timeline.validate()
        if problems:
            self._set_status(f"Fix the timeline before exporting: {problems[0]}", kind="error")
            return
        try:
            mse.resolve_export_format(timeline.aspect_ratio, resolution_tier)
        except MultiSourceExportError as e:
            self._set_status(str(e), kind="error")
            return

        if self._caption_style is not None and self._caption_lines is not None:
            # The person already generated AND reviewed/edited the
            # subtitle lines via "📝 Generate & Edit Subtitles" (see
            # _on_generate_subtitles_requested()) - export uses those
            # exact, possibly hand-edited lines directly, with NO
            # re-transcription at export time (re-transcribing here
            # would silently discard whatever text/timing edits the
            # person just made).
            try:
                caption_filter = build_caption_filter_from_lines(self._caption_lines, self._caption_style)
            except CaptionError as e:
                self._set_status(f"Couldn't build captions: {e}", kind="error")
                return
            self._start_export(resolution_tier, caption_filter=caption_filter)
            return

        if self._caption_style is not None:
            # Real, possibly-slow speech transcription - run through the
            # existing run_generation_in_background() (an LLM-free, pure
            # ffmpeg/whisper call, not an ffmpeg EXPORT subprocess, so
            # the plain fire-and-forget worker is the right fit, not
            # run_cancelable_in_background() - see jarvis.gui.worker's
            # own docstring for why these two are separate mechanisms).
            # The actual export is started from
            # _handle_caption_generation_result() below once real word
            # timings come back, never before.
            first_clip_media = self._first_clip_media_item(timeline)
            if first_clip_media is None:
                self._set_status(
                    "Captions need at least one video clip (with speech) as the timeline's first item.",
                    kind="error",
                )
                return
            # Disabled here, not just once the real export subprocess
            # starts in _start_export() - this transcription step can
            # take many seconds, and leaving the button enabled let a
            # second click here race a second export against this same
            # pending one (both eventually landing on the SAME
            # time.time()-based output filename in _start_export() -
            # a real, hand-hit collision that corrupted the resulting
            # MP4's container when two ffmpeg processes wrote the same
            # file at once). Re-enabled on every error branch below and
            # inside _handle_caption_transcription_result() so a failed
            # transcription never leaves Export stuck disabled.
            self._export_panel.set_exporting_state(exporting=True)
            self._set_status("Transcribing speech for captions...", kind="loading")
            run_generation_in_background(
                lambda: generate_word_timings(first_clip_media.stored_path),
                self._result_queue, source="caption_transcription",
            )
            self._pending_export_resolution_tier = resolution_tier
            return

        self._start_export(resolution_tier, caption_filter=None)

    def _first_clip_media_item(self, timeline: Timeline) -> MediaItem | None:
        for item in timeline.items:
            if isinstance(item, TimelineClip):
                media = self._media_items.get(item.media_item_id)
                if media is not None:
                    return media
        return None

    def _handle_caption_transcription_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._export_panel.set_exporting_state(exporting=False)
            self._set_status(f"Captions failed: {result.error}", kind="error")
            return
        words = result.value
        timeline = self._timeline_panel.timeline
        try:
            caption_filter = build_caption_filter(words, self._caption_style or CaptionStyle())
        except Exception as e:  # pragma: no cover - build_caption_filter() itself never raises for valid input
            self._export_panel.set_exporting_state(exporting=False)
            self._set_status(f"Couldn't build captions: {e}", kind="error")
            return
        self._clear_status()
        self._start_export(self._pending_export_resolution_tier, caption_filter=caption_filter)

    def _start_export(self, resolution_tier: str, *, caption_filter: str | None) -> None:
        if self._current_project is None:
            return
        timeline = self._timeline_panel.timeline
        try:
            export_format = mse.resolve_export_format(timeline.aspect_ratio, resolution_tier)
        except MultiSourceExportError as e:
            self._export_panel.set_exporting_state(exporting=False)
            self._set_status(str(e), kind="error")
            return

        text_overlay_filter = None
        if self._text_overlays:
            # video_label must read from whichever label the PREVIOUS
            # stage actually produced - "capv" if captions are also
            # enabled (export_timeline() applies caption_filter first),
            # otherwise the raw assembled timeline's own "outv" -
            # mirrors export_timeline()'s own documented stage order
            # exactly rather than assuming only one of the two is ever
            # used.
            overlay_video_label = "capv" if caption_filter is not None else "outv"
            try:
                text_overlay_filter = build_text_overlay_filter(
                    self._text_overlays, video_label=overlay_video_label,
                )
            except TextOverlayError as e:
                self._export_panel.set_exporting_state(exporting=False)
                self._set_status(f"Text overlay couldn't be added: {e}", kind="error")
                return

        audio_mix_filter = None
        if self._music_track is not None:
            try:
                input_args, _full_filter, _video_out, audio_out = mse.build_filtergraph(
                    timeline, self._media_items, export_format, cwd=self._current_project.exports_dir,
                )
                distinct_input_count = len({item.media_item_id for item in timeline.items})
                audio_mix_filter = build_music_mix_filter(
                    self._music_track, timeline_duration_seconds=timeline.total_duration_seconds(),
                    cwd=self._current_project.exports_dir, timeline_audio_input_count=distinct_input_count,
                    timeline_audio_label=audio_out,
                )
            except AudioMixingError as e:
                self._export_panel.set_exporting_state(exporting=False)
                self._set_status(f"Music couldn't be added: {e}", kind="error")
                return

        # uuid4, not time.time() - two exports started within the same
        # wall-clock second (e.g. a second click during the captions
        # transcription window above, before this function's own
        # set_exporting_state(exporting=True) below had run) previously
        # produced the SAME filename, so two ffmpeg processes wrote the
        # same output file concurrently and corrupted its MP4 container
        # (surfaced to the user as a bogus "unsupported codec" error on
        # playback). A uuid4 suffix makes every export's path unique
        # regardless of timing, closing the collision outright rather
        # than just narrowing its window.
        output_path = self._current_project.exports_dir / f"export_{uuid.uuid4().hex}.mp4"
        self._cancel_event = threading.Event()
        self._export_panel.set_exporting_state(exporting=True)
        db.save_export_format_last_used(self._current_project.project_id, export_format.label)
        run_cancelable_in_background(
            mse.export_timeline, self._result_queue, cancel_event=self._cancel_event, source="export",
            timeline=timeline, media_items=self._media_items, export_format=export_format, output_path=output_path,
            caption_filter=caption_filter, audio_mix_filter=audio_mix_filter, text_overlay_filter=text_overlay_filter,
        )

    def _on_cancel_clicked(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()

    def _handle_export_progress(self, result: ProgressResult) -> None:
        self._export_panel.update_progress(result.percent)

    def _handle_export_result(self, result: CancelableTaskResult) -> None:
        self._export_panel.set_exporting_state(exporting=False)
        self._cancel_event = None
        if result.cancelled:
            self._export_panel.show_cancelled()
        elif result.error:
            self._export_panel.show_error(result.error)
        else:
            self._export_panel.show_result(result.value)
            if self._current_project is not None:
                db.set_status(self._current_project.project_id, "exported")
            self._render_recent_projects()

    # --- recent projects -------------------------------------------------------------------

    def _render_recent_projects(self) -> None:
        for child in self._recent_projects_container.winfo_children():
            child.destroy()
        records = db.list_projects(limit=_RECENT_PROJECTS_LIMIT)
        if not records:
            return
        ctk.CTkLabel(
            self._recent_projects_container, text="📁 RECENT PROJECTS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        for record in records:
            card = Card(self._recent_projects_container)
            card.pack(fill="x", pady=(0, theme.SPACE_XS))
            button = ctk.CTkButton(
                card, text="", fg_color="transparent", hover_color=theme.BG_CARD_HOVER,
                command=lambda pid=record.id: self._open_project(pid), height=40,
            )
            button.place(relx=0, rely=0, relwidth=1, relheight=1)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_SM)
            ctk.CTkLabel(
                inner, text=f"{record.name} - {record.status}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w",
            ).pack(anchor="w")

    # --- status --------------------------------------------------------------------------------

    def _set_status(self, message: str, *, kind: str = "muted") -> None:
        from jarvis.gui.views.video_editor.common import status_label

        for child in self._status_container.winfo_children():
            child.destroy()
        status_label(self._status_container, message, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for child in self._status_container.winfo_children():
            child.destroy()

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
        if isinstance(result, GenerationTaskResult) and result.source == "import":
            self._handle_import_result(result)
        elif isinstance(result, GenerationTaskResult) and result.source == "caption_transcription":
            self._handle_caption_transcription_result(result)
        elif isinstance(result, GenerationTaskResult) and result.source == "subtitle_generation":
            self._handle_subtitle_generation_result(result)
        elif (
            isinstance(result, GenerationTaskResult) and isinstance(result.source, tuple)
            and len(result.source) == 2 and result.source[0] == "effect_preview"
        ):
            self._handle_effect_preview_result(result.source[1], result)
        elif isinstance(result, ProgressResult) and result.source == "export":
            self._handle_export_progress(result)
        elif isinstance(result, CancelableTaskResult) and result.source == "export":
            self._handle_export_result(result)


def _import_files(project: VideoEditorProject, paths: list[Path]) -> tuple[list[MediaItem], list[str]]:
    """Runs on a background thread. Returns (successfully-imported
    items, error messages for any that failed) - one failure never
    blocks the others, same "one failure doesn't abort the batch"
    convention this codebase's other batch-import/batch-generate
    functions already establish (e.g. jarvis.reel_generator.scenes
    .render_all_scenes())."""
    import uuid

    items: list[MediaItem] = []
    errors: list[str] = []
    for path in paths:
        try:
            item = media_import.import_media(path, project, media_item_id=uuid.uuid4().hex)
            items.append(item)
        except MediaImportError as e:
            errors.append(f"{path.name}: {e}")
    return items, errors
