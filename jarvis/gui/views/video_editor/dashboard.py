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

import contextlib
import dataclasses
import queue
import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.video_editor.ai_assistant_panel import AiAssistantPanel
from jarvis.gui.views.video_editor.captions_panel import CaptionsPanel
from jarvis.gui.views.video_editor.element_inspector_panel import ElementInspectorPanel
from jarvis.gui.views.video_editor.export_panel import ExportPanel
from jarvis.gui.views.video_editor.import_panel import ImportPanel
from jarvis.gui.views.video_editor.interactive_preview_panel import InteractivePreviewPanel
from jarvis.gui.views.video_editor.looks_panel import FiltersPanel, TransitionsPanel
from jarvis.gui.views.video_editor.music_panel import MusicPanel
from jarvis.gui.views.video_editor.reel_templates_panel import ReelTemplatesPanel
from jarvis.gui.views.video_editor.stickers_panel import StickersPanel
from jarvis.gui.views.video_editor.speech_sync_panel import SpeechSyncPanel
from jarvis.gui.views.video_editor.text_overlay_panel import TextOverlayPanel
from jarvis.gui.views.video_editor.timeline_panel import TimelinePanel
from jarvis.gui.views.video_editor.track_timeline_panel import TrackTimelinePanel
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import (
    CancelableTaskResult,
    GenerationTaskResult,
    ProgressResult,
    run_cancelable_in_background,
    run_generation_in_background,
)
from jarvis.video_editor import db, media_import, multisource_export as mse, playback, storage
from jarvis.video_editor import preview_compositor as pc
from jarvis.video_editor import track_layout
from jarvis.video_editor.audio_mixing import AudioMixingError, MusicTrack, build_music_mix_filter
from jarvis.video_editor.captions import (
    CaptionError,
    CaptionLine,
    CaptionStyle,
    build_caption_filter,
    build_caption_filter_from_lines,
    export_srt,
    generate_word_timings,
    group_words_into_lines,
)
from jarvis.video_editor.ai_assistant import AiReelProposal, propose_reel_style
from jarvis.video_editor.editor_state import EditorHistory, EditorState
from jarvis.video_editor.live_preview import LivePreviewError, PreviewFilters, render_preview_frame
from jarvis.video_editor.media_import import MediaImportError, MediaItem
from jarvis.video_editor.multisource_export import MultiSourceExportError
from jarvis.video_editor.reel_templates import ReelTemplate, apply_template
from jarvis.video_editor.storage import VideoEditorProject
from jarvis.video_editor.stickers import StickerError, StickerInstance, build_sticker_filter
from jarvis.video_editor.text_overlay import (
    TextOverlay,
    TextOverlayError,
    build_rotated_text_filters,
    build_text_overlay_filter,
    text_scale_for,
)
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, apply_effect_to_every_item
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

_QUEUE_POLL_INTERVAL_MS = 30
# 30 ms (was 100): background results include the live preview's
# decoded frames while scrubbing, and every poll interval of delay is
# visible lag there. Draining an empty queue costs nothing.
_RECENT_PROJECTS_LIMIT = 12
_EXACT_FRAME_DELAY_MS = 900
# How long the preview must sit still (paused, no edits) before the
# exact export frame is rendered with ffmpeg - long enough that
# scrubbing or dragging never queues an export render per mouse event.
_BASE_FRAME_DEBOUNCE_MS = 40
_PLAYBACK_TICK_MS = 15
_OVERLAY_SAVE_DELAY_MS = 400
_PREVIEW_AUDIO_DELAY_MS = 600
_PREVIEW_CANVAS_TIER = "1080p"
_LIBRARY_WIDTH = 440
_INSPECTOR_WIDTH = 290
_TRACKS_HEIGHT = 250
# Overlay sizes are 1080p pixels (text_overlay.REFERENCE_SHORT_SIDE_PX),
# so the preview measures everything against the 1080p canvas.

