"""Speech sync panel: search for a spoken phrase (requirement 5 -
"ištarus 'Nuostabus pasiūlymas', ekrane atsiranda animuotas tekstas" -
when a phrase is spoken, show an effect) within the ALREADY-transcribed
word timings (the same real WordTiming list "📝 Generate & Edit
Subtitles" produces - this panel never transcribes anything itself, it
only searches text already transcribed). Real, exact text matching via
jarvis.video_editor.speech_sync.find_phrase_matches() - this is cheap
and synchronous (no ffmpeg call), so it runs directly on the GUI thread,
unlike every transcription/export action in this package."""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor.captions import WordTiming
from jarvis.video_editor.speech_sync import find_phrase_matches


class SpeechSyncPanel(ctk.CTkFrame):
    def __init__(self, master, *, get_words: Callable[[], list[WordTiming]], **kwargs) -> None:
        """`get_words()` is called fresh on every search (not cached at
        construction time) - returns the owning dashboard's own most
        recently transcribed WordTiming list (possibly empty if
        "Generate & Edit Subtitles" was never clicked), so a search
        always reflects the CURRENT transcription, not a stale one from
        when this panel was built."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._get_words = get_words

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="🎙️ SPEECH SYNC",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner,
            text="Find exactly when a word or phrase is spoken (uses the subtitles already generated in "
                 "📝 Tekstas ir subtitrai) - copy a timestamp into any sticker's or text overlay's own Start field.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        search_row = ctk.CTkFrame(inner, fg_color="transparent")
        search_row.pack(fill="x")
        self._phrase_entry = ctk.CTkEntry(search_row, width=220, placeholder_text="e.g. great offer")
        self._phrase_entry.pack(side="left", padx=(0, theme.SPACE_SM))
        self._phrase_entry.bind("<Return>", lambda _e: self._on_search_clicked())
        ctk.CTkButton(search_row, text="🔍 Find", command=self._on_search_clicked, width=100).pack(side="left")

        self._results_container = ctk.CTkFrame(inner, fg_color="transparent")
        self._results_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

    def _on_search_clicked(self) -> None:
        phrase = self._phrase_entry.get().strip()
        for child in self._results_container.winfo_children():
            child.destroy()

        if not phrase:
            return
        words = self._get_words()
        if not words:
            status_label(
                self._results_container,
                "No subtitles generated yet - click \"Generate & Edit Subtitles\" first.", kind="error",
            ).pack(anchor="w")
            return

        matches = find_phrase_matches(words, phrase)
        if not matches:
            status_label(self._results_container, f"\"{phrase}\" was not found in the transcribed speech.", kind="muted").pack(anchor="w")
            return

        for match in matches:
            ctk.CTkLabel(
                self._results_container,
                text=f"  \"{match.matched_text}\" at {match.start_seconds:.2f}s - {match.end_seconds:.2f}s",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")
