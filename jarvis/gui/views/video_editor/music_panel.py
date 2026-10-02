"""Music panel: upload a real music file (MP3/WAV/etc), trim it,
adjust volume, set fade in/out - requirement 6's own "upload music/
audio, trim music, adjust volume, add fade". File selection uses
tkinter.filedialog directly, same convention every other upload control
in this package already follows (see ImportPanel's own docstring).
This panel never calls build_music_mix_filter()/copies the file into
the project itself - it only collects the person's own file choice and
MusicTrack configuration and hands both back to the owning dashboard,
which knows the current project's own storage paths."""

from __future__ import annotations

from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor.audio_mixing import MusicTrack, validate_music_track

_FILETYPES = (
    ("Audio files", "*.mp3 *.wav *.m4a *.aac *.flac *.ogg"),
    ("All files", "*.*"),
)


class MusicPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, on_file_chosen: Callable[[Path], None], on_track_changed: Callable[[MusicTrack | None], None],
        on_analyze_rhythm_requested: Callable[[Path], None] | None = None, **kwargs,
    ) -> None:
        """`on_file_chosen(path)` fires when a new music file is picked
        (the owning dashboard imports it into the project's own storage
        and calls back with the real project-local MusicTrack via
        set_imported_track() below). `on_track_changed(track_or_None)`
        fires whenever any control changes with an already-imported
        track, or None if the person removes the track entirely.

        `on_analyze_rhythm_requested(track_path)`, if given, fires when
        the "🎵 Analyze Rhythm" button is clicked - a real, possibly-
        slow audio analysis (jarvis.video_editor.audio_sync
        .analyze_amplitude_peaks()), so this panel never runs it
        directly; the owning dashboard runs it in the background and
        calls show_rhythm_peaks()/show_rhythm_error() below once done."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_file_chosen = on_file_chosen
        self._on_track_changed = on_track_changed
        self._on_analyze_rhythm_requested = on_analyze_rhythm_requested
        self._track_path: Path | None = None

        card = Card(self)
        card.pack(fill="x")
        self._inner = ctk.CTkFrame(card, fg_color="transparent")
        self._inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            self._inner, text="🎵 MUSIC",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._upload_row = ctk.CTkFrame(self._inner, fg_color="transparent")
        self._upload_row.pack(fill="x")
        ctk.CTkButton(self._upload_row, text="➕ Add Music", command=self._on_add_clicked, width=140).pack(side="left")

        self._controls_container = ctk.CTkFrame(self._inner, fg_color="transparent")

    def _on_add_clicked(self) -> None:
        path = filedialog.askopenfilename(title="Add music", filetypes=_FILETYPES)
        if path:
            self._on_file_chosen(Path(path))

    def set_imported_track(self, track_path: Path, *, duration_seconds: float | None) -> None:
        """Called by the owning dashboard once the chosen file has been
        copied into the project and probed - renders the real
        trim/volume/fade controls for it."""
        self._track_path = track_path
        for child in self._controls_container.winfo_children():
            child.destroy()
        self._controls_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        ctk.CTkLabel(
            self._controls_container, text=f"🎵 {track_path.name}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))

        row1 = ctk.CTkFrame(self._controls_container, fg_color="transparent")
        row1.pack(fill="x", pady=(0, theme.SPACE_XS))
        start_entry = ctk.CTkEntry(row1, width=70)
        start_entry.insert(0, "0.0")
        ctk.CTkLabel(row1, text="Start (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        start_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        volume_entry = ctk.CTkEntry(row1, width=60)
        volume_entry.insert(0, "1.0")
        ctk.CTkLabel(row1, text="Volume:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        volume_entry.pack(side="left")

        row2 = ctk.CTkFrame(self._controls_container, fg_color="transparent")
        row2.pack(fill="x", pady=(0, theme.SPACE_XS))
        fade_in_entry = ctk.CTkEntry(row2, width=60)
        fade_in_entry.insert(0, "1.0")
        ctk.CTkLabel(row2, text="Fade in (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        fade_in_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        fade_out_entry = ctk.CTkEntry(row2, width=60)
        fade_out_entry.insert(0, "1.0")
        ctk.CTkLabel(row2, text="Fade out (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        fade_out_entry.pack(side="left")

        error_label = status_label(self._controls_container, "", kind="error")

        def on_change(_event=None) -> None:
            try:
                start = float(start_entry.get())
                volume = float(volume_entry.get())
                fade_in = float(fade_in_entry.get())
                fade_out = float(fade_out_entry.get())
            except ValueError:
                return
            track = MusicTrack(
                source_path=track_path, trim_start_seconds=start, volume=volume,
                fade_in_seconds=fade_in, fade_out_seconds=fade_out,
            )
            problems = validate_music_track(track)
            if problems:
                error_label.configure(text=f"⚠️ {problems[0]}")
                error_label.pack(anchor="w", pady=(theme.SPACE_XS, 0))
                self._on_track_changed(None)
                return
            error_label.pack_forget()
            self._on_track_changed(track)

        for entry in (start_entry, volume_entry, fade_in_entry, fade_out_entry):
            entry.bind("<FocusOut>", on_change)
            entry.bind("<Return>", on_change)

        buttons_row = ctk.CTkFrame(self._controls_container, fg_color="transparent")
        buttons_row.pack(anchor="w", pady=(theme.SPACE_XS, 0))
        ctk.CTkButton(
            buttons_row, text="🗑 Remove Music", command=self._on_remove_clicked, width=130, height=24,
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        if self._on_analyze_rhythm_requested is not None:
            ctk.CTkButton(
                buttons_row, text="🎵 Analyze Rhythm (approximate)", height=24,
                command=lambda: self._on_analyze_rhythm_requested(track_path),
            ).pack(side="left")

        self._rhythm_status = status_label(self._controls_container, "", kind="muted")
        self._rhythm_container = ctk.CTkFrame(self._controls_container, fg_color="transparent")

        on_change()

    def set_analyzing_state(self, *, analyzing: bool) -> None:
        if analyzing:
            self._rhythm_status.configure(text="Analyzing audio for loud moments...", text_color=theme.ACCENT_PRIMARY)
            self._rhythm_status.pack(anchor="w", pady=(theme.SPACE_SM, 0))
        else:
            self._rhythm_status.pack_forget()

    def show_rhythm_error(self, message: str) -> None:
        self._rhythm_status.configure(text=f"⚠️ {message}", text_color=theme.DANGER)
        self._rhythm_status.pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def show_rhythm_peaks(self, peaks) -> None:
        """Renders the real, measured amplitude peaks as a plain,
        read-only timestamp list - requirement: "galimybė rankiniu būdu
        koreguoti automatiškai parinktus efektų pradžios momentus"
        (ability to manually adjust automatically-suggested effect start
        times). Deliberately NOT a one-click "apply to this sticker/
        text" button - the person copies whichever timestamp they want
        into any sticker's/text overlay's own Start field themselves,
        keeping every placement an explicit, reviewed choice rather than
        a silent automatic move (see jarvis.video_editor.audio_sync's
        own honest-disclosure docstring: these are loud-moment
        SUGGESTIONS, never auto-applied)."""
        self._rhythm_status.pack_forget()
        for child in self._rhythm_container.winfo_children():
            child.destroy()
        self._rhythm_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        if not peaks:
            status_label(
                self._rhythm_container, "No clear loud moments detected in this track.", kind="muted",
            ).pack(anchor="w")
            return

        status_label(
            self._rhythm_container,
            "Approximate loud moments (not real BPM/beat detection) - copy a timestamp into any "
            "sticker's or text overlay's own Start field:",
            kind="muted",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        for peak in peaks:
            ctk.CTkLabel(
                self._rhythm_container,
                text=f"  {peak.timestamp_seconds:.2f}s  (strength {peak.relative_strength:.0%})",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")

    def _on_remove_clicked(self) -> None:
        self._track_path = None
        for child in self._controls_container.winfo_children():
            child.destroy()
        self._controls_container.pack_forget()
        self._on_track_changed(None)
