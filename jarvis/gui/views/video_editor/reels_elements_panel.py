"""Reels mode library panels for the pop-up text cards and the
picture/video inserts (jarvis.video_editor.reels.TextCard /
MediaInsert), plus ReelsTabs, which puts them next to the Reels
subtitles panel under one "📱 Reels" category.

Each panel lists its elements, adds new ones at the playhead and shows
every setting of the selected one. Like every Video Editor panel it
owns no project state: the dashboard hands it the current elements
(set_items()) and the selection (select()), and receives every change
through `on_items_changed`."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from tkinter import colorchooser, filedialog
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor import reels
from jarvis.video_editor.text_render import FONT_CHOICES, FONT_LABELS, parse_color

ItemsCallback = Callable[[tuple, str, "str | None"], None]
"""(all elements of this kind, undo label, coalesce key)."""

_SECONDARY = dict(
    fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
)


def _hex(color: str) -> str:
    r, g, b, _a = parse_color(color)
    return f"#{r:02X}{g:02X}{b:02X}"


def _keep_alpha(old: str, new_hex: str) -> str:
    return f"{new_hex}@{old.split('@', 1)[1]}" if "@" in old else new_hex


def _time(seconds: float) -> str:
    minutes, secs = divmod(max(0.0, seconds), 60)
    return f"{int(minutes):02d}:{secs:04.1f}"


def _small(parent, text: str, *, color=theme.TEXT_SECONDARY) -> ctk.CTkLabel:
    return ctk.CTkLabel(
        parent, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        text_color=color, anchor="w", wraplength=380, justify="left",
    )


def _heading(parent, text: str, *, top: int = 0) -> None:
    ctk.CTkLabel(
        parent, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
        text_color=theme.ACCENT_PRIMARY, anchor="w",
    ).pack(anchor="w", pady=(top, theme.SPACE_XS))


class _ElementsPanel(ctk.CTkFrame):
    """List + settings form shared by the cards and inserts panels."""

    title = ""
    hint = ""
    noun = "elementas"

    def __init__(
        self, master, *, on_items_changed: ItemsCallback, on_select: Callable[[int | None], None],
        on_seek: Callable[[float], None], get_time: Callable[[], float], get_duration: Callable[[], float], **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_items_changed = on_items_changed
        self._on_select = on_select
        self._on_seek = on_seek
        self._get_time = get_time
        self._get_duration = get_duration
        self._items: tuple = ()
        self._selected: int | None = None
        self._refreshing = False
        self._setters: list[Callable[[object], None]] = []
        self._form_for: int | None = None
        self._list_signature: tuple | None = None

        card = Card(self)
        card.pack(fill="x")
        self._inner = ctk.CTkFrame(card, fg_color="transparent")
        self._inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        _heading(self._inner, self.title)
        _small(self._inner, self.hint, color=theme.TEXT_MUTED).pack(anchor="w")
        self._add_area = ctk.CTkFrame(self._inner, fg_color="transparent")
        self._add_area.pack(fill="x", pady=(theme.SPACE_SM, 0))
        self._build_add_area(self._add_area)
        self._status = status_label(self._inner, "")
        self._status.pack(anchor="w", pady=(theme.SPACE_XS, 0))
        _heading(self._inner, "📋 SĄRAŠAS", top=theme.SPACE_SM)
        self._list = ctk.CTkFrame(self._inner, fg_color="transparent")
        self._list.pack(fill="x")
        self._form = ctk.CTkFrame(self._inner, fg_color="transparent")
        self._form.pack(fill="x", pady=(theme.SPACE_SM, 0))
        self._render_list()

    # --- dashboard-facing API ---------------------------------------------------------------

    def set_items(self, items: tuple) -> None:
        self._items = tuple(items)
        if self._selected is not None and self._selected >= len(self._items):
            self._selected = None
        self._render_list()
        self._render_form()

    def select(self, index: int | None) -> None:
        self._selected = index if index is not None and 0 <= index < len(self._items) else None
        self._render_list(force=True)
        self._render_form()

    @property
    def selected(self) -> int | None:
        return self._selected

    def show_message(self, message: str, *, kind: str = "muted") -> None:
        color = {"error": theme.DANGER, "success": theme.SUCCESS, "loading": theme.ACCENT_PRIMARY}.get(
            kind, theme.TEXT_MUTED,
        )
        self._status.configure(text=message, text_color=color)

    # --- subclass hooks ---------------------------------------------------------------------

    def _build_add_area(self, parent) -> None:
        raise NotImplementedError

    def _label_for(self, item) -> str:
        raise NotImplementedError

    def _build_form(self, parent) -> None:
        raise NotImplementedError

    # --- adding / editing -------------------------------------------------------------------

    def _window(self, length: float) -> tuple[float, float] | None:
        duration = self._get_duration()
        if duration <= 0:
            self.show_message("Pirmiausia pridėkite klipą į laiko juostą.", kind="error")
            return None
        start = min(round(self._get_time(), 2), max(0.0, round(duration - 0.5, 2)))
        return start, round(min(duration, start + length), 2)

    def _add(self, item, label: str) -> None:
        items = self._items + (item,)
        self._items = items
        self._selected = len(items) - 1
        self._on_items_changed(items, label, None)
        self._on_select(self._selected)
        self._render_list(force=True)
        self._render_form()

    def _current(self):
        return self._items[self._selected] if self._selected is not None else None

    def _edit(self, label: str, coalesce_key: str | None, **changes) -> None:
        item = self._current()
        if item is None:
            return
        new_item = dataclasses.replace(item, **changes)
        problems = new_item.validate()
        if problems:
            self.show_message(problems[0], kind="error")
            return
        self.show_message("")
        index = self._selected
        self._items = self._items[:index] + (new_item,) + self._items[index + 1:]
        self._on_items_changed(self._items, label, coalesce_key)
        self._render_list()

    def _delete(self, index: int) -> None:
        self._items = self._items[:index] + self._items[index + 1:]
        if self._selected == index:
            self._selected = None
        elif self._selected is not None and self._selected > index:
            self._selected -= 1
        self._on_items_changed(self._items, f"Ištrinta: {self.noun}", None)
        self._on_select(self._selected)
        self._render_list(force=True)
        self._render_form()

    # --- list -------------------------------------------------------------------------------

    def _render_list(self, *, force: bool = False) -> None:
        signature = (tuple((self._label_for(i), i.start_seconds) for i in self._items), self._selected)
        if signature == self._list_signature and not force:
            return
        self._list_signature = signature
        for child in self._list.winfo_children():
            child.destroy()
        if not self._items:
            _small(self._list, "Dar nieko nėra.", color=theme.TEXT_MUTED).pack(anchor="w")
            return
        for index, item in enumerate(self._items):
            selected = index == self._selected
            row = tk.Frame(self._list, bg=theme.ACCENT_GLOW if selected else theme.BG_CARD, cursor="hand2")
            row.pack(fill="x", pady=(0, 2))
            time_label = tk.Label(row, text=f"▶ {_time(item.start_seconds)}", bg=row["bg"], fg=theme.ACCENT_PRIMARY,
                                  font=(theme.FONT_FAMILY_BODY, 9))
            time_label.pack(side="left", padx=(4, 6), pady=3)
            time_label.bind("<Button-1>", lambda _e, t=item.start_seconds: self._on_seek(t + 0.01))
            name = tk.Label(row, text=self._label_for(item), bg=row["bg"], fg=theme.TEXT_PRIMARY,
                            font=(theme.FONT_FAMILY_BODY, 10, "bold"), anchor="w")
            name.pack(side="left", fill="x", expand=True)
            for widget in (row, name):
                widget.bind("<Button-1>", lambda _e, i=index: self._pick(i))
            delete = tk.Label(row, text="✕", bg=row["bg"], fg=theme.TEXT_MUTED, cursor="hand2")
            delete.pack(side="right", padx=6)
            delete.bind("<Button-1>", lambda _e, i=index: self._delete(i))

    def _pick(self, index: int) -> None:
        self._selected = index
        self._on_select(index)
        item = self._items[index]
        if not item.start_seconds <= self._get_time() < item.end_seconds:
            self._on_seek(item.start_seconds + min(0.5, (item.end_seconds - item.start_seconds) / 2))
        self._render_list(force=True)
        self._render_form()

    # --- settings form ----------------------------------------------------------------------

    def _render_form(self) -> None:
        item = self._current()
        if self._form_for != self._selected or item is None:
            for child in self._form.winfo_children():
                child.destroy()
            self._setters = []
            self._form_for = self._selected
            if item is None:
                if self._items:
                    _small(self._form, f"Pasirinkite sąraše arba peržiūroje, ką nustatyti.",
                           color=theme.TEXT_MUTED).pack(anchor="w")
                return
            _heading(self._form, "⚙ NUSTATYMAI", top=theme.SPACE_SM)
            self._build_form(self._form)
        self._refreshing = True
        try:
            for setter in self._setters:
                setter(item)
        finally:
            self._refreshing = False

    def _grid(self, parent) -> ctk.CTkFrame:
        grid = ctk.CTkFrame(parent, fg_color="transparent")
        grid.pack(fill="x")
        grid.grid_columnconfigure((0, 1), weight=1)
        return grid

    def _cell(self, grid, row: int, column: int, label: str | None) -> ctk.CTkFrame:
        cell = ctk.CTkFrame(grid, fg_color="transparent")
        cell.grid(row=row, column=column, sticky="ew", padx=(0 if column == 0 else theme.SPACE_SM, 0))
        if label:
            _small(cell, label).pack(anchor="w", pady=(theme.SPACE_XS, 0))
        return cell

    def _slider(self, grid, row, column, label, field, low, high, *, cast=float, fmt="{:.2f}", steps=None) -> None:
        cell = self._cell(grid, row, column, label)
        line = ctk.CTkFrame(cell, fg_color="transparent")
        line.pack(fill="x")
        value_label = ctk.CTkLabel(line, text="", width=44, anchor="e",
                                   font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION))

        def on_move(value: float) -> None:
            value_label.configure(text=fmt.format(cast(value)))
            if not self._refreshing:
                self._edit(label, f"{field}:{self._selected}", **{field: cast(value)})

        slider = ctk.CTkSlider(line, from_=low, to=high, number_of_steps=steps or 100, command=on_move, width=120)
        slider.pack(side="left", fill="x", expand=True)
        value_label.pack(side="left")

        def set_value(item) -> None:
            value = getattr(item, field)
            slider.set(max(low, min(high, value)))
            value_label.configure(text=fmt.format(value))

        self._setters.append(set_value)

    def _dropdown(self, grid, row, column, label, field, choices, labels) -> None:
        cell = self._cell(grid, row, column, label)
        by_label = {labels.get(c, c): c for c in choices}
        var = ctk.StringVar()

        def on_pick(shown: str) -> None:
            if not self._refreshing:
                self._edit(label, None, **{field: by_label[shown]})

        ctk.CTkOptionMenu(cell, values=list(by_label), variable=var, command=on_pick, height=28,
                          fg_color=theme.BG_CARD, button_color=theme.ACCENT_PRIMARY).pack(fill="x")
        self._setters.append(lambda item: var.set(labels.get(getattr(item, field), getattr(item, field))))

    def _color(self, grid, row, column, label, field) -> None:
        cell = self._cell(grid, row, column, label)
        button = ctk.CTkButton(cell, text="", height=28, border_width=1, border_color=theme.BORDER_SUBTLE)

        def choose() -> None:
            current = getattr(self._current(), field)
            picked = colorchooser.askcolor(color=_hex(current), title=label, parent=self.winfo_toplevel())[1]
            if picked:
                self._edit(label, None, **{field: _keep_alpha(current, picked.upper())})
                self._render_form()

        button.configure(command=choose)
        button.pack(fill="x")

        def set_value(item) -> None:
            color = _hex(getattr(item, field))
            button.configure(fg_color=color, hover_color=color, text=color,
                             text_color="black" if sum(parse_color(color)[:3]) > 380 else "white")

        self._setters.append(set_value)

    def _switch(self, grid, row, column, label, field) -> None:
        cell = self._cell(grid, row, column, None)
        var = ctk.StringVar(value="on")

        def on_toggle() -> None:
            if not self._refreshing:
                self._edit(label, None, **{field: var.get() == "on"})

        ctk.CTkSwitch(cell, text=label, variable=var, onvalue="on", offvalue="off", command=on_toggle,
                      text_color=theme.TEXT_PRIMARY).pack(anchor="w", pady=(theme.SPACE_MD, 0))
        self._setters.append(lambda item: var.set("on" if getattr(item, field) else "off"))

    def _timing(self, parent) -> None:
        _small(parent, "Rodoma (s): nuo – iki").pack(anchor="w", pady=(theme.SPACE_XS, 0))
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        entries = {}
        for field in ("start_seconds", "end_seconds"):
            entry = ctk.CTkEntry(row, width=70)
            entry.pack(side="left", padx=(0, theme.SPACE_XS))
            entries[field] = entry

            def commit(_event=None, field=field, entry=entry) -> None:
                try:
                    value = round(float(entry.get().replace(",", ".")), 2)
                except ValueError:
                    return
                if self._current() is not None and value != getattr(self._current(), field):
                    self._edit("Laikas", None, **{field: value})

            entry.bind("<Return>", commit)
            entry.bind("<FocusOut>", commit)
        ctk.CTkButton(row, text="⏱ Nuo dabar", width=90, height=28, command=self._start_at_playhead, **_SECONDARY).pack(
            side="left",
        )

        def set_value(item) -> None:
            for field, entry in entries.items():
                text = f"{getattr(item, field):g}"
                if entry.get() != text and entry.focus_get() is not entry:
                    entry.delete(0, "end")
                    entry.insert(0, text)

        self._setters.append(set_value)

    def _start_at_playhead(self) -> None:
        item = self._current()
        if item is None:
            return
        start = round(self._get_time(), 2)
        self._edit("Laikas", None, start_seconds=start, end_seconds=round(start + item.end_seconds - item.start_seconds, 2))
        self._render_form()

    def _animations(self, parent, enter_choices, exit_choices) -> None:
        grid = self._grid(parent)
        self._dropdown(grid, 0, 0, "Įėjimo animacija", "enter_animation", enter_choices, reels.ENTER_ANIMATION_LABELS)
        self._dropdown(grid, 0, 1, "Išėjimo animacija", "exit_animation", exit_choices, reels.EXIT_ANIMATION_LABELS)
        self._slider(grid, 1, 0, "Įėjimo trukmė", "enter_seconds", 0.0, 2.0, cast=lambda v: round(v, 2),
                     fmt="{:.2f} s", steps=40)
        self._slider(grid, 1, 1, "Išėjimo trukmė", "exit_seconds", 0.0, 2.0, cast=lambda v: round(v, 2),
                     fmt="{:.2f} s", steps=40)
        self._slider(grid, 2, 0, "Pasukimas", "rotation_degrees", -180, 180, cast=lambda v: round(v, 1),
                     fmt="{:.0f}°", steps=360)
        self._slider(grid, 2, 1, "Permatomumas", "opacity", 0.0, 1.0, cast=lambda v: round(v, 2), fmt="{:.0%}",
                     steps=20)


# --- text cards ------------------------------------------------------------------------------------


class CardsPanel(_ElementsPanel):
    title = "🏷 TEKSTO KORTELĖS"
    hint = ("Mažos animuotos kortelės vaizdo viršuje ar šonuose. Spauskite žodį arba įrašykite savo. "
            "Kortelę galite tempti, didinti ir sukti tiesiog peržiūroje.")
    noun = "kortelė"

    def _build_add_area(self, parent) -> None:
        words = ctk.CTkFrame(parent, fg_color="transparent")
        words.pack(fill="x")
        for n, word in enumerate(reels.CARD_QUICK_WORDS):
            ctk.CTkButton(words, text=word, height=26, width=10, command=lambda w=word: self._add_card(w),
                          **_SECONDARY).grid(row=n // 3, column=n % 3, padx=(0, theme.SPACE_XS),
                                             pady=(0, theme.SPACE_XS), sticky="w")
        own = ctk.CTkFrame(parent, fg_color="transparent")
        own.pack(fill="x")
        self._text_entry = ctk.CTkEntry(own, placeholder_text="Savas tekstas")
        self._text_entry.pack(side="left", fill="x", expand=True, padx=(0, theme.SPACE_XS))
        self._text_entry.bind("<Return>", lambda _e: self._add_own())
        ctk.CTkButton(own, text="➕ Pridėti", width=90, command=self._add_own).pack(side="left")
        _small(parent, "Dizainas naujai kortelei:").pack(anchor="w", pady=(theme.SPACE_XS, 0))
        self._new_design = ctk.StringVar(value=reels.CARD_DESIGN_LABELS["pill"])
        ctk.CTkSegmentedButton(
            parent, values=[reels.CARD_DESIGN_LABELS[d] for d in reels.CARD_DESIGN_CHOICES], variable=self._new_design,
        ).pack(fill="x")

    def _add_own(self) -> None:
        text = self._text_entry.get().strip()
        if not text:
            self.show_message("Įrašykite kortelės tekstą.", kind="error")
            return
        self._add_card(text)
        self._text_entry.delete(0, "end")

    def _add_card(self, text: str) -> None:
        window = self._window(2.5)
        if window is None:
            return
        design = next(d for d, label in reels.CARD_DESIGN_LABELS.items() if label == self._new_design.get())
        # Cards on screen at the same time are stacked so they don't cover each other.
        busy = [c for c in self._items if c.start_seconds < window[1] and window[0] < c.end_seconds]
        y = 0.14 + 0.08 * (len(busy) % 6)
        card = reels.apply_card_design(reels.TextCard(text, window[0], window[1], y_fraction=round(y, 3)), design)
        self._add(card, "Nauja kortelė")
        self.show_message(f"Pridėta kortelė „{text}“.", kind="success")

    def _label_for(self, item) -> str:
        return f"{item.text}  ·  {reels.CARD_DESIGN_LABELS.get(item.design, item.design)}"

    def _build_form(self, parent) -> None:
        _small(parent, "Tekstas").pack(anchor="w")
        entry = ctk.CTkEntry(parent)
        entry.pack(fill="x")

        def commit(_event=None) -> None:
            text = entry.get().strip()
            if text and self._current() is not None and text != self._current().text:
                self._edit("Kortelės tekstas", None, text=text)

        entry.bind("<Return>", commit)
        entry.bind("<FocusOut>", commit)

        def set_text(item) -> None:
            if entry.get() != item.text and entry.focus_get() is not entry:
                entry.delete(0, "end")
                entry.insert(0, item.text)

        self._setters.append(set_text)
        self._timing(parent)

        _small(parent, "Dizainas").pack(anchor="w", pady=(theme.SPACE_XS, 0))
        designs = ctk.CTkFrame(parent, fg_color="transparent")
        designs.pack(fill="x")
        for n, design in enumerate(reels.CARD_DESIGN_CHOICES):
            ctk.CTkButton(designs, text=reels.CARD_DESIGN_LABELS[design], height=26, width=110,
                          command=lambda d=design: self._apply_design(d), **_SECONDARY).grid(
                row=n // 3, column=n % 3, padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS), sticky="w")

        grid = self._grid(parent)
        self._color(grid, 0, 0, "Teksto spalva", "text_color")
        self._color(grid, 0, 1, "Fono spalva", "background_color")
        self._color(grid, 1, 0, "Akcento spalva", "accent_color")
        self._dropdown(grid, 1, 1, "Šriftas", "font", FONT_CHOICES, FONT_LABELS)
        self._slider(grid, 2, 0, "Dydis", "scale", 0.3, 3.0, cast=lambda v: round(v, 2), fmt="{:.0%}", steps=54)
        self._switch(grid, 2, 1, "DIDŽIOSIOS", "uppercase")
        self._switch(grid, 3, 0, "Šešėlis", "shadow")

        _small(parent, "Vieta").pack(anchor="w", pady=(theme.SPACE_XS, 0))
        places = ctk.CTkFrame(parent, fg_color="transparent")
        places.pack(fill="x")
        spots = (("↖", 0.25, 0.12), ("⬆", 0.5, 0.12), ("↗", 0.75, 0.12),
                 ("⬅", 0.22, 0.45), ("⏺", 0.5, 0.45), ("➡", 0.78, 0.45))
        for text, x, y in spots:
            ctk.CTkButton(places, text=text, width=40, height=28, command=lambda x=x, y=y: self._move(x, y),
                          **_SECONDARY).pack(side="left", padx=(0, theme.SPACE_XS))
        grid = self._grid(parent)
        self._slider(grid, 0, 0, "Horizontaliai", "x_fraction", 0.0, 1.0, cast=lambda v: round(v, 3), fmt="{:.0%}")
        self._slider(grid, 0, 1, "Vertikaliai", "y_fraction", 0.0, 1.0, cast=lambda v: round(v, 3), fmt="{:.0%}")
        self._animations(parent, reels.ENTER_ANIMATION_CHOICES, reels.EXIT_ANIMATION_CHOICES)

    def _apply_design(self, design: str) -> None:
        item = self._current()
        if item is None:
            return
        new_item = reels.apply_card_design(item, design)
        self._edit("Kortelės dizainas", None, **{f: getattr(new_item, f) for f in
                                                  ("design", "text_color", "background_color", "accent_color")})
        self._render_form()

    def _move(self, x: float, y: float) -> None:
        self._edit("Kortelės vieta", None, x_fraction=x, y_fraction=y)
        self._render_form()


# --- picture / video inserts -------------------------------------------------------------------------


class InsertsPanel(_ElementsPanel):
    title = "🖼 VAIZDO INTARPAI"
    hint = ("Nuotrauka, ekrano vaizdas ar trumpas video, rodomas kalbant. Pasirinkite vietą, dydį, apvalius "
            "kampus, šešėlį ir animaciją.")
    noun = "intarpas"

    def __init__(self, master, *, on_file_chosen: Callable[[str], None],
                 get_project_media: Callable[[], list[tuple[str, str]]], **kwargs) -> None:
        self._on_file_chosen = on_file_chosen
        self._get_project_media = get_project_media
        super().__init__(master, **kwargs)

    def _build_add_area(self, parent) -> None:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkButton(row, text="📂 Įkelti nuotrauką ar video", command=self._choose_file).pack(side="left")
        self._media_var = ctk.StringVar(value="Iš projekto medijos…")
        self._media_menu = ctk.CTkOptionMenu(
            row, values=["Iš projekto medijos…"], variable=self._media_var, command=self._pick_project_media,
            fg_color=theme.BG_CARD, button_color=theme.ACCENT_PRIMARY, width=170,
        )
        self._media_menu.pack(side="left", padx=(theme.SPACE_XS, 0))
        self._media_menu.bind("<Enter>", lambda _e: self._refresh_media_menu(), add="+")

    def _refresh_media_menu(self) -> None:
        names = [name for name, _path in self._get_project_media()]
        self._media_menu.configure(values=names or ["(nėra įkeltos medijos)"])

    def _pick_project_media(self, name: str) -> None:
        self._media_var.set("Iš projekto medijos…")
        for media_name, path in self._get_project_media():
            if media_name == name:
                self._on_file_chosen(path)
                return

    def _choose_file(self) -> None:
        patterns = " ".join(f"*{e}" for e in reels.INSERT_IMAGE_EXTENSIONS + reels.INSERT_VIDEO_EXTENSIONS)
        path = filedialog.askopenfilename(
            title="Pasirinkite nuotrauką ar vaizdo įrašą", parent=self.winfo_toplevel(),
            filetypes=[("Nuotraukos ir video", patterns), ("Visi failai", "*.*")],
        )
        if path:
            self._on_file_chosen(path)

    def add_insert(self, insert: reels.MediaInsert) -> None:
        """Called by the dashboard once a chosen file is ready."""
        busy = [i for i in self._items if i.start_seconds < insert.end_seconds and insert.start_seconds < i.end_seconds]
        if busy:  # another insert is on screen then: use the other top corner
            x = 0.27 if busy[-1].x_fraction > 0.5 else 0.73
            insert = dataclasses.replace(insert, x_fraction=x)
        self._add(insert, "Naujas intarpas")
        self.show_message(f"Pridėta: {insert.name}", kind="success")

    def window(self, length: float) -> tuple[float, float] | None:
        return self._window(length)

    def _label_for(self, item) -> str:
        return f"{'🎞' if item.kind == 'video' else '🖼'} {item.name}"

    def _build_form(self, parent) -> None:
        self._timing(parent)
        _small(parent, "Vieta").pack(anchor="w", pady=(theme.SPACE_XS, 0))
        places = ctk.CTkFrame(parent, fg_color="transparent")
        places.pack(fill="x")
        for n, (key, label) in enumerate(reels.INSERT_POSITION_LABELS.items()):
            ctk.CTkButton(places, text=label, height=26, width=130, command=lambda k=key: self._place(k),
                          **_SECONDARY).grid(row=n // 3, column=n % 3, padx=(0, theme.SPACE_XS),
                                             pady=(0, theme.SPACE_XS), sticky="w")
        grid = self._grid(parent)
        self._slider(grid, 0, 0, "Horizontaliai", "x_fraction", 0.0, 1.0, cast=lambda v: round(v, 3), fmt="{:.0%}")
        self._slider(grid, 0, 1, "Vertikaliai", "y_fraction", 0.0, 1.0, cast=lambda v: round(v, 3), fmt="{:.0%}")
        self._slider(grid, 1, 0, "Dydis (plotis)", "width_fraction", 0.1, 1.0, cast=lambda v: round(v, 3),
                     fmt="{:.0%}", steps=90)
        self._slider(grid, 1, 1, "Apvalūs kampai", "corner_radius", 0.0, 0.5, cast=lambda v: round(v, 3),
                     fmt="{:.0%}", steps=50)
        self._slider(grid, 2, 0, "Šešėlis", "shadow", 0.0, 1.0, cast=lambda v: round(v, 2), fmt="{:.0%}", steps=20)
        self._slider(grid, 2, 1, "Rėmelis", "border_width", 0, 20, cast=int, fmt="{} px", steps=20)
        self._color(grid, 3, 0, "Rėmelio spalva", "border_color")
        if self._current() is not None and self._current().kind == "video":
            self._slider(grid, 3, 1, "Video pradžia", "source_start_seconds", 0.0, 60.0,
                         cast=lambda v: round(v, 1), fmt="{:.1f} s", steps=600)
        self._animations(parent, reels.INSERT_ENTER_CHOICES, reels.INSERT_EXIT_CHOICES)

    def _place(self, key: str) -> None:
        x, y = reels.INSERT_POSITIONS[key]
        self._edit("Intarpo vieta", None, x_fraction=x, y_fraction=y)
        self._render_form()


# --- tabs ------------------------------------------------------------------------------------------


class ReelsTabs(ctk.CTkFrame):
    """The "📱 Reels" category: Subtitrai / Kortelės / Intarpai tabs."""

    TABS = ("Subtitrai", "Kortelės", "Intarpai")

    def __init__(self, master, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._var = ctk.StringVar(value=self.TABS[0])
        ctk.CTkSegmentedButton(self, values=list(self.TABS), variable=self._var,
                               command=lambda _v: self._show()).pack(fill="x", pady=(0, theme.SPACE_SM))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.pack(fill="x")
        self._pages: dict[str, ctk.CTkFrame] = {}

    def add_page(self, name: str, page: ctk.CTkFrame) -> None:
        self._pages[name] = page
        self._show()

    def show_tab(self, name: str) -> None:
        self._var.set(name)
        self._show()

    @property
    def current(self) -> str:
        return self._var.get()

    def _show(self) -> None:
        for name, page in self._pages.items():
            if name == self._var.get():
                page.pack(fill="x")
            else:
                page.pack_forget()
