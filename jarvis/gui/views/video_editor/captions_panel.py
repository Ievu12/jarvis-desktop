"""Captions panel: a simple on/off toggle plus style controls
(position/animation/font size/color) for Stage 4's own real, word-level
Lithuanian/any-language animated captions
(jarvis.video_editor.captions). This panel never calls
generate_word_timings()/build_caption_filter() itself - it only
collects the person's own enabled/style choice and hands it back to the
owning dashboard, which knows which source clip's own audio to
transcribe and runs the actual (possibly slow) transcription through
jarvis.gui.worker.run_generation_in_background(), same convention every
other panel in this package already follows (see ImportPanel's own
docstring for the identical division of responsibility).

Also renders the EDITABLE subtitle-line list (requirement: "galimybė
redaguoti kiekvieną subtitrų eilutę ir jos rodymo laiką" - edit each
subtitle line and its own display time) once a transcription has been
generated and grouped into jarvis.video_editor.captions.CaptionLine
entries - see set_lines()/`on_lines_changed` below. Editing a line here
never re-transcribes; it only changes the plain text/timing values the
export step later burns in via build_caption_filter_from_lines()."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import LabeledDropdown, status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor.captions import (
    CAPTION_ANIMATION_CHOICES,
    CAPTION_LANGUAGE_CHOICES,
    CAPTION_LANGUAGE_LABELS,
    CAPTION_POSITION_CHOICES,
    DEFAULT_CAPTION_COLOR,
    DEFAULT_CAPTION_FONT_SIZE,
    DEFAULT_CAPTION_HIGHLIGHT_COLOR,
    DEFAULT_CAPTION_LANGUAGE,
    CaptionLine,
    CaptionStyle,
)

_LANGUAGE_DISPLAY_CHOICES = tuple(CAPTION_LANGUAGE_LABELS[code] for code in CAPTION_LANGUAGE_CHOICES)
_LANGUAGE_CODE_BY_LABEL = {label: code for code, label in CAPTION_LANGUAGE_LABELS.items()}


class CaptionsPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, on_style_changed: Callable[[CaptionStyle | None], None],
        on_generate_requested: Callable[[str], None] | None = None,
        on_lines_changed: Callable[[list[CaptionLine]], None] | None = None,
        on_export_srt_requested: Callable[[list[CaptionLine]], None] | None = None, **kwargs,
    ) -> None:
        """`on_style_changed(style_or_None)` is called with a real
        CaptionStyle whenever the toggle is ON and any control changes,
        or None when the toggle is turned OFF - the owning dashboard
        uses None to mean "no captions for the next export", exactly as
        omitting jarvis.video_editor.multisource_export
        .export_timeline()'s own `caption_filter` parameter already
        means "no change from before this feature existed".

        `on_generate_requested(language_code)`, if given, fires when the
        "📝 Generate & Edit Subtitles" button is clicked, with the
        person's own chosen language code (e.g. "lt") - a real,
        possibly-slow transcription, so this panel never runs it
        directly; the owning dashboard transcribes in the background
        and calls set_lines() once real CaptionLines are ready. Real bug
        this fixes: this callback used to take no language argument at
        all, so every transcription silently used whisper's own "auto"
        default, which can (and, per a real user report, did) misdetect
        the actual spoken language - see jarvis.video_editor.captions
        .CAPTION_LANGUAGE_CHOICES's own docstring for the full finding.
        `on_lines_changed(lines)` fires with the current, valid-only
        line list whenever any line is edited/removed - mirrors
        TextOverlayPanel's own "one bad entry doesn't break everything
        else" validation spirit."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_style_changed = on_style_changed
        self._on_generate_requested = on_generate_requested
        self._on_lines_changed = on_lines_changed
        self._on_export_srt_requested = on_export_srt_requested
        self._enabled = False
        self._lines: list[CaptionLine] = []

        card = Card(self)
        card.pack(fill="x")
        self._inner = ctk.CTkFrame(card, fg_color="transparent")
        self._inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            self._inner, text="💬 ANIMATED CAPTIONS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            self._inner,
            text="Real, word-by-word timed captions transcribed from the first clip's own speech "
                 "(Lithuanian and other languages supported).",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._toggle_var = ctk.StringVar(value="off")
        ctk.CTkSwitch(
            self._inner, text="Enable captions for this export", variable=self._toggle_var,
            onvalue="on", offvalue="off", command=self._on_toggle,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        ).pack(anchor="w")

        self._controls_row = ctk.CTkFrame(self._inner, fg_color="transparent")

        self._animation_dropdown = LabeledDropdown(self._controls_row, "Style:", CAPTION_ANIMATION_CHOICES)
        self._animation_dropdown.dropdown.configure(command=lambda _v: self._emit())
        self._animation_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        self._position_dropdown = LabeledDropdown(self._controls_row, "Position:", CAPTION_POSITION_CHOICES)
        self._position_dropdown.set("bottom")
        self._position_dropdown.dropdown.configure(command=lambda _v: self._emit())
        self._position_dropdown.pack(side="left")

        self._language_dropdown = LabeledDropdown(self._controls_row, "Language:", _LANGUAGE_DISPLAY_CHOICES)
        self._language_dropdown.set(CAPTION_LANGUAGE_LABELS[DEFAULT_CAPTION_LANGUAGE])
        self._language_dropdown.pack(side="left", padx=(theme.SPACE_MD, 0))

        if self._on_generate_requested is not None:
            ctk.CTkButton(
                self._controls_row, text="📝 Generate & Edit Subtitles", width=190,
                command=self._on_generate_clicked,
            ).pack(side="left", padx=(theme.SPACE_MD, 0))

        self._style_row = ctk.CTkFrame(self._inner, fg_color="transparent")

        self._font_size_entry = ctk.CTkEntry(self._style_row, width=55)
        self._font_size_entry.insert(0, str(DEFAULT_CAPTION_FONT_SIZE))
        ctk.CTkLabel(self._style_row, text="Size:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        self._font_size_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        self._color_entry = ctk.CTkEntry(self._style_row, width=70)
        self._color_entry.insert(0, DEFAULT_CAPTION_COLOR)
        ctk.CTkLabel(self._style_row, text="Color:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        self._color_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        self._highlight_color_entry = ctk.CTkEntry(self._style_row, width=70)
        self._highlight_color_entry.insert(0, DEFAULT_CAPTION_HIGHLIGHT_COLOR)
        ctk.CTkLabel(self._style_row, text="Highlight:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        self._highlight_color_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        self._outline_width_entry = ctk.CTkEntry(self._style_row, width=45)
        self._outline_width_entry.insert(0, "2")
        ctk.CTkLabel(self._style_row, text="Outline:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        self._outline_width_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        self._shadow_offset_entry = ctk.CTkEntry(self._style_row, width=45)
        self._shadow_offset_entry.insert(0, "0")
        ctk.CTkLabel(self._style_row, text="Shadow:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        self._shadow_offset_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        self._background_var = ctk.StringVar(value="on")
        ctk.CTkSwitch(
            self._style_row, text="Background", variable=self._background_var, onvalue="on", offvalue="off",
            command=self._emit, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left")

        for entry in (
            self._font_size_entry, self._color_entry, self._highlight_color_entry,
            self._outline_width_entry, self._shadow_offset_entry,
        ):
            entry.bind("<FocusOut>", lambda _e: self._emit())
            entry.bind("<Return>", lambda _e: self._emit())

        self._lines_status = status_label(self._inner, "", kind="muted")
        self._lines_container = ctk.CTkFrame(self._inner, fg_color="transparent")

    def apply_style(self, style: CaptionStyle) -> None:
        """Fills every style control from `style` and turns captions ON
        - used by the owning dashboard's "Apply Reel Template" action
        (jarvis.video_editor.reel_templates.ReelTemplate.caption_style)
        to set a real starting style in one call, through this panel's
        own normal emit path (never bypassing it) so the result is
        identical to a person manually matching every field by hand."""
        self._toggle_var.set("on")
        self._animation_dropdown.set(style.animation)
        self._position_dropdown.set(style.position)
        for entry, value in (
            (self._font_size_entry, str(style.font_size)),
            (self._color_entry, style.color),
            (self._highlight_color_entry, style.highlight_color),
            (self._outline_width_entry, str(style.outline_width)),
            (self._shadow_offset_entry, str(style.shadow_offset)),
        ):
            entry.delete(0, "end")
            entry.insert(0, value)
        self._background_var.set("on" if style.background else "off")
        self._on_toggle()

    def reset(self) -> None:
        """Captions OFF and no lines - the state of a project that never
        enabled captions (used when another project is opened). Emits
        nothing; the dashboard resets its own caption state itself."""
        self._toggle_var.set("off")
        self._enabled = False
        self._controls_row.pack_forget()
        self._style_row.pack_forget()
        self._lines = []
        for child in self._lines_container.winfo_children():
            child.destroy()
        self._lines_container.pack_forget()
        self._lines_status.pack_forget()

    def _on_generate_clicked(self) -> None:
        self._on_generate_requested(self.get_language())

    def get_language(self) -> str:
        """The person's own currently-selected transcription language
        code (e.g. "lt") - used both by the "Generate & Edit Subtitles"
        button above and by the owning dashboard's own auto-transcribe-
        at-export-time path (when captions are enabled via the toggle
        but this button was never clicked), so BOTH transcription
        entry points honor the same real language choice rather than
        one of them silently falling back to whisper's own "auto"."""
        return _LANGUAGE_CODE_BY_LABEL.get(self._language_dropdown.get(), DEFAULT_CAPTION_LANGUAGE)

    def set_generating_state(self, *, generating: bool) -> None:
        if generating:
            self._lines_status.configure(text="Transcribing speech...")
            self._lines_status.pack(anchor="w", pady=(theme.SPACE_SM, 0))
        else:
            self._lines_status.pack_forget()

    def set_generate_error(self, message: str) -> None:
        self._lines_status.configure(text=f"⚠️ {message}", text_color=theme.DANGER)
        self._lines_status.pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def set_lines(self, lines: list[CaptionLine]) -> None:
        """Called by the owning dashboard once a real transcription has
        been grouped into CaptionLines (jarvis.video_editor.captions
        .group_words_into_lines()) - renders each line's own editable
        text/start/end fields."""
        self._lines = list(lines)
        self._lines_status.pack_forget()
        for child in self._lines_container.winfo_children():
            child.destroy()
        self._lines_container.pack(fill="x", pady=(theme.SPACE_SM, 0))
        for index, line in enumerate(self._lines):
            self._render_line_row(index, line)

        if self._on_export_srt_requested is not None and self._lines:
            ctk.CTkButton(
                self._lines_container, text="💾 Export Subtitles as .SRT", width=200,
                command=lambda: self._on_export_srt_requested(self._lines),
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def _render_line_row(self, index: int, line: CaptionLine) -> None:
        row = Card(self._lines_container)
        row.pack(fill="x", pady=(0, theme.SPACE_XS))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        text_entry = ctk.CTkEntry(inner, width=250)
        text_entry.insert(0, line.text)
        text_entry.pack(fill="x", pady=(0, theme.SPACE_XS))

        timing_row = ctk.CTkFrame(inner, fg_color="transparent")
        timing_row.pack(fill="x")
        start_entry = ctk.CTkEntry(timing_row, width=60)
        start_entry.insert(0, f"{line.start_seconds:g}")
        ctk.CTkLabel(timing_row, text="Start (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        start_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        end_entry = ctk.CTkEntry(timing_row, width=60)
        end_entry.insert(0, f"{line.end_seconds:g}")
        ctk.CTkLabel(timing_row, text="End (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        end_entry.pack(side="left")

        error_label = status_label(inner, "", kind="error")

        def on_change(_event=None) -> None:
            try:
                new_line = CaptionLine(
                    text=text_entry.get(), start_seconds=float(start_entry.get()), end_seconds=float(end_entry.get()),
                )
            except ValueError:
                return
            problems = new_line.validate()
            if problems:
                error_label.configure(text=f"⚠️ {problems[0]}")
                error_label.pack(anchor="w", pady=(theme.SPACE_XS, 0))
            else:
                error_label.pack_forget()
            self._lines[index] = new_line
            self._emit_lines()

        for entry in (text_entry, start_entry, end_entry):
            entry.bind("<FocusOut>", on_change)
            entry.bind("<Return>", on_change)

        ctk.CTkButton(
            inner, text="🗑 Remove Line", width=110, height=24, command=lambda i=index: self._remove_line(i),
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def _remove_line(self, index: int) -> None:
        del self._lines[index]
        self.set_lines(self._lines)
        self._emit_lines()

    def _emit_lines(self) -> None:
        if self._on_lines_changed is not None:
            valid = [line for line in self._lines if not line.validate()]
            self._on_lines_changed(valid)

    def _on_toggle(self) -> None:
        self._enabled = self._toggle_var.get() == "on"
        if self._enabled:
            self._controls_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
            self._style_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        else:
            self._controls_row.pack_forget()
            self._style_row.pack_forget()
        self._emit()

    def _emit(self) -> None:
        if not self._enabled:
            self._on_style_changed(None)
            return
        try:
            font_size = int(self._font_size_entry.get())
            outline_width = int(self._outline_width_entry.get())
            shadow_offset = int(self._shadow_offset_entry.get())
        except ValueError:
            return
        style = CaptionStyle(
            animation=self._animation_dropdown.get(), position=self._position_dropdown.get(),
            font_size=font_size, color=self._color_entry.get().strip() or DEFAULT_CAPTION_COLOR,
            highlight_color=self._highlight_color_entry.get().strip() or DEFAULT_CAPTION_HIGHLIGHT_COLOR,
            outline_width=outline_width, shadow_offset=shadow_offset,
            background=self._background_var.get() == "on",
        )
        self._on_style_changed(style)
