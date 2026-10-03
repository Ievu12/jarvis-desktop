"""Reels mode library panel: big animated word-by-word subtitles
(jarvis.video_editor.reels). Create them from speech or from typed
text, pick a ready-made look or tune every part of it (font, size,
weight, colors, outline, shadow, background, position, animation,
highlighting of the spoken word), mark key words in another color, and
fix each phrase's text.

Like every Video Editor panel this one holds no project state of its
own: it shows the ReelsCaptions the dashboard gives it (set_captions())
and hands every change back through `on_captions_changed`; the
dashboard applies it to the preview, the timeline, undo/redo and the
saved project."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor import reels
from jarvis.video_editor.captions import CAPTION_LANGUAGE_CHOICES, CAPTION_LANGUAGE_LABELS, DEFAULT_CAPTION_LANGUAGE
from jarvis.video_editor.reels_keywords import suggest_keywords
from jarvis.video_editor.text_render import FONT_CHOICES, FONT_LABELS, parse_color

_MAX_PHRASE_ROWS = 300
_LANGUAGE_CODE_BY_LABEL = {label: code for code, label in CAPTION_LANGUAGE_LABELS.items()}

CaptionsCallback = Callable[[reels.ReelsCaptions | None, str, "str | None"], None]
"""(new captions or None to remove them, undo label, coalesce key)."""


def _hex(color: str) -> str:
    r, g, b, _a = parse_color(color)
    return f"#{r:02X}{g:02X}{b:02X}"


def _keep_alpha(old: str, new_hex: str) -> str:
    """`new_hex` with `old`'s "@opacity" suffix, if it had one."""
    return f"{new_hex}@{old.split('@', 1)[1]}" if "@" in old else new_hex


def _time(seconds: float) -> str:
    minutes, secs = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{secs:04.1f}"


class ReelsPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, on_captions_changed: CaptionsCallback, on_transcribe_requested: Callable[[str], None],
        on_seek: Callable[[float], None], get_duration: Callable[[], float], **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_captions_changed = on_captions_changed
        self._on_transcribe_requested = on_transcribe_requested
        self._on_seek = on_seek
        self._get_duration = get_duration
        self._captions: reels.ReelsCaptions | None = None
        self._refreshing = False
        self._value_setters: list[Callable[[reels.ReelsCaptionStyle], None]] = []
        self._phrase_signature: tuple | None = None

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        self._inner = inner

        self._heading(inner, "🎬 REELS SUBTITRAI")
        self._muted(
            inner, "Dideli animuoti subtitrai, sinchroniški su kalba. Sukurkite juos iš kalbos arba įrašykite "
                   "tekstą, pasirinkite stilių ir paryškinkite svarbiausius žodžius.",
        )

        # --- create ---
        create = ctk.CTkFrame(inner, fg_color="transparent")
        create.pack(fill="x", pady=(theme.SPACE_SM, 0))
        self._language = ctk.StringVar(value=CAPTION_LANGUAGE_LABELS[DEFAULT_CAPTION_LANGUAGE])
        ctk.CTkOptionMenu(
            create, values=[CAPTION_LANGUAGE_LABELS[c] for c in CAPTION_LANGUAGE_CHOICES], variable=self._language,
            width=120, fg_color=theme.BG_CARD, button_color=theme.ACCENT_PRIMARY,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        self.transcribe_button = ctk.CTkButton(
            create, text="🎙 Atpažinti kalbą", width=150, command=self._transcribe,
        )
        self.transcribe_button.pack(side="left")
        self._status = status_label(inner, "")
        self._status.pack(anchor="w", pady=(theme.SPACE_XS, 0))

        self._label(inner, "Arba įrašykite tekstą (paskirstomas per visą vaizdo įrašą):")
        self._typed_text = ctk.CTkTextbox(inner, height=54, wrap="word")
        self._typed_text.pack(fill="x")
        typed_row = ctk.CTkFrame(inner, fg_color="transparent")
        typed_row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        ctk.CTkButton(typed_row, text="✍ Sukurti iš teksto", width=150, command=self._create_from_text).pack(side="left")
        self._visible_var = ctk.StringVar(value="on")
        self._visible_switch = ctk.CTkSwitch(
            typed_row, text="Rodyti", variable=self._visible_var, onvalue="on", offvalue="off",
            command=self._on_visible_toggled, text_color=theme.TEXT_PRIMARY,
        )
        self._visible_switch.pack(side="left", padx=(theme.SPACE_MD, 0))
        ctk.CTkButton(
            typed_row, text="🗑 Pašalinti", width=100, command=self._remove, fg_color=theme.BG_CARD,
            hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="right")

        # --- look ---
        self._heading(inner, "🎨 STILIUS", top=theme.SPACE_MD)
        presets = ctk.CTkFrame(inner, fg_color="transparent")
        presets.pack(fill="x")
        self.preset_buttons: dict[str, ctk.CTkButton] = {}
        for n, name in enumerate(reels.REELS_CAPTION_PRESETS):
            button = ctk.CTkButton(
                presets, text=name, width=118, height=28, command=lambda p=name: self._apply_preset(p),
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
            )
            button.grid(row=n // 3, column=n % 3, padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS), sticky="w")
            self.preset_buttons[name] = button

        grid = ctk.CTkFrame(inner, fg_color="transparent")
        grid.pack(fill="x", pady=(theme.SPACE_SM, 0))
        grid.grid_columnconfigure((0, 1), weight=1)
        self._grid = grid

        self._dropdown(grid, 0, 0, "Šriftas", "font", FONT_CHOICES, FONT_LABELS)
        font_file_row = ctk.CTkFrame(grid, fg_color="transparent")
        font_file_row.grid(row=0, column=1, sticky="ew", padx=(theme.SPACE_SM, 0))
        self._label(font_file_row, "Savas šriftas (.ttf)")
        self._font_file_button = ctk.CTkButton(
            font_file_row, text="📂 Pasirinkti", height=28, command=self._choose_font_file,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        )
        self._font_file_button.pack(fill="x")
        self._value_setters.append(lambda s: self._font_file_button.configure(
            text=f"📂 {Path(s.custom_font_path).name}" if s.custom_font_path else "📂 Pasirinkti",
        ))

        self._slider(grid, 1, 0, "Dydis", "font_size", 30, 220, cast=int, fmt="{}")
        self._slider(grid, 1, 1, "Storis", "weight", 0, 3, cast=int, fmt="{}", steps=3)
        self._color(grid, 2, 0, "Teksto spalva", "text_color")
        self._color(grid, 2, 1, "Raktinių žodžių spalva", "highlight_color")
        self._slider(grid, 3, 0, "Kontūras", "outline_width", 0, 20, cast=int, fmt="{}")
        self._color(grid, 3, 1, "Kontūro spalva", "outline_color")
        self._slider(grid, 4, 0, "Šešėlis", "shadow_offset", 0, 20, cast=int, fmt="{}")
        self._slider(grid, 4, 1, "Šešėlio suliejimas", "shadow_blur", 0, 24, cast=int, fmt="{}")
        self._color(grid, 5, 0, "Šešėlio spalva", "shadow_color")
        self._dropdown(grid, 5, 1, "Fonas", "background", reels.PHRASE_BACKGROUND_CHOICES, reels.PHRASE_BACKGROUND_LABELS)
        self._color(grid, 6, 0, "Fono spalva", "background_color")
        self._switch(grid, 6, 1, "DIDŽIOSIOS RAIDĖS", "uppercase")

        self._heading(inner, "📍 VIETA", top=theme.SPACE_MD)
        places = ctk.CTkFrame(inner, fg_color="transparent")
        places.pack(fill="x")
        for key in ("top", "center", "bottom"):
            ctk.CTkButton(
                places, text=reels.POSITION_LABELS[key], width=100, height=28,
                command=lambda k=key: self._edit_style("Subtitrų vieta", None, y_fraction=reels.POSITION_Y_FRACTIONS[k]),
            ).pack(side="left", padx=(0, theme.SPACE_XS))
        place_grid = ctk.CTkFrame(inner, fg_color="transparent")
        place_grid.pack(fill="x")
        place_grid.grid_columnconfigure((0, 1), weight=1)
        self._slider(place_grid, 0, 0, "Aukštis ekrane", "y_fraction", 0.05, 0.95, cast=lambda v: round(v, 3),
                     fmt="{:.0%}", steps=90)
        self._slider(place_grid, 0, 1, "Plotis", "max_width_fraction", 0.4, 0.98, cast=lambda v: round(v, 2),
                     fmt="{:.0%}", steps=58)
        self._muted(inner, "Subtitrus galite tempti ir tiesiog peržiūros lange.")

        self._heading(inner, "✨ ANIMACIJA", top=theme.SPACE_MD)
        animation = ctk.CTkFrame(inner, fg_color="transparent")
        animation.pack(fill="x")
        animation.grid_columnconfigure((0, 1), weight=1)
        self._dropdown(animation, 0, 0, "Frazės animacija", "animation", reels.PHRASE_ANIMATION_CHOICES,
                       reels.PHRASE_ANIMATION_LABELS)
        self._dropdown(animation, 0, 1, "Žodžių rodymas", "reveal", reels.WORD_REVEAL_CHOICES, reels.WORD_REVEAL_LABELS)
        self._dropdown(animation, 1, 0, "Tariamas žodis", "active_effect", reels.ACTIVE_EFFECT_CHOICES,
                       reels.ACTIVE_EFFECT_LABELS)
        self._color(animation, 1, 1, "Tariamo žodžio spalva", "active_color")
        self._color(animation, 2, 0, "Tariamo žodžio fonas", "box_color")
        self._slider(animation, 2, 1, "Greitis", "animation_speed", 0.5, 2.0, cast=lambda v: round(v, 2),
                     fmt="{:.1f}×", steps=30)
        self._slider(animation, 3, 0, "Žodžių frazėje", "max_words", 1, 8, cast=int, fmt="{}", steps=7)
        self._slider(animation, 3, 1, "Raktinio žodžio dydis", "emphasis_scale", 1.0, 1.6,
                     cast=lambda v: round(v, 2), fmt="{:.0%}", steps=12)

        # --- key words ---
        self._heading(inner, "⭐ RAKTINIAI ŽODŽIAI", top=theme.SPACE_MD)
        self._muted(inner, "Įrašykite žodžius per kablelį arba spauskite žodį frazių sąraše.")
        self._keywords_entry = ctk.CTkEntry(inner, placeholder_text="pvz. JARVIS, automatizacija, Reels")
        self._keywords_entry.pack(fill="x")
        key_row = ctk.CTkFrame(inner, fg_color="transparent")
        key_row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        ctk.CTkButton(key_row, text="⭐ Paryškinti", width=110, command=self._emphasize_entry).pack(side="left")
        ctk.CTkButton(key_row, text="✨ Pasiūlyti", width=110, command=self._suggest).pack(
            side="left", padx=(theme.SPACE_XS, 0),
        )
        ctk.CTkButton(
            key_row, text="Išvalyti", width=80, command=self._clear_emphasis, fg_color=theme.BG_CARD,
            hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(theme.SPACE_XS, 0))

        # --- phrases ---
        self._heading(inner, "📝 FRAZĖS", top=theme.SPACE_MD)
        self._muted(inner, "Spauskite laiką, kad peršoktumėte. Pataisykite tekstą ir spauskite Enter. "
                           "Spauskite žodį, kad jį paryškintumėte.")
        self._phrases_frame = ctk.CTkFrame(inner, fg_color="transparent")
        self._phrases_frame.pack(fill="x", pady=(theme.SPACE_XS, 0))

        self.set_captions(None)

    # --- dashboard-facing API ---------------------------------------------------------------

    def set_captions(self, captions: reels.ReelsCaptions | None) -> None:
        """Shows `captions` (from the dashboard's state)."""
        self._captions = captions
        style = captions.style if captions is not None else reels.ReelsCaptionStyle()
        self._refreshing = True
        try:
            for setter in self._value_setters:
                setter(style)
            self._visible_var.set("on" if captions is None or captions.visible else "off")
        finally:
            self._refreshing = False
        self._render_phrases()

    def set_busy(self, busy: bool, message: str = "", *, kind: str = "loading") -> None:
        self.transcribe_button.configure(state="disabled" if busy else "normal")
        self.show_message(message, kind=kind)

    def show_message(self, message: str, *, kind: str = "muted") -> None:
        color = {"loading": theme.ACCENT_PRIMARY, "error": theme.DANGER, "success": theme.SUCCESS}.get(
            kind, theme.TEXT_MUTED,
        )
        self._status.configure(text=message, text_color=color)

    @property
    def language(self) -> str:
        return _LANGUAGE_CODE_BY_LABEL.get(self._language.get(), DEFAULT_CAPTION_LANGUAGE)

    # --- building helpers -------------------------------------------------------------------

    def _heading(self, parent, text: str, *, top: int = 0) -> None:
        ctk.CTkLabel(
            parent, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(top, theme.SPACE_XS))

    def _muted(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=380, justify="left",
        ).pack(anchor="w")

    def _label(self, parent, text: str) -> None:
        ctk.CTkLabel(
            parent, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def _cell(self, parent, row: int, column: int) -> ctk.CTkFrame:
        cell = ctk.CTkFrame(parent, fg_color="transparent")
        cell.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else theme.SPACE_SM, 0))
        return cell

    def _slider(
        self, parent, row: int, column: int, label: str, field: str, low: float, high: float, *, cast, fmt: str,
        steps: int | None = None,
    ) -> None:
        cell = self._cell(parent, row, column)
        self._label(cell, label)
        line = ctk.CTkFrame(cell, fg_color="transparent")
        line.pack(fill="x")
        value_label = ctk.CTkLabel(line, text="", width=40, anchor="e",
                                   font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION))

        def on_move(value: float) -> None:
            value_label.configure(text=fmt.format(cast(value)))
            if not self._refreshing:
                self._edit_style(label, f"style:{field}", **{field: cast(value)})

        slider = ctk.CTkSlider(line, from_=low, to=high, number_of_steps=steps or int(high - low), command=on_move,
                               width=120)
        slider.pack(side="left", fill="x", expand=True)
        value_label.pack(side="left")

        def set_value(style) -> None:
            value = getattr(style, field)
            slider.set(max(low, min(high, value)))
            value_label.configure(text=fmt.format(value))

        self._value_setters.append(set_value)

    def _dropdown(self, parent, row: int, column: int, label: str, field: str, choices, labels: dict) -> None:
        cell = self._cell(parent, row, column)
        self._label(cell, label)
        by_label = {labels.get(c, c): c for c in choices}
        var = ctk.StringVar(value=labels.get(choices[0], choices[0]))

        def on_pick(shown: str) -> None:
            if not self._refreshing:
                self._edit_style(label, None, **{field: by_label[shown]})

        ctk.CTkOptionMenu(
            cell, values=list(by_label), variable=var, command=on_pick, height=28,
            fg_color=theme.BG_CARD, button_color=theme.ACCENT_PRIMARY, button_hover_color=theme.ACCENT_PRIMARY_HOVER,
        ).pack(fill="x")
        self._value_setters.append(lambda style: var.set(labels.get(getattr(style, field), getattr(style, field))))

    def _color(self, parent, row: int, column: int, label: str, field: str) -> None:
        cell = self._cell(parent, row, column)
        self._label(cell, label)
        button = ctk.CTkButton(cell, text="", height=28, border_width=1, border_color=theme.BORDER_SUBTLE)

        def choose() -> None:
            current = getattr(self._style(), field)
            picked = colorchooser.askcolor(color=_hex(current), title=label, parent=self.winfo_toplevel())[1]
            if picked:
                self._edit_style(label, None, **{field: _keep_alpha(current, picked.upper())})

        button.configure(command=choose)
        button.pack(fill="x")

        def set_value(style) -> None:
            color = _hex(getattr(style, field))
            button.configure(fg_color=color, hover_color=color, text=color,
                             text_color="black" if sum(parse_color(color)[:3]) > 380 else "white")

        self._value_setters.append(set_value)

    def _switch(self, parent, row: int, column: int, label: str, field: str) -> None:
        cell = self._cell(parent, row, column)
        var = ctk.StringVar(value="on")

        def on_toggle() -> None:
            if not self._refreshing:
                self._edit_style(label, None, **{field: var.get() == "on"})

        ctk.CTkSwitch(cell, text=label, variable=var, onvalue="on", offvalue="off", command=on_toggle,
                      text_color=theme.TEXT_PRIMARY).pack(anchor="w", pady=(theme.SPACE_MD, 0))
        self._value_setters.append(lambda style: var.set("on" if getattr(style, field) else "off"))

    # --- editing ----------------------------------------------------------------------------

    def _style(self) -> reels.ReelsCaptionStyle:
        return self._captions.style if self._captions is not None else reels.ReelsCaptionStyle()

    def _emit(self, captions: reels.ReelsCaptions | None, label: str, coalesce_key: str | None = None) -> None:
        self._captions = captions
        self._on_captions_changed(captions, label, coalesce_key)
        self._render_phrases()

    def _edit_style(self, label: str, coalesce_key: str | None, **changes) -> None:
        captions = self._captions or reels.ReelsCaptions()
        style = dataclasses.replace(captions.style, **changes)
        if style.validate():
            return
        self._emit(dataclasses.replace(captions, style=style), label, coalesce_key)
        if "y_fraction" in changes and coalesce_key is None:
            self.set_captions(self._captions)  # move the slider too

    def _apply_preset(self, name: str) -> None:
        captions = self._captions or reels.ReelsCaptions()
        self._emit(dataclasses.replace(captions, style=reels.apply_caption_preset(captions.style, name)),
                   f"Stilius „{name}“")
        self.set_captions(self._captions)

    def _choose_font_file(self) -> None:
        path = filedialog.askopenfilename(
            title="Pasirinkite šriftą", filetypes=[("Šriftai", "*.ttf *.otf"), ("Visi failai", "*.*")],
            parent=self.winfo_toplevel(),
        )
        if path:
            self._edit_style("Savas šriftas", None, custom_font_path=path)
            self.set_captions(self._captions)

    def _on_visible_toggled(self) -> None:
        if self._captions is not None:
            self._emit(dataclasses.replace(self._captions, visible=self._visible_var.get() == "on"), "Rodyti subtitrus")

    def _transcribe(self) -> None:
        self._on_transcribe_requested(self.language)

    def _create_from_text(self) -> None:
        text = self._typed_text.get("1.0", "end").strip()
        duration = self._get_duration()
        if not text:
            self.show_message("Įrašykite tekstą.", kind="error")
            return
        if duration <= 0:
            self.show_message("Pirmiausia pridėkite klipą į laiko juostą.", kind="error")
            return
        words = reels.words_from_text(text, start_seconds=0.0, end_seconds=duration)
        captions = self._captions or reels.ReelsCaptions()
        self._emit(dataclasses.replace(captions, words=words, visible=True), "Subtitrai iš teksto")
        self.show_message(f"Sukurta {len(words)} žodžių. Laiką galite koreguoti laiko juostoje.", kind="success")

    def _remove(self) -> None:
        if self._captions is not None and self._captions.words:
            self._emit(dataclasses.replace(self._captions, words=()), "Pašalinti Reels subtitrai")

    def keywords(self) -> list[str]:
        return [part.strip() for part in self._keywords_entry.get().split(",") if part.strip()]

    def _emphasize_entry(self) -> None:
        if self._captions is None or not self._captions.words:
            return
        words = reels.emphasize_matching(self._captions.words, self.keywords())
        self._emit(dataclasses.replace(self._captions, words=words), "Raktiniai žodžiai")

    def _suggest(self) -> None:
        if self._captions is None or not self._captions.words:
            self.show_message("Pirmiausia sukurkite subtitrus.", kind="error")
            return
        suggested = suggest_keywords(self._captions.words, own_words=self.keywords())
        if not suggested:
            self.show_message("Raktinių žodžių nerasta.", kind="muted")
            return
        self._keywords_entry.delete(0, "end")
        self._keywords_entry.insert(0, ", ".join(suggested))
        words = reels.emphasize_matching(self._captions.words, suggested)
        self._emit(dataclasses.replace(self._captions, words=words), "Pasiūlyti raktiniai žodžiai")
        self.show_message(f"Paryškinta: {', '.join(suggested)}", kind="success")

    def _clear_emphasis(self) -> None:
        if self._captions is None:
            return
        words = tuple(dataclasses.replace(w, emphasized=False) for w in self._captions.words)
        self._emit(dataclasses.replace(self._captions, words=words), "Išvalyti raktiniai žodžiai")

    # --- phrase list ------------------------------------------------------------------------

    def _render_phrases(self) -> None:
        captions = self._captions
        words = captions.words if captions is not None else ()
        phrases = reels.build_phrases(words, captions.style) if captions is not None else []
        signature = (words, tuple((p.first, p.last) for p in phrases))
        if signature == self._phrase_signature:
            return
        self._phrase_signature = signature
        for child in self._phrases_frame.winfo_children():
            child.destroy()
        if not phrases:
            ctk.CTkLabel(
                self._phrases_frame, text="Subtitrų dar nėra.", text_color=theme.TEXT_MUTED, anchor="w",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(anchor="w")
            return
        for n, phrase in enumerate(phrases[:_MAX_PHRASE_ROWS]):
            self._phrase_row(n, phrase, words)
        if len(phrases) > _MAX_PHRASE_ROWS:
            self._muted(self._phrases_frame, f"… ir dar {len(phrases) - _MAX_PHRASE_ROWS} frazės (laiko juostoje).")

    def _phrase_row(self, n: int, phrase: reels.Phrase, words) -> None:
        row = tk.Frame(self._phrases_frame, bg=theme.BG_CARD)
        row.pack(fill="x", pady=(0, 2))
        top = tk.Frame(row, bg=theme.BG_CARD)
        top.pack(fill="x")
        time_label = tk.Label(
            top, text=f"▶ {_time(phrase.start_seconds)}", bg=theme.BG_CARD, fg=theme.ACCENT_PRIMARY, cursor="hand2",
            font=(theme.FONT_FAMILY_BODY, 9),
        )
        time_label.pack(side="left", padx=(4, 6))
        time_label.bind("<Button-1>", lambda _e, t=phrase.start_seconds: self._on_seek(t + 0.01))
        for index in range(phrase.first, phrase.last + 1):
            word = words[index]
            chip = tk.Label(
                top, text=word.text, bg=theme.BG_CARD, cursor="hand2", font=(theme.FONT_FAMILY_BODY, 10, "bold"),
                fg="#FFD400" if word.emphasized else theme.TEXT_PRIMARY,
            )
            chip.pack(side="left", padx=1)
            chip.bind("<Button-1>", lambda _e, i=index: self._toggle_emphasis(i))
        delete = tk.Label(top, text="✕", bg=theme.BG_CARD, fg=theme.TEXT_MUTED, cursor="hand2")
        delete.pack(side="right", padx=4)
        delete.bind("<Button-1>", lambda _e, p=phrase: self._delete_phrase(p))

        entry = ctk.CTkEntry(row, height=26)
        entry.insert(0, " ".join(words[i].text for i in range(phrase.first, phrase.last + 1)))
        entry.pack(fill="x", padx=4, pady=(0, 4))
        entry.bind("<Return>", lambda _e, p=phrase, e=entry: self._retext(p, e.get()))

    def _toggle_emphasis(self, index: int) -> None:
        if self._captions is None or index >= len(self._captions.words):
            return
        words = reels.set_emphasis(self._captions.words, index, not self._captions.words[index].emphasized)
        self._emit(dataclasses.replace(self._captions, words=words), "Raktinis žodis")

    def _retext(self, phrase: reels.Phrase, text: str) -> None:
        if self._captions is None or not text.strip():
            return
        words = reels.retext_phrase(self._captions.words, phrase, text.strip())
        self._emit(dataclasses.replace(self._captions, words=words), "Pataisyta frazė")

    def _delete_phrase(self, phrase: reels.Phrase) -> None:
        if self._captions is None:
            return
        words = self._captions.words[:phrase.first] + self._captions.words[phrase.last + 1:]
        self._emit(dataclasses.replace(self._captions, words=words), "Ištrinta frazė")