_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("clips", "🎬 Klipai"),
    ("animations", "✨ Animacijos"),
    ("stickers", "🎉 Lipdukai"),
    ("transitions", "🔀 Perėjimai"),
    ("text", "📝 Tekstas"),
    ("filters", "🎨 Filtrai"),
    ("music", "🎵 Muzika"),
    ("format", "📐 Formatas"),
    ("templates", "🎞️ Šablonai"),
    ("ai_assistant", "🤖 AI"),
    ("export", "📤 Eksportas"),
)
# Short labels: the categories are now a narrow strip at the left edge
# of the library column ("Lipdukai" = stickers, GIFs and emoji; "Tekstas"
# = text and subtitles; "Filtrai" = filters and colors).
# "Lipdukai, GIF ir Emoji" covers both the user's own separate
# "Lipdukai ir GIF" and "Emoji" category requests - a sticker IS the
# generalized mechanism an emoji would also use (a small transparent
# image composited via overlay with the same animation/position
# controls), so a dedicated empty "Emoji" category with its own
# duplicate mechanism was judged unnecessary rather than genuinely
# useful; any emoji the person wants can be uploaded as a PNG/GIF
# sticker here exactly like any other custom sticker.
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
    "clips": ("_import_panel", "_timeline_panel"),
    "animations": ("_timeline_panel",),
    "stickers": ("_stickers_panel",),
    "transitions": ("_transitions_panel", "_timeline_panel"),
    "text": ("_captions_panel", "_text_overlay_panel", "_speech_sync_panel"),
    "filters": ("_filters_panel", "_timeline_panel"),
    "music": ("_music_panel",),
    "format": ("_timeline_panel",),
    "templates": ("_reel_templates_panel",),
    "ai_assistant": ("_ai_assistant_panel",),
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
        self._last_transcribed_words: list = []
        self._music_track: MusicTrack | None = None
        self._text_overlays: list[TextOverlay] = []
        self._stickers: list[StickerInstance] = []
        self._pending_export_resolution_tier: str = "1080p"
        self._live_preview_render_after_id: str | None = None
        self._preview_request_token: int = 0
        self._last_exact_frame_path: Path | None = None
        self._engine: playback.PlaybackEngine | None = None
        self._audio_player = playback.AudioPlayer()
        self._playback_after_id: str | None = None
        self._base_frame_after_id: str | None = None
        self._base_frame_token = 0
        self._overlay_save_after_id: str | None = None
        self._audio_after_id: str | None = None
        self._audio_token = 0
        self._panel_sync_originals: dict[tuple[str, int], object] = {}
        self._history = EditorHistory()
        self._applying_state = False
        self._history_batch_depth = 0
        self._selection: tuple[str, int] | None = None
        self.bind("<Destroy>", self._on_destroy, add="+")

        if not ffmpeg_available():
            SectionHeader(self, "Video Editor").pack(
                anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM),
            )
            body = ctk.CTkFrame(self, fg_color="transparent")
            body.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))
            self._scroll = ctk.CTkScrollableFrame(body, fg_color="transparent")
            self._scroll.pack(fill="both", expand=True)
            self._render_ffmpeg_missing()
            self._poll_queue()
            return

        # Layout (top to bottom): project bar with undo/redo and save
        # state; then three resizable columns - the library of tools on
        # the left, the live preview in the center, the selected
        # element's settings on the right; and the multi-track timeline
        # across the bottom. The dividers between them can be dragged.
        self._build_project_bar()
        self._status_container = ctk.CTkFrame(self, fg_color="transparent", height=1)
        # Packed only while there is a message (see _set_status()).

        self._vertical_panes = tk.PanedWindow(
            self, orient="vertical", sashwidth=6, bd=0, bg=theme.BG_PRIMARY, sashrelief="flat",
        )
        self._vertical_panes.pack(fill="both", expand=True, padx=theme.SPACE_SM, pady=(0, theme.SPACE_SM))
        self._columns = tk.PanedWindow(
            self._vertical_panes, orient="horizontal", sashwidth=6, bd=0, bg=theme.BG_PRIMARY, sashrelief="flat",
        )

        library = ctk.CTkFrame(self._columns, fg_color="transparent")
        self._category_sidebar = ctk.CTkFrame(library, fg_color="transparent", width=118)
        self._category_sidebar.pack(side="left", fill="y", padx=(0, theme.SPACE_XS))
        self._category_sidebar.pack_propagate(False)
        self._category_buttons: dict[str, ctk.CTkButton] = {}
        self._active_category = "clips"
        for key, label in _CATEGORIES:
            button = ctk.CTkButton(
                self._category_sidebar, text=label, anchor="w", height=30,
                command=lambda k=key: self._on_category_selected(k),
            )
            button.pack(fill="x", pady=(0, theme.SPACE_XS))
            self._category_buttons[key] = button
        self._scroll = ctk.CTkScrollableFrame(library, fg_color="transparent")
        self._scroll.pack(side="left", fill="both", expand=True)

        center = ctk.CTkFrame(self._columns, fg_color="transparent")
        self._preview_panel = InteractivePreviewPanel(
            center, on_play_toggled=self._on_play_toggled, on_seek=self._on_preview_seek,
            on_selection_changed=self._on_preview_selection_changed, on_element_edited=self._on_element_edited,
            on_delete_requested=self._on_element_delete_requested,
        )
        self._preview_panel.pack(fill="both", expand=True)

        right = ctk.CTkFrame(self._columns, fg_color="transparent")
        inspector_column = ctk.CTkScrollableFrame(right, fg_color="transparent")
        inspector_column.pack(fill="both", expand=True)
        self._inspector_panel = ElementInspectorPanel(
            inspector_column, on_element_edited=self._on_element_edited,
            on_delete_requested=self._on_element_delete_requested,
            on_duplicate_requested=self._on_element_duplicate_requested,
        )
        self._inspector_panel.pack(fill="both", expand=True)
        add_row = ctk.CTkFrame(inspector_column, fg_color="transparent")
        add_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(add_row, text="➕ Tekstas", width=110, command=self._on_quick_add_text).pack(
            side="left", padx=(0, theme.SPACE_XS),
        )
        ctk.CTkButton(add_row, text="➕ Lipdukas", width=110, command=self._on_quick_add_sticker).pack(side="left")

        self._columns.add(library, minsize=300, width=_LIBRARY_WIDTH, stretch="never")
        self._columns.add(center, minsize=240, stretch="always")
        self._columns.add(right, minsize=220, width=_INSPECTOR_WIDTH, stretch="never")

        self._track_timeline = TrackTimelinePanel(
            self._vertical_panes, on_seek=self._on_preview_seek, on_selection_changed=self._on_track_selection_changed,
            on_state_edited=self._on_track_state_edited,
        )
        self._columns_user_sized = False
        self._columns.bind("<Configure>", self._on_columns_configured, add="+")
        self._columns.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_columns_user_sized", True), add="+")
        self._vertical_panes.add(self._columns, minsize=320, stretch="always")
        self._vertical_panes.add(self._track_timeline, minsize=170, height=_TRACKS_HEIGHT, stretch="never")

        self._import_panel = ImportPanel(
            self._scroll, on_files_chosen=self._on_files_chosen, on_add_to_timeline=self._on_add_to_timeline_clicked,
        )

        self._timeline_panel = TimelinePanel(
            self._scroll, on_timeline_changed=self._on_timeline_changed, get_thumbnail=self._get_thumbnail,
            on_preview_requested=self._on_effect_preview_requested,
            on_undo_requested=self._on_undo, on_redo_requested=self._on_redo,
        )

        self._filters_panel = FiltersPanel(
            self._scroll, on_look_chosen=self._on_look_chosen, on_intensity_changed=self._on_look_intensity_changed,
            on_apply_all=self._on_look_apply_all, render_previews=playback.render_look_previews,
        )
        self._transitions_panel = TransitionsPanel(
            self._scroll, on_transition_chosen=self._on_transition_chosen,
            on_duration_changed=self._on_transition_duration_changed, on_apply_all=self._on_transition_apply_all,
        )

        self._captions_panel = CaptionsPanel(
            self._scroll, on_style_changed=self._on_caption_style_changed,
            on_generate_requested=self._on_generate_subtitles_requested,
            on_lines_changed=self._on_caption_lines_changed,
            on_export_srt_requested=self._on_export_srt_requested,
        )

        self._text_overlay_panel = TextOverlayPanel(self._scroll, on_overlays_changed=self._on_text_overlays_changed)

        self._speech_sync_panel = SpeechSyncPanel(self._scroll, get_words=lambda: self._last_transcribed_words)

        self._stickers_panel = StickersPanel(
            self._scroll, on_stickers_changed=self._on_stickers_changed, on_shape_dragged=self._on_sticker_dragged,
        )

        self._music_panel = MusicPanel(
            self._scroll, on_file_chosen=self._on_music_file_chosen, on_track_changed=self._on_music_track_changed,
            on_analyze_rhythm_requested=self._on_analyze_rhythm_requested,
        )

        self._reel_templates_panel = ReelTemplatesPanel(self._scroll, on_template_applied=self._on_apply_reel_template)

        self._ai_assistant_panel = AiAssistantPanel(
            self._scroll, on_propose_requested=self._on_ai_propose_requested,
            on_apply_requested=self._on_apply_ai_proposal,
        )

        self._export_panel = ExportPanel(
            self._scroll, on_export_clicked=self._on_export_clicked, on_cancel_clicked=self._on_cancel_clicked,
        )

        self._recent_projects_container = ctk.CTkFrame(self._scroll, fg_color="transparent")

        self._set_project_controls_enabled(False)
        self._render_recent_projects()
        self._on_category_selected(self._active_category)
        self._update_history_controls()
        self._bind_shortcuts()
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
            self._import_panel, self._filters_panel, self._transitions_panel, self._timeline_panel, self._captions_panel,
            self._text_overlay_panel, self._speech_sync_panel, self._stickers_panel, self._music_panel,
            self._reel_templates_panel, self._ai_assistant_panel, self._export_panel,
        )
        for panel in all_panels:
            panel.pack_forget()
        self._recent_projects_container.pack_forget()

        for attr in _CATEGORY_PANEL_ATTRS.get(category, ()):
            getattr(self, attr).pack(fill="x", pady=(theme.SPACE_SM, 0))
        if category == "clips":
            self._recent_projects_container.pack(fill="x", pady=(theme.SPACE_LG, 0))
        # Start each tool at its top - a scroll position left over from a
        # longer tool would otherwise show an empty area.
        self._scroll._parent_canvas.yview_moveto(0.0)

    def _on_columns_configured(self, event) -> None:
        """Splits the width between the columns: the library gets about
        a third (its tools are wide), the settings a fixed column, the
        preview the rest - until the person drags a divider, after which
        the dividers stay where they put them."""
        if self._columns_user_sized or event.width < 600:
            return
        library = max(_LIBRARY_WIDTH, min(640, round(event.width * 0.38)))
        self._columns.sash_place(0, library, 0)
        self._columns.sash_place(1, event.width - _INSPECTOR_WIDTH, 0)

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
        row = ctk.CTkFrame(self, fg_color=theme.BG_SURFACE, corner_radius=0)
        row.pack(fill="x", pady=(0, theme.SPACE_SM))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_SM)
        ctk.CTkLabel(
            inner, text="🎛️ Video Editor",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_SUBTITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY,
        ).pack(side="left", padx=(0, theme.SPACE_MD))
        ctk.CTkButton(inner, text="➕ Naujas projektas", command=self._on_new_project_clicked, width=150).pack(
            side="left",
        )
        self._project_name_label = ctk.CTkLabel(
            inner, text="Projektas neatidarytas",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self._project_name_label.pack(side="left", padx=(theme.SPACE_MD, 0))

        button_style = dict(
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        )
        ctk.CTkButton(
            inner, text="📤 Eksportuoti", width=120, command=lambda: self._on_category_selected("export"),
        ).pack(side="right")
        self._save_label = ctk.CTkLabel(
            inner, text="", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED,
        )
        self._save_label.pack(side="right", padx=theme.SPACE_MD)
        self._redo_button = ctk.CTkButton(
            inner, text="↪ Grąžinti", width=100, command=self._on_redo, **button_style,
        )
        self._redo_button.pack(side="right", padx=(theme.SPACE_XS, 0))
        self._undo_button = ctk.CTkButton(
            inner, text="↩ Atšaukti", width=100, command=self._on_undo, **button_style,
        )
        self._undo_button.pack(side="right")

    def _on_new_project_clicked(self) -> None:
        project = storage.create_project()
        db.create_project_record(project.project_id, f"Project {project.project_id[:8]}")
        self._open_project(project.project_id)

    def _open_project(self, project_id: str) -> None:
        record = db.get_project(project_id)
        if record is None:
            self._set_status(f"Project {project_id} could no longer be found.", kind="error")
            return
        self._stop_playback()
        self._current_project = storage.project_paths(project_id)
        timeline, media_items = storage.load_project(project_id)
        self._media_items = media_items
        self._timeline_panel.render(timeline or Timeline(), media_items)
        self._import_panel.render_media_list(media_items)
        self._restore_overlays(storage.load_overlays(project_id))
        self._project_name_label.configure(text=record.name, text_color=theme.TEXT_PRIMARY)
        self._set_project_controls_enabled(True)
        self._clear_status()
        self._render_recent_projects()
        self._set_selection(None)
        self._history.reset(self._editor_state())
        self._update_history_controls()
        self._refresh_track_timeline()
        self._on_timeline_for_preview_changed()
        self._save_label.configure(text="💾 Išsaugoma automatiškai")

    def _restore_overlays(self, overlays: storage.ProjectOverlays) -> None:
        """Puts a reopened project's saved text/stickers/captions/music
        back into the dashboard AND into their panels - before the
        overlays_data column existed, none of this survived a reopen.
        Also clears whatever the previously open project left behind."""
        self._text_overlays = list(overlays.text_overlays)
        self._text_overlay_panel.set_overlays(self._text_overlays)
        self._stickers = list(overlays.stickers)
        self._stickers_panel.set_stickers(self._stickers)
        self._panel_sync_originals.clear()

        self._captions_panel.reset()
        self._caption_style = None
        self._caption_lines = None
        if overlays.caption_style is not None:
            self._captions_panel.apply_style(overlays.caption_style)  # emits -> _on_caption_style_changed
            self._caption_style = overlays.caption_style
        if overlays.caption_lines is not None:
            self._captions_panel.set_lines(list(overlays.caption_lines))
            self._caption_lines = list(overlays.caption_lines)

        track = overlays.music_track
        if track is not None and track.source_path.is_file():
            self._music_panel.set_imported_track(track.source_path, duration_seconds=None, track=track)
        else:
            self._music_panel.clear_track()
            self._music_track = None

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
        self._mark_saved()
        self._record_history("Laiko juosta")
        self._refresh_track_timeline()
        self._sync_inspector_with_selection()
        self._on_timeline_for_preview_changed()

    def _refresh_track_timeline(self) -> None:
        """Redraws the track timeline from the current state."""
        self._track_timeline.render(self._editor_state(), self._media_items)

    # --- live preview: playback ------------------------------------------------------------------

    def _preview_formats(self) -> tuple[mse.ExportFormat, tuple[int, int]]:
        """(the 1080p export canvas overlays are measured against, the
        decoded preview frame size) for the current aspect ratio."""
        canvas = mse.resolve_export_format(self._timeline_panel.timeline.aspect_ratio, _PREVIEW_CANVAS_TIER)
        return canvas, playback.preview_size(canvas)

    def _on_timeline_for_preview_changed(self) -> None:
        """The timeline (clips, trims, effects, aspect ratio) changed -
        re-point playback at it, redraw the paused frame, and rebuild
        the preview audio."""
        if self._current_project is None:
            return
        timeline = self._timeline_panel.timeline
        canvas, frame_size = self._preview_formats()
        self._preview_panel.configure_canvas(frame_size=frame_size, canvas_size=(canvas.width, canvas.height))
        if self._engine is None or self._engine.frame_size != frame_size:
            if self._engine is not None:
                self._engine.close()
            self._engine = playback.PlaybackEngine(
                timeline, self._media_items, frame_size=frame_size, audio=self._audio_player,
            )
        else:
            self._engine.set_timeline(timeline, self._media_items)
        self._show_time(self._engine.position, self._engine.duration)
        self._preview_panel.update_scene(self._current_scene())
        self._request_preview_audio()
        if self._engine.playing:
            self._schedule_playback_tick()
        else:
            self._request_base_frame()

    def _show_time(self, t: float, duration: float, *, playing: bool = False) -> None:
        self._preview_panel.set_time(t, duration)
        self._track_timeline.set_playhead(t, follow=playing)

    def _on_play_toggled(self) -> None:
        engine = self._engine
        if engine is None or engine.duration <= 0:
            return
        engine.toggle()
        self._preview_panel.set_playing(engine.playing)
        if engine.playing:
            self._cancel_exact_frame()
            if not self._audio_player.available:
                self._preview_panel.show_error("Garso nėra: nerastas ffplay (įdiekite pilną FFmpeg paketą).")
            self._schedule_playback_tick()
        else:
            self._request_base_frame()

    def _schedule_playback_tick(self) -> None:
        if self._playback_after_id is None:
            self._playback_after_id = self.after(_PLAYBACK_TICK_MS, self._playback_tick)

    def _playback_tick(self) -> None:
        self._playback_after_id = None
        engine = self._engine
        if engine is None:
            return
        frame = engine.tick()
        if frame is not None:
            self._preview_panel.show_base(frame, engine.position)
        self._show_time(engine.position, engine.duration, playing=engine.playing)
        if engine.playing:
            self._schedule_playback_tick()
        else:  # reached the end
            self._preview_panel.set_playing(False)
            self._request_base_frame()

    def _stop_playback(self) -> None:
        if self._engine is not None:
            self._engine.pause()
            self._preview_panel.set_playing(False)
        if self._playback_after_id is not None:
            self.after_cancel(self._playback_after_id)
            self._playback_after_id = None

    def _on_preview_seek(self, t: float) -> None:
        engine = self._engine
        if engine is None:
            return
        engine.seek(t)
        self._show_time(engine.position, engine.duration, playing=engine.playing)
        if self._selection is None or self._selection[0] != "clip":
            self._refresh_look_panels()
        if not engine.playing:
            self._cancel_exact_frame()
            self._request_base_frame()

    def _request_base_frame(self) -> None:
        """Decodes the paused frame at the current position (debounced,
        so a slider drag decodes only where the pointer rests)."""
        if self._base_frame_after_id is not None:
            self.after_cancel(self._base_frame_after_id)
        self._base_frame_after_id = self.after(_BASE_FRAME_DEBOUNCE_MS, self._run_base_frame_decode)

    def _run_base_frame_decode(self) -> None:
        self._base_frame_after_id = None
        engine = self._engine
        if engine is None or engine.duration <= 0 or engine.playing:
            return
        timeline, media_items = self._timeline_panel.timeline, dict(self._media_items)
        t = engine.position
        width, height = engine.frame_size
        self._base_frame_token += 1
        token = self._base_frame_token
        run_generation_in_background(
            lambda: playback.decode_single_frame(timeline, media_items, t=t, width=width, height=height),
            self._result_queue, source=("base_frame", token, t),
        )

    def _handle_base_frame_result(self, token: int, t: float, result: GenerationTaskResult) -> None:
        if token != self._base_frame_token or (self._engine is not None and self._engine.playing):
            return  # superseded by a newer seek, or playback started meanwhile
        if result.error:
            self._preview_panel.show_error(result.error)
            return
        self._preview_panel.show_base(result.value, t)
        self._schedule_exact_frame()

    def _request_preview_audio(self) -> None:
        """(Re)builds the WAV the preview plays along - after a timeline
        or music change, debounced. Playback is silent until it's ready
        rather than playing audio that no longer matches the video."""
        if self._current_project is None or not self._audio_player.available:
            return
        if self._engine is not None:
            self._engine.set_audio_file(None)
        if self._audio_after_id is not None:
            self.after_cancel(self._audio_after_id)
        self._audio_after_id = self.after(_PREVIEW_AUDIO_DELAY_MS, self._run_preview_audio_render)

    def _run_preview_audio_render(self) -> None:
        self._audio_after_id = None
        if self._current_project is None:
            return
        timeline = self._timeline_panel.timeline
        if not timeline.items or timeline.validate():
            return
        media_items = dict(self._media_items)
        cwd = self._current_project.exports_dir
        track = self._music_track
        self._audio_token += 1
        token = self._audio_token
        output_path = cwd / f"_preview_audio_{token}.wav"

        def music_builder(input_count: int, duration: float):
            if track is None:
                return None
            return build_music_mix_filter(
                track, timeline_duration_seconds=duration, cwd=cwd, timeline_audio_input_count=input_count,
            )

        run_generation_in_background(
            lambda: playback.render_preview_audio(
                timeline, media_items, output_path=output_path, cwd=cwd, audio_mix_builder=music_builder,
            ),
            self._result_queue, source=("preview_audio", token),
        )

    def _handle_preview_audio_result(self, token: int, result: GenerationTaskResult) -> None:
        if result.error or token != self._audio_token or self._engine is None:
            return
        self._engine.set_audio_file(result.value)
        for old in result.value.parent.glob("_preview_audio_*.wav"):
            if old != result.value:
                try:
                    old.unlink()
                except OSError:
                    pass  # still open in a just-stopped ffplay on Windows - removed next time

    def _on_destroy(self, event) -> None:
        if event.widget is not self:
            return
        if self._engine is not None:
            self._engine.close()
        self._audio_player.stop()

    # --- live preview: overlays, selection, interactive editing --------------------------------

    def _current_scene(self) -> pc.Scene:
        lines = None
        if self._caption_style is not None and self._caption_lines:
            lines = tuple(self._caption_lines)
        return pc.Scene(
            text_overlays=tuple(self._text_overlays), stickers=tuple(self._stickers),
            caption_style=self._caption_style, caption_lines=lines,
        )

    def _on_scene_changed(self) -> None:
        """Any overlay (text, sticker, caption) changed: redraw the
        preview right away, then save and re-render the exact frame."""
        self._preview_panel.update_scene(self._current_scene())
        self._sync_inspector_with_selection()
        self._schedule_exact_frame()
        self._schedule_overlay_save()

    def _items_for(self, kind: str) -> list:
        return self._text_overlays if kind == "text" else self._stickers

    def _sync_inspector_with_selection(self) -> None:
        """Shows the selected element's settings (or none)."""
        self._refresh_look_panels()
        ref = self._selection
        if ref is not None:
            kind, index = ref
            if kind in ("text", "sticker"):
                items = self._items_for(kind)
                if 0 <= index < len(items):
                    self._inspector_panel.show_element(kind, index, items[index])
                    return
            elif kind == "clip":
                items = self._timeline_panel.timeline.items
                if 0 <= index < len(items):
                    media = self._media_items.get(items[index].media_item_id)
                    title = media.original_filename if media is not None else ""
                    max_transition = None
                    if index < len(items) - 1:
                        max_transition = track_layout.max_transition_seconds(self._editor_state(), index)
                    self._inspector_panel.show_element(
                        "clip", index, items[index], title=title, max_transition=max_transition,
                    )
                    return
            self._selection = None
        if self._inspector_panel.shown is not None:
            self._inspector_panel.show_nothing()

    def _set_selection(self, ref: tuple[str, int] | None) -> None:
        """One selection shared by the preview, the track timeline and
        the settings panel ("text"/"sticker"/"clip", index)."""
        self._selection = ref
        if ref is None or ref[0] in ("text", "sticker"):
            self._preview_panel.select(ref)
        else:
            self._preview_panel.select(None)
        track_ref = None
        if ref is not None:
            track_ref = ({"text": "text", "sticker": "stickers", "clip": "video"}[ref[0]], ref[1])
        self._track_timeline.select(track_ref)
        self._sync_inspector_with_selection()

    def _on_preview_selection_changed(self, ref: tuple[str, int] | None) -> None:
        self._set_selection(ref)

    def _on_track_selection_changed(self, ref: tuple[str, int] | None) -> None:
        """A bar was clicked on the track timeline."""
        if ref is None:
            self._set_selection(None)
            return
        track, index = ref
        if track in ("text", "stickers"):
            kind = "text" if track == "text" else "sticker"
            self._set_selection((kind, index))
            element = self._items_for(kind)[index]
            # Make sure it's on screen in the preview so it can be dragged there too.
            if self._engine is not None and not (element.start_seconds <= self._engine.position < element.end_seconds):
                self._on_preview_seek(element.start_seconds)
        elif track in ("video", "effects"):
            self._set_selection(("clip", index))
            if track == "effects":
                self._on_category_selected("filters")
        else:
            self._set_selection(None)
            self._track_timeline.select(ref)
            self._on_category_selected("music" if track == "audio" else "text")

    # --- filters and transitions (library panels) -------------------------------------------

    def _look_target(self) -> int | None:
        """The clip the Filters/Transitions panels apply to: the
        selected clip, else the one under the playhead."""
        items = self._timeline_panel.timeline.items
        if not items:
            return None
        if self._selection is not None and self._selection[0] == "clip" and 0 <= self._selection[1] < len(items):
            return self._selection[1]
        position = self._engine.position if self._engine is not None else 0.0
        segments = playback.timeline_segments(self._timeline_panel.timeline, self._media_items)
        for segment in segments:
            if segment.start_seconds <= position < segment.end_seconds:
                return segment.index
        return segments[-1].index if segments else 0

    def _refresh_look_panels(self) -> None:
        if not hasattr(self, "_filters_panel"):
            return
        index = self._look_target()
        if index is None:
            self._filters_panel.show_target(None, None, None)
            self._transitions_panel.show_target(None, None, None)
            return
        items = self._timeline_panel.timeline.items
        item = items[index]
        media = self._media_items.get(item.media_item_id)
        title = media.original_filename if media is not None else f"#{index + 1}"
        self._filters_panel.show_target(title, item.effect, self._get_thumbnail(media) if media is not None else None)
        limit = track_layout.max_transition_seconds(self._editor_state(), index) if index < len(items) - 1 else None
        self._transitions_panel.show_target(title, item.transition_out, limit)

    def _on_look_chosen(self, look: str) -> None:
        index = self._look_target()
        if index is None:
            return
        effect = self._timeline_panel.timeline.items[index].effect
        intensity = effect.look_intensity if effect.look != "none" and effect.look_intensity > 0 else 1.0
        self._apply_editor_state(
            track_layout.set_look(self._editor_state(), index, look, intensity), label="Filtras",
        )

    def _on_look_intensity_changed(self, intensity: float, final: bool) -> None:
        """Applied once the slider rests (each change restarts decoding)."""
        index = self._look_target()
        if index is None or not final:
            return
        look = self._timeline_panel.timeline.items[index].effect.look
        self._apply_editor_state(
            track_layout.set_look(self._editor_state(), index, look, intensity),
            label="Filtro intensyvumas", coalesce_key=f"look:{index}",
        )

    def _on_look_apply_all(self) -> None:
        index = self._look_target()
        if index is None:
            return
        effect = self._timeline_panel.timeline.items[index].effect
        self._apply_editor_state(
            track_layout.set_look(self._editor_state(), None, effect.look, effect.look_intensity),
            label="Filtras visiems klipams",
        )

    def _on_transition_chosen(self, kind: str) -> None:
        index = self._look_target()
        if index is None:
            return
        self._apply_editor_state(
            track_layout.set_transition(self._editor_state(), index, kind, self._transitions_panel.duration()),
            label="Perėjimas",
        )

    def _on_transition_duration_changed(self, duration: float, final: bool) -> None:
        index = self._look_target()
        if index is None or not final:
            return
        kind = self._timeline_panel.timeline.items[index].transition_out.kind
        self._apply_editor_state(
            track_layout.set_transition(self._editor_state(), index, kind, duration),
            label="Perėjimo trukmė", coalesce_key=f"transition:{index}",
        )

    def _on_transition_apply_all(self) -> None:
        index = self._look_target()
        if index is None:
            return
        transition = self._timeline_panel.timeline.items[index].transition_out
        self._apply_editor_state(
            track_layout.set_transition_everywhere(self._editor_state(), transition.kind, transition.duration_seconds),
            label="Perėjimas visiems klipams",
        )

    def _on_element_edited(self, kind: str, index: int, new_element, final: bool) -> None:
        """An element was moved/resized/rotated in the preview or
        changed in the settings panel. Applied immediately on every
        call; the Text/Stickers panels and the saved project are
        updated once the edit is final."""
        if kind == "clip":
            self._on_clip_edited(index, new_element, final)
            return
        items = self._items_for(kind)
        if not (0 <= index < len(items)):
            return
        problems = new_element.validate()
        if problems:
            if final:
                self._set_status(problems[0], kind="error")
                self._inspector_panel.refresh_values(items[index])
            return
        original = self._panel_sync_originals.setdefault((kind, index), items[index])
        items[index] = new_element
        self._cancel_exact_frame()
        self._preview_panel.update_scene(self._current_scene())
        if self._inspector_panel.shown == (kind, index):
            self._inspector_panel.refresh_values(new_element)
        if not final:
            return
        del self._panel_sync_originals[(kind, index)]
        if kind == "text":
            self._text_overlay_panel.replace_overlay(original, new_element)
        else:
            self._stickers_panel.replace_sticker(original, new_element)
        self._clear_status()
        self._record_history("Pakeistas elementas", coalesce_key=f"{kind}:{index}")
        self._refresh_track_timeline()
        self._schedule_exact_frame()
        self._schedule_overlay_save()

    def _on_clip_edited(self, index: int, new_item, final: bool) -> None:
        """A clip/photo setting changed in the settings panel. Applied
        once final (each change restarts video decoding)."""
        if not final:
            return
        timeline = self._timeline_panel.timeline
        if not (0 <= index < len(timeline.items)):
            return
        items = list(timeline.items)
        items[index] = new_item
        new_timeline = dataclasses.replace(timeline, items=tuple(items))
        problems = [p for p in new_timeline.validate() if p not in timeline.validate()]
        if problems:
            self._set_status(problems[0], kind="error")
            self._inspector_panel.refresh_values(timeline.items[index])
            return
        self._clear_status()
        self._apply_editor_state(
            dataclasses.replace(self._editor_state(), timeline=new_timeline),
            label="Klipo nustatymai", coalesce_key=f"clip:{index}",
        )

    def _on_element_delete_requested(self, kind: str, index: int) -> None:
        if kind == "clip":
            self._set_selection(None)
            self._apply_editor_state(
                track_layout.delete_element(self._editor_state(), "video", index), label="Ištrintas klipas",
            )
            return
        items = self._items_for(kind)
        if not (0 <= index < len(items)):
            return
        element = items[index]
        self._set_selection(None)
        self._panel_sync_originals.pop((kind, index), None)
        if kind == "text":
            self._text_overlay_panel.remove_overlay(element)  # emits -> _on_text_overlays_changed
        else:
            self._stickers_panel.remove_sticker(element)  # emits -> _on_stickers_changed

    def _on_element_duplicate_requested(self, kind: str, index: int) -> None:
        if kind == "clip":
            result = track_layout.duplicate_element(
                self._editor_state(), "video", index, new_clip_id=uuid.uuid4().hex[:12],
                total=track_layout.total_duration(self._editor_state(), self._media_items),
            )
            if result is not None:
                new_state, new_index = result
                self._apply_editor_state(new_state, label="Nukopijuotas klipas")
                self._set_selection(("clip", new_index))
            return
        items = self._items_for(kind)
        if not (0 <= index < len(items)):
            return
        element = items[index]
        copy = dataclasses.replace(
            element, x_fraction=min(1.0, element.x_fraction + 0.05), y_fraction=min(1.0, element.y_fraction + 0.05),
        )
        self._add_element(kind, copy)

    def _on_quick_add_text(self) -> None:
        start, end = self._quick_add_window()
        if end is None:
            return
        self._add_element("text", TextOverlay(
            text="Naujas tekstas", start_seconds=start, end_seconds=end, x_fraction=0.5, y_fraction=0.3,
        ))

    def _on_quick_add_sticker(self) -> None:
        start, end = self._quick_add_window()
        if end is None:
            return
        self._add_element("sticker", StickerInstance(
            start_seconds=start, end_seconds=end, shape="heart", animation="none",
        ))

    def _on_sticker_dragged(self, shape: str, x_root: int, y_root: int, dropped: bool) -> None:
        """A sticker dragged from the library: dropping it on the video
        places it there, at the current time."""
        fraction = self._preview_panel.fraction_at_root(x_root, y_root)
        if not dropped:
            self._preview_panel.show_drop_target(fraction is not None)
            return
        self._preview_panel.show_drop_target(False)
        if fraction is None:
            return
        start, end = self._quick_add_window()
        if end is None:
            return
        self._add_element("sticker", StickerInstance(
            start_seconds=start, end_seconds=end, shape=shape, animation="none",
            x_fraction=fraction[0], y_fraction=fraction[1],
        ))

    def _quick_add_window(self) -> tuple[float, float | None]:
        """A new element starts at the preview's current time and lasts
        3 s (or to the end of the timeline), so it's visible right away."""
        if self._current_project is None or self._engine is None or self._engine.duration <= 0:
            self._set_status("Pirmiausia pridėkite klipą ar nuotrauką į laiko juostą.", kind="error")
            return 0.0, None
        duration = self._engine.duration
        start = min(round(self._engine.position, 2), max(0.0, duration - 0.5))
        return start, round(min(duration, start + 3.0), 2)

    def _add_element(self, kind: str, element) -> None:
        if kind == "text":
            self._text_overlay_panel.add_overlay(element)  # emits -> _on_text_overlays_changed
        else:
            self._stickers_panel.add_sticker(element)  # emits -> _on_stickers_changed
        items = self._items_for(kind)
        if element in items:
            index = len(items) - 1 - items[::-1].index(element)
            self._set_selection((kind, index))

    # --- live preview: exact export frame --------------------------------------------------------

    def _schedule_exact_frame(self) -> None:
        """Once the preview has been paused and unchanged for a moment,
        renders the REAL export frame of this moment with ffmpeg and
        swaps it in - the check that what you see is what export makes."""
        if self._current_project is None or self._engine is None or self._engine.playing:
            return
        self._cancel_exact_frame()
        self._live_preview_render_after_id = self.after(_EXACT_FRAME_DELAY_MS, self._run_live_preview_render)

    def _cancel_exact_frame(self) -> None:
        if self._live_preview_render_after_id is not None:
            self.after_cancel(self._live_preview_render_after_id)
            self._live_preview_render_after_id = None
        self._preview_request_token += 1  # any render already running is now stale

    def _run_live_preview_render(self) -> None:
        self._live_preview_render_after_id = None
        if self._current_project is None or self._engine is None or self._engine.playing:
            return
        timeline = self._timeline_panel.timeline
        if not timeline.items or timeline.validate():
            return
        try:
            export_format = mse.resolve_export_format(timeline.aspect_ratio, _PREVIEW_CANVAS_TIER)
        except MultiSourceExportError as e:
            self._preview_panel.show_error(str(e))
            return

        caption_filter = None
        if self._caption_style is not None and self._caption_lines is not None:
            try:
                caption_filter = build_caption_filter_from_lines(self._caption_lines, self._caption_style)
            except CaptionError:
                caption_filter = None  # a mid-edit invalid style/line is shown via CaptionsPanel's own validation, never surfaced as a preview error
        text_overlay_filter, sticker_filters, overlay_error = self._build_overlay_filters(
            timeline, export_format, caption_filter=caption_filter,
        )
        if overlay_error is not None:
            self._preview_panel.show_error(overlay_error)
            return

        timestamp = self._engine.position
        media_items = dict(self._media_items)
        cwd = self._current_project.exports_dir
        self._preview_request_token += 1
        token = self._preview_request_token
        run_generation_in_background(
            lambda: render_preview_frame(
                timeline, media_items, export_format=export_format, timestamp_seconds=timestamp,
                filters=PreviewFilters(
                    caption_filter=caption_filter, text_overlay_filter=text_overlay_filter,
                    sticker_filters=sticker_filters or None,
                ),
                cwd=cwd,
            ),
            self._result_queue, source=("live_preview", token),
        )

    def _handle_live_preview_result(self, token: int, result: GenerationTaskResult) -> None:
        if result.error:
            if token == self._preview_request_token:
                self._preview_panel.show_error(result.error)
            return
        if token != self._preview_request_token or (self._engine is not None and self._engine.playing):
            result.value.unlink(missing_ok=True)  # stale: the scene or position changed while it rendered
            return
        self._preview_panel.show_exact(result.value)
        if self._last_exact_frame_path is not None and self._last_exact_frame_path != result.value:
            self._last_exact_frame_path.unlink(missing_ok=True)
        self._last_exact_frame_path = result.value

    # --- saving overlays --------------------------------------------------------------------------

    def _schedule_overlay_save(self) -> None:
        """Autosave for everything on top of the timeline (the timeline
        itself is already saved on every edit by _on_timeline_changed())."""
        if self._current_project is None:
            return
        if self._overlay_save_after_id is not None:
            self.after_cancel(self._overlay_save_after_id)
        self._overlay_save_after_id = self.after(_OVERLAY_SAVE_DELAY_MS, self._save_overlays_now)
        self._save_label.configure(text="💾 Saugoma...")

    def _save_overlays_now(self) -> None:
        self._overlay_save_after_id = None
        if self._current_project is None:
            return
        storage.save_overlays(self._current_project.project_id, storage.ProjectOverlays(
            text_overlays=tuple(self._text_overlays), stickers=tuple(self._stickers),
            caption_style=self._caption_style,
            caption_lines=tuple(self._caption_lines) if self._caption_lines is not None else None,
            music_track=self._music_track,
        ))
        self._mark_saved()

    def _mark_saved(self) -> None:
        self._save_label.configure(text=f"💾 Išsaugota {time.strftime('%H:%M:%S')}")

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

    # --- reel templates (Stage 4 of the Reels-editor plan) ----------------------------------

    def _on_apply_reel_template(self, template: ReelTemplate) -> None:
        """Routes each real part of `template` into the ONE panel that
        already knows how to apply it - never writes into
        TimelinePanel/CaptionsPanel/StickersPanel's own internal state
        directly (this handler has no business knowing their widgets'
        own field names), matching every other cross-panel action in
        this dashboard (e.g. _on_add_to_timeline_clicked() calling
        TimelinePanel.add_clip() rather than touching `_timeline`
        itself). Requires an open project with at least one timeline
        item - a template has nothing to apply motion/fade to on an
        empty timeline, and TextOverlayPanel's own per-row Template
        dropdown (added in Stage 2) stays the way to add new fresh text
        with a template's own font/animation if the person wants that
        too; this action only ever touches the CURRENT clips/stills."""
        if self._current_project is None:
            self._set_status("Create or open a project first.", kind="error")
            return
        if not self._timeline_panel.timeline.items:
            self._set_status("Add at least one clip or photo before applying a template.", kind="error")
            return
        new_timeline = apply_template(self._timeline_panel.timeline, template)
        with self._history_batch(f"Šablonas „{template.name}“"):
            self._timeline_panel.apply_timeline(new_timeline)
            self._captions_panel.apply_style(template.caption_style)
            if template.sticker_presets:
                self._stickers_panel.apply_presets(template.sticker_presets)
        self._set_status(f"Applied template '{template.name}'.", kind="muted")

    # --- AI creative assistant (Stage 5 of the Reels-editor plan) ---------------------------

    def _on_ai_propose_requested(self, brief: str) -> None:
        """Runs the real (possibly-slow) LLM call in the background,
        same run_generation_in_background() convention every other
        possibly-slow action in this dashboard already uses - never
        called directly from the panel's own button handler. If no
        LLMClient is configured at all (self._llm is None - the same
        optional dependency every other AI feature in this app already
        tolerates), fails immediately with a clear message rather than
        trying and getting a confusing exception."""
        if self._llm is None:
            self._ai_assistant_panel.show_error(
                "AI features are not available (no API key configured). "
                "Try a ready-made Reel Template instead."
            )
            return
        llm = self._llm
        run_generation_in_background(
            lambda: propose_reel_style(llm, brief), self._result_queue, source="ai_proposal",
        )

    def _handle_ai_proposal_result(self, result: GenerationTaskResult) -> None:
        if result.error:
            self._ai_assistant_panel.show_error(f"Could not get a suggestion: {result.error}")
            return
        proposal: AiReelProposal | None = result.value
        if proposal is None:
            self._ai_assistant_panel.show_error(
                "No usable suggestion came back - try rephrasing your description, "
                "or use a ready-made Reel Template instead."
            )
            return
        self._ai_assistant_panel.show_proposal(proposal)

    def _on_apply_ai_proposal(self, proposal: AiReelProposal) -> None:
        """The person's own explicit "✅ Apply" click on a shown
        proposal - routes through the EXACT SAME real apply paths
        _on_apply_reel_template() above already uses and already
        tested (TimelinePanel.apply_timeline()/CaptionsPanel
        .apply_style()/StickersPanel.apply_presets()), never a second
        application mechanism for AI-sourced style versus a curated
        ReelTemplate. Nothing here runs until this exact method is
        called - propose_reel_style()/show_proposal() above never
        apply anything on their own, satisfying the user's own
        explicit "preview and confirm before applying" requirement."""
        if self._current_project is None:
            self._set_status("Create or open a project first.", kind="error")
            return
        if not self._timeline_panel.timeline.items:
            self._set_status("Add at least one clip or photo before applying a suggestion.", kind="error")
            return
        new_timeline = apply_effect_to_every_item(self._timeline_panel.timeline, proposal.effect)
        with self._history_batch("AI pasiūlymas"):
            self._timeline_panel.apply_timeline(new_timeline)
            self._captions_panel.apply_style(proposal.caption_style)
            if proposal.stickers:
                self._stickers_panel.apply_presets(tuple(s.to_preset() for s in proposal.stickers))
        self._set_status("Applied AI-suggested style.", kind="muted")

    # --- captions (Stage 4) ----------------------------------------------------------------

    def _on_caption_style_changed(self, style: CaptionStyle | None) -> None:
        self._caption_style = style
        # A style change invalidates any already-edited lines (they
        # were grouped/reviewed under the PREVIOUS style's own
        # animation choice) - cleared so export falls back to the
        # existing auto-transcribe-at-export-time path rather than
        # silently reusing stale, possibly-mismatched edited lines.
        self._caption_lines = None
        self._record_history("Subtitrų stilius")
        self._refresh_track_timeline()
        self._on_scene_changed()

    def _on_generate_subtitles_requested(self, language: str) -> None:
        """"📝 Generate & Edit Subtitles" button - transcribes the
        first clip's own speech NOW (not at export time, unlike the
        existing auto-transcribe path still used when the person never
        clicks this button) so the person can review/edit each line's
        own text/timing before export. A real, possibly-slow
        transcription, run through the same run_generation_in_background()
        convention as every other background call in this dashboard.

        `language` is the person's own chosen language code (e.g. "lt")
        from CaptionsPanel's own language dropdown - real bug fixed
        here: this call used to never pass a language at all, silently
        defaulting to whisper's own "auto" detection, which could (and,
        per a real user report, did) misidentify the actual spoken
        language entirely - see jarvis.video_editor.captions
        .CAPTION_LANGUAGE_CHOICES's own docstring for the full finding."""
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
            lambda: generate_word_timings(first_clip_media.stored_path, language=language),
            self._result_queue, source="subtitle_generation",
        )

    def _handle_subtitle_generation_result(self, result: GenerationTaskResult) -> None:
        self._captions_panel.set_generating_state(generating=False)
        if result.error:
            self._captions_panel.set_generate_error(result.error)
            return
        self._last_transcribed_words = result.value
        lines = group_words_into_lines(result.value)
        self._captions_panel.set_lines(lines)
        self._caption_lines = lines
        self._record_history("Sugeneruoti subtitrai")
        self._refresh_track_timeline()
        self._on_scene_changed()

    def _on_caption_lines_changed(self, lines: list[CaptionLine]) -> None:
        self._caption_lines = lines
        self._record_history("Subtitrai")
        self._refresh_track_timeline()
        self._on_scene_changed()

    def _on_export_srt_requested(self, lines: list[CaptionLine]) -> None:
        """"💾 Export Subtitles as .SRT" button - requirement: "Leisk
        eksportuoti subtitrus SRT formatu" (allow exporting subtitles in
        SRT format). Writes the SAME real, possibly person-edited
        CaptionLines the export step itself burns into the video (not a
        fresh re-transcription), so the exported .srt always matches
        exactly what the video's own burned-in captions say."""
        from tkinter import filedialog

        path = filedialog.asksaveasfilename(
            title="Export Subtitles", defaultextension=".srt", filetypes=[("SubRip subtitles", "*.srt")],
        )
        if not path:
            return
        try:
            export_srt(lines, Path(path))
            self._set_status(f"Subtitles exported to {Path(path).name}.", kind="muted")
        except OSError as e:
            self._set_status(f"Couldn't export subtitles: {e}", kind="error")

    # --- text overlays -----------------------------------------------------------------------

    def _on_text_overlays_changed(self, overlays: list[TextOverlay]) -> None:
        self._text_overlays = list(overlays)
        self._drop_pending_panel_sync("text")
        self._record_history("Tekstas")
        self._refresh_track_timeline()
        self._on_scene_changed()

    def _drop_pending_panel_sync(self, kind: str) -> None:
        # The panel itself just reported the authoritative list, so no
        # preview edit of this kind is still waiting to be synced into it.
        for key in [k for k in self._panel_sync_originals if k[0] == kind]:
            del self._panel_sync_originals[key]

    # --- stickers ------------------------------------------------------------------------------

    def _on_stickers_changed(self, stickers: list[StickerInstance]) -> None:
        self._stickers = list(stickers)
        self._drop_pending_panel_sync("sticker")
        self._record_history("Lipdukai")
        self._refresh_track_timeline()
        self._on_scene_changed()

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
        self._record_history("Muzika")
        self._refresh_track_timeline()
        self._schedule_overlay_save()
        self._request_preview_audio()

    def _on_analyze_rhythm_requested(self, track_path: Path) -> None:
        """"🎵 Analyze Rhythm" button - a real, possibly-slow audio
        analysis (jarvis.video_editor.audio_sync.analyze_amplitude_peaks()),
        run through the same run_generation_in_background() convention
        as every other background call in this dashboard. See that
        module's own docstring for the honest disclosure that this finds
        real amplitude peaks (loud moments), not true BPM/beat
        detection."""
        from jarvis.video_editor.audio_sync import analyze_amplitude_peaks

        self._music_panel.set_analyzing_state(analyzing=True)
        run_generation_in_background(
            lambda: analyze_amplitude_peaks(track_path), self._result_queue, source="rhythm_analysis",
        )

    def _handle_rhythm_analysis_result(self, result: GenerationTaskResult) -> None:
        self._music_panel.set_analyzing_state(analyzing=False)
        if result.error:
            self._music_panel.show_rhythm_error(result.error)
            return
        self._music_panel.show_rhythm_peaks(result.value)

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

        text_scale = text_scale_for(*_dimensions(mse.resolve_export_format(timeline.aspect_ratio, resolution_tier)))
        if self._caption_style is not None and self._caption_lines is not None:
            # The person already generated AND reviewed/edited the
            # subtitle lines via "📝 Generate & Edit Subtitles" (see
            # _on_generate_subtitles_requested()) - export uses those
            # exact, possibly hand-edited lines directly, with NO
            # re-transcription at export time (re-transcribing here
            # would silently discard whatever text/timing edits the
            # person just made).
            try:
                caption_filter = build_caption_filter_from_lines(
                    self._caption_lines, self._caption_style, scale=text_scale,
                )
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
            caption_language = self._captions_panel.get_language()
            run_generation_in_background(
                lambda: generate_word_timings(first_clip_media.stored_path, language=caption_language),
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
            export_format = mse.resolve_export_format(timeline.aspect_ratio, self._pending_export_resolution_tier)
            caption_filter = build_caption_filter(
                words, self._caption_style or CaptionStyle(), scale=text_scale_for(*_dimensions(export_format)),
            )
        except Exception as e:  # pragma: no cover - build_caption_filter() itself never raises for valid input
            self._export_panel.set_exporting_state(exporting=False)
            self._set_status(f"Couldn't build captions: {e}", kind="error")
            return
        self._clear_status()
        self._start_export(self._pending_export_resolution_tier, caption_filter=caption_filter)

    def _build_overlay_filters(
        self, timeline: Timeline, export_format, *, caption_filter: str | None,
    ) -> tuple[str | None, list[tuple[list[str], str]], str | None]:
        """Builds `(text_overlay_filter, sticker_filters, error_message)`
        from this dashboard's own current `_text_overlays`/`_stickers`
        state - the ONE place this video-label-chaining logic lives, used
        by BOTH `_start_export()` and `_refresh_live_preview()` (Live
        Preview's own single-frame render needs the EXACT SAME filter
        clauses a real export would build, or the preview would not
        genuinely reflect what export produces). Factored out after this
        exact logic was duplicated once already and caused a real,
        reproduced export bug (see this method's own inline comment on
        the video_label chaining order) - a second copy for Live Preview
        would have risked the identical class of bug drifting out of
        sync between the two call sites.

        Returns `error_message` (not None) if building a filter failed
        (an invalid overlay/sticker) - the caller is responsible for
        surfacing it and stopping; this method never raises."""
        text_scale = text_scale_for(export_format.width, export_format.height)
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
                    self._text_overlays, video_label=overlay_video_label, scale=text_scale,
                )
            except TextOverlayError as e:
                return None, [], f"Text overlay couldn't be added: {e}"

        sticker_filters: list[tuple[list[str], str]] = []
        if text_overlay_filter is not None:
            current_video_label = "textv"
        elif caption_filter is not None:
            current_video_label = "capv"
        else:
            current_video_label = "outv"
        distinct_input_count = len({item.media_item_id for item in timeline.items})

        # Rotated text: drawtext can't rotate, so each rotated overlay
        # is a Pillow-rendered image composited like a sticker, right
        # after the drawtext stage and BELOW every sticker - the same
        # stacking order the live preview draws.
        if any(overlay.is_rotated for overlay in self._text_overlays):
            try:
                rotated_filters, current_video_label = build_rotated_text_filters(
                    self._text_overlays, canvas_width=export_format.width, canvas_height=export_format.height,
                    cwd=self._current_project.exports_dir, video_label=current_video_label,
                    first_input_index=distinct_input_count, scale=text_scale,
                )
            except TextOverlayError as e:
                return None, [], f"Text overlay couldn't be added: {e}"
            sticker_filters.extend(rotated_filters)

        if self._stickers:
            # Each sticker's own filter clause reads from whichever
            # video label the PREVIOUS stage produced - the raw
            # timeline, captions, text overlays, or an earlier sticker
            # in this same loop - mirroring text_overlay_filter's own
            # chaining logic just above, generalized to however many
            # stickers are placed. Each sticker's own image occupies the
            # NEXT ffmpeg input index after every real timeline media
            # input (distinct_input_count) plus every earlier sticker's
            # own image input.
            #
            # Real, hand-hit bug this fixes: this used to check
            # caption_filter BEFORE text_overlay_filter, so whenever
            # BOTH were active the sticker read from "capv" (captions'
            # own output) even though text_overlay_filter had already
            # run AFTER captions and produced "textv" - "capv" was a
            # real, valid label earlier in the chain, so ffmpeg accepted
            # the sticker's own reference to it, but "textv" (text
            # overlay's own just-produced output) was then never read by
            # anything, which ffmpeg's filtergraph parser rejects
            # outright as `Filter 'drawtext:default' has output 1
            # (textv) unconnected`/`Error binding filtergraph
            # inputs/outputs: Invalid argument` - a real, reproduced
            # export failure whenever a timeline had captions AND a
            # text overlay AND at least one sticker, with no music
            # (music's own already-correct chaining via `audio_out`
            # masked the video-label ordering bug in every export that
            # also had music, since that path never touches this
            # video-label logic at all). The fix: check whichever filter
            # actually runs LAST in export_timeline()'s own documented
            # stage order (text_overlay_filter, THEN caption_filter,
            # THEN the raw timeline) - never the two stages' own
            # presence in isolation.
            first_sticker_input = distinct_input_count + len(sticker_filters)
            try:
                for n, sticker in enumerate(self._stickers):
                    input_index = first_sticker_input + n
                    output_label = f"stickv{n}"
                    extra_args, clause = build_sticker_filter(
                        sticker, canvas_width=export_format.width, canvas_height=export_format.height,
                        cwd=self._current_project.exports_dir, video_label=current_video_label,
                        output_label=output_label, input_index=input_index,
                    )
                    sticker_filters.append((extra_args, clause))
                    current_video_label = output_label
            except StickerError as e:
                return None, [], f"Sticker couldn't be added: {e}"

        return text_overlay_filter, sticker_filters, None

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

        text_overlay_filter, sticker_filters, overlay_error = self._build_overlay_filters(
            timeline, export_format, caption_filter=caption_filter,
        )
        if overlay_error is not None:
            self._export_panel.set_exporting_state(exporting=False)
            self._set_status(overlay_error, kind="error")
            return

        audio_mix_filter = None
        if self._music_track is not None:
            try:
                input_args, _full_filter, _video_out, audio_out = mse.build_filtergraph(
                    timeline, self._media_items, export_format, cwd=self._current_project.exports_dir,
                )
                # Real, hand-hit bug: this used to recompute
                # len({item.media_item_id ...}) alone, ignoring every
                # sticker input already appended above - when both
                # stickers AND music were active, music's own `[N:a]`
                # reference collided with the FIRST sticker's own PNG
                # input index (both claimed to be "the next input after
                # the timeline's own media"), so ffmpeg tried to read an
                # audio stream from a PNG file and failed with "matches
                # no streams" / a cascading filtergraph binding error
                # (reported as drawtext's own "output unconnected" in
                # one real user-hit variant of this same root cause).
                # Music's own input must land AFTER every sticker's own
                # image input, never at the same index.
                distinct_input_count = len({item.media_item_id for item in timeline.items}) + len(sticker_filters)
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
            sticker_filters=sticker_filters or None,
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

    # --- editor state, undo/redo -------------------------------------------------------------

    def _editor_state(self) -> EditorState:
        return EditorState(
            timeline=self._timeline_panel.timeline,
            text_overlays=tuple(self._text_overlays),
            stickers=tuple(self._stickers),
            caption_style=self._caption_style,
            caption_lines=tuple(self._caption_lines) if self._caption_lines is not None else None,
            music_track=self._music_track,
        )

    def _record_history(self, label: str, *, coalesce_key: str | None = None) -> None:
        """Called after every edit: makes it one Undo step (quick
        repeats of the same edit - typing, a slider - merge into one)."""
        if self._applying_state or self._history_batch_depth or self._current_project is None:
            return
        self._history.record(self._editor_state(), label=label, coalesce_key=coalesce_key)
        self._update_history_controls()

    @contextlib.contextmanager
    def _history_batch(self, label: str):
        """Several panel changes that form ONE action (applying a
        template) become a single Undo step."""
        self._history_batch_depth += 1
        try:
            yield
        finally:
            self._history_batch_depth -= 1
        self._record_history(label)

    def _update_history_controls(self) -> None:
        can_undo, can_redo = self._history.can_undo(), self._history.can_redo()
        self._undo_button.configure(state="normal" if can_undo else "disabled")
        self._redo_button.configure(state="normal" if can_redo else "disabled")
        self._timeline_panel.set_history_state(can_undo=can_undo, can_redo=can_redo)

    def _on_undo(self) -> None:
        label = self._history.undo_label
        state = self._history.undo()
        if state is not None:
            self._apply_editor_state(state, record=False)
            self._set_status(f"Atšaukta: {label}" if label else "Atšaukta", kind="muted")

    def _on_redo(self) -> None:
        label = self._history.redo_label
        state = self._history.redo()
        if state is not None:
            self._apply_editor_state(state, record=False)
            self._set_status(f"Grąžinta: {label}" if label else "Grąžinta", kind="muted")

    def _apply_editor_state(
        self, state: EditorState, *, record: bool = True, label: str = "", coalesce_key: str | None = None,
    ) -> None:
        """Puts `state` on screen everywhere (every panel, the preview,
        the track timeline) and saves it - used by undo/redo and by
        edits made on the track timeline or in the settings panel."""
        if self._current_project is None:
            return
        previous = self._editor_state()
        timeline_changed = state.timeline != previous.timeline
        music_changed = state.music_track != previous.music_track
        self._applying_state = True
        try:
            if timeline_changed:
                self._timeline_panel.show_timeline(state.timeline)
            if state.text_overlays != previous.text_overlays:
                self._text_overlays = list(state.text_overlays)
                self._text_overlay_panel.set_overlays(self._text_overlays)
            if state.stickers != previous.stickers:
                self._stickers = list(state.stickers)
                self._stickers_panel.set_stickers(self._stickers)
            if (state.caption_style, state.caption_lines) != (previous.caption_style, previous.caption_lines):
                self._captions_panel.reset()
                if state.caption_style is not None:
                    self._captions_panel.apply_style(state.caption_style)
                if state.caption_lines is not None:
                    self._captions_panel.set_lines(list(state.caption_lines))
            if music_changed:
                track = state.music_track
                if track is not None and track.source_path.is_file():
                    self._music_panel.set_imported_track(track.source_path, duration_seconds=None, track=track)
                else:
                    self._music_panel.clear_track()
            self._text_overlays = list(state.text_overlays)
            self._stickers = list(state.stickers)
            self._caption_style = state.caption_style
            self._caption_lines = list(state.caption_lines) if state.caption_lines is not None else None
            self._music_track = state.music_track
            self._panel_sync_originals.clear()
        finally:
            self._applying_state = False

        if record:
            self._history.record(self._editor_state(), label=label, coalesce_key=coalesce_key)
        self._update_history_controls()
        if timeline_changed:
            storage.save_project(self._current_project.project_id, state.timeline, self._media_items)
            self._mark_saved()
            self._on_timeline_for_preview_changed()
        elif music_changed:
            self._request_preview_audio()
        self._refresh_track_timeline()
        self._on_scene_changed()

    def _on_track_state_edited(self, state: EditorState, final: bool, label: str, coalesce_key: str | None) -> None:
        """An edit made on the track timeline. While a text/sticker/
        caption bar is being dragged only the preview and the tracks
        follow it; the finished edit is applied and saved once."""
        if self._current_project is None:
            return
        if final:
            self._apply_editor_state(state, label=label, coalesce_key=coalesce_key)
            return
        lines = state.caption_lines if state.caption_style is not None and state.caption_lines else None
        self._cancel_exact_frame()
        self._preview_panel.update_scene(pc.Scene(
            text_overlays=state.text_overlays, stickers=state.stickers,
            caption_style=state.caption_style, caption_lines=lines,
        ))
        self._track_timeline.render(state, self._media_items)

    def _bind_shortcuts(self) -> None:
        """Ctrl+Z undo; Ctrl+Y / Ctrl+Shift+Z redo; Space play/pause -
        while this view is on screen and the cursor isn't in a text field."""
        toplevel = self.winfo_toplevel()

        def guarded(action: Callable[[], None]):
            def handler(event):
                try:
                    if not self.winfo_exists() or not self.winfo_ismapped():
                        return None
                    if isinstance(self.focus_get(), (tk.Entry, tk.Text, tk.Button, tk.Spinbox)):
                        return None
                except (tk.TclError, KeyError):
                    return None
                action()
                return "break"
            return handler

        undo = guarded(self._on_undo)
        redo = guarded(self._on_redo)

        def upper_z(event):
            return redo(event) if event.state & 0x1 else undo(event)  # Shift held, or Caps Lock

        toplevel.bind("<Control-z>", undo, add="+")
        toplevel.bind("<Control-Z>", upper_z, add="+")
        toplevel.bind("<Control-y>", redo, add="+")
        toplevel.bind("<Control-Y>", redo, add="+")
        toplevel.bind("<space>", guarded(self._on_play_toggled), add="+")

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
        if not self._status_container.winfo_manager() and hasattr(self, "_vertical_panes"):
            self._status_container.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_XS),
                                        before=self._vertical_panes)

    def _clear_status(self) -> None:
        for child in self._status_container.winfo_children():
            child.destroy()
        if hasattr(self, "_vertical_panes"):
            self._status_container.pack_forget()

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
        elif isinstance(result, GenerationTaskResult) and result.source == "rhythm_analysis":
            self._handle_rhythm_analysis_result(result)
        elif isinstance(result, GenerationTaskResult) and result.source == "ai_proposal":
            self._handle_ai_proposal_result(result)
        elif (
            isinstance(result, GenerationTaskResult) and isinstance(result.source, tuple)
            and len(result.source) == 2 and result.source[0] == "effect_preview"
        ):
            self._handle_effect_preview_result(result.source[1], result)
        elif (
            isinstance(result, GenerationTaskResult) and isinstance(result.source, tuple)
            and len(result.source) == 2 and result.source[0] == "live_preview"
        ):
            self._handle_live_preview_result(result.source[1], result)
        elif (
            isinstance(result, GenerationTaskResult) and isinstance(result.source, tuple)
            and len(result.source) == 3 and result.source[0] == "base_frame"
        ):
            self._handle_base_frame_result(result.source[1], result.source[2], result)
        elif (
            isinstance(result, GenerationTaskResult) and isinstance(result.source, tuple)
            and len(result.source) == 2 and result.source[0] == "preview_audio"
        ):
            self._handle_preview_audio_result(result.source[1], result)
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


def _dimensions(export_format: mse.ExportFormat) -> tuple[int, int]:
    return export_format.width, export_format.height
