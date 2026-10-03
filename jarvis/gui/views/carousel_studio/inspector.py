"""Right-hand settings panel: shows the selected element's settings
(text, font, colors, size, opacity, rotation, layers...) or, with
nothing selected, the current slide's background and the carousel's
palette. Every change goes through the editor host, so it is one undo
step (typing and slider drags are coalesced) and autosaves."""

from __future__ import annotations

from tkinter import colorchooser
from typing import TYPE_CHECKING, Any, Callable

import customtkinter as ctk

from jarvis.carousel_studio import fonts
from jarvis.carousel_studio.model import SLIDE_ROLES, Element
from jarvis.carousel_studio.themes import COLOR_ROLES, PALETTES, ROLE_LABELS, normalize_hex
from jarvis.gui import theme

if TYPE_CHECKING:
    from jarvis.gui.views.carousel_studio.editor_screen import EditorScreen

STYLE_LABELS = {"heading": "Antraštė", "subheading": "Paantraštė", "body": "Pagrindinis tekstas", "caption": "Prierašas"}
ALIGN_LABELS = {"left": "Kairė", "center": "Centras", "right": "Dešinė"}
VALIGN_LABELS = {"top": "Viršus", "middle": "Vidurys", "bottom": "Apačia"}
SHAPE_LABELS = {"rect": "Stačiakampis", "rounded": "Apvalinti kampai", "ellipse": "Apskritimas / elipsė"}
FIT_LABELS = {"cover": "Užpildyti", "contain": "Sutalpinti"}
BG_LABELS = {"color": "Spalva", "gradient": "Gradientas", "image": "Nuotrauka"}


def _font(size: int = theme.FONT_SIZE_SMALL, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=size, weight="bold" if bold else "normal")


def _label(master, text: str, *, bold: bool = False, size: int = theme.FONT_SIZE_SMALL) -> ctk.CTkLabel:
    return ctk.CTkLabel(master, text=text, font=_font(size, bold), text_color=theme.TEXT_PRIMARY if bold else theme.TEXT_SECONDARY, anchor="w")


def _reverse(mapping: dict[str, str], label: str) -> str:
    return next((k for k, v in mapping.items() if v == label), label)


class ColorField(ctk.CTkFrame):
    """A color picker: 5 theme-role swatches (they follow theme changes),
    a HEX entry and a system color dialog. Emits "theme:<role>", a
    "#RRGGBB" literal, or None (when `allow_none` and cleared)."""

    def __init__(self, master, label: str, *, resolve: Callable[[str | None], str | None], on_change: Callable[[str | None], None], allow_none: bool = False) -> None:
        super().__init__(master, fg_color="transparent")
        self._resolve = resolve
        self._on_change = on_change
        self._value: str | None = None
        _label(self, label).pack(anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(2, 0))
        self._swatches: dict[str, ctk.CTkButton] = {}
        for role in COLOR_ROLES:
            button = ctk.CTkButton(row, text="", width=22, height=22, corner_radius=11, border_width=2, border_color=theme.BORDER_SUBTLE, hover=False, command=lambda r=role: self._emit(f"theme:{r}"))
            button.pack(side="left", padx=(0, 4))
            self._swatches[role] = button
        self.entry = ctk.CTkEntry(row, width=78, height=24, font=_font())
        self.entry.pack(side="left", padx=(4, 2))
        self.entry.bind("<Return>", lambda _e: self._from_entry())
        self.entry.bind("<FocusOut>", lambda _e: self._from_entry())
        ctk.CTkButton(row, text="…", width=24, height=24, command=self._pick).pack(side="left")
        if allow_none:
            ctk.CTkButton(row, text="∅", width=24, height=24, command=lambda: self._emit(None)).pack(side="left", padx=(2, 0))

    def set(self, value: str | None) -> None:
        self._value = value
        for role, button in self._swatches.items():
            color = self._resolve(f"theme:{role}") or "#000000"
            active = value == f"theme:{role}"
            button.configure(fg_color=color, border_color=theme.ACCENT_PRIMARY if active else theme.BORDER_SUBTLE)
        self.entry.delete(0, "end")
        resolved = self._resolve(value)
        self.entry.insert(0, resolved or "")

    def _emit(self, value: str | None) -> None:
        self.set(value)
        self._on_change(value)

    def _from_entry(self) -> None:
        raw = self.entry.get().strip()
        if not raw:
            return
        hex_value = normalize_hex(raw)
        if hex_value is None:
            self.set(self._value)  # invalid: restore
            return
        if hex_value != self._resolve(self._value):
            self._emit(hex_value)

    def _pick(self) -> None:
        result = colorchooser.askcolor(color=self._resolve(self._value) or "#FFFFFF", title="Pasirinkite spalvą")
        if result and result[1]:
            self._emit(normalize_hex(result[1]))


class SliderField(ctk.CTkFrame):
    def __init__(self, master, label: str, *, from_: float, to: float, on_change: Callable[[float], None], fmt: str = "{:.0f}", on_release: Callable[[], None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x")
        _label(top, label).pack(side="left")
        self._value_label = _label(top, "")
        self._value_label.pack(side="right")
        self._fmt = fmt
        self._on_change = on_change
        self._loading = False
        self.slider = ctk.CTkSlider(self, from_=from_, to=to, command=self._changed, height=16)  # type: ignore[arg-type]  # floats work
        self.slider.pack(fill="x")
        if on_release:
            self.slider.bind("<ButtonRelease-1>", lambda _e: on_release())

    def set(self, value: float) -> None:
        self._loading = True
        self.slider.set(value)
        self._value_label.configure(text=self._fmt.format(value))
        self._loading = False

    def _changed(self, value: float) -> None:
        self._value_label.configure(text=self._fmt.format(value))
        if not self._loading:
            self._on_change(value)


class Inspector(ctk.CTkScrollableFrame):
    def __init__(self, master, host: "EditorScreen") -> None:
        super().__init__(master, fg_color=theme.BG_CARD, width=280, corner_radius=theme.RADIUS_CARD)
        self.host = host
        self._shown: tuple[str, str] | None = None  # (kind, id) currently built
        self._fields: dict[str, Any] = {}
        self._loading = False

    # --- building ---------------------------------------------------------------------

    def show(self, force: bool = False) -> None:
        el = self.host.selected_element()
        slide = self.host.current_slide()
        key = ("element", el.id) if el else ("slide", slide.id if slide else "")
        if force or key != self._shown:
            for child in self.winfo_children():
                child.destroy()
            self._fields = {}
            self._shown = key
            if el is not None:
                self._build_element(el)
            elif slide is not None:
                self._build_slide()
        self.refresh()

    def _section(self, title: str) -> ctk.CTkFrame:
        _label(self, title, bold=True, size=theme.FONT_SIZE_BODY).pack(fill="x", padx=8, pady=(12, 4))
        frame = ctk.CTkFrame(self, fg_color="transparent")
        frame.pack(fill="x", padx=8)
        return frame

    def _color(self, master, key: str, label: str, *, allow_none: bool = False, target: str = "element") -> None:
        def on_change(value: str | None) -> None:
            if target == "element":
                self.host.update_selected(**{key: value})
            else:
                self.host.update_background(**{key: value})
        field = ColorField(master, label, resolve=self.host.doc.project.theme.resolve, on_change=on_change, allow_none=allow_none)
        field.pack(fill="x", pady=4)
        self._fields[f"color:{key}"] = field

    def _slider(self, master, key: str, label: str, from_: float, to: float, *, fmt: str = "{:.0f}", target: str = "element", transform: Callable[[float], Any] = lambda v: v) -> None:
        def on_change(value: float) -> None:
            if self._loading:
                return
            if target == "element":
                self.host.update_selected(**{key: transform(value)})
            else:
                self.host.update_background(**{key: transform(value)})
        field = SliderField(master, label, from_=from_, to=to, on_change=on_change, fmt=fmt, on_release=self.host.doc.break_coalescing)
        field.pack(fill="x", pady=3)
        self._fields[f"slider:{key}"] = field

    def _option(self, master, key: str, label: str, mapping: dict[str, str], *, target: str = "element") -> None:
        _label(master, label).pack(anchor="w")
        def on_change(choice: str) -> None:
            value = _reverse(mapping, choice)
            if target == "element":
                self.host.update_selected(**{key: value})
            elif target == "background":
                self.host.update_background(**{key: value})
            else:
                target_callback = getattr(self.host, target)
                target_callback(value)
        menu = ctk.CTkOptionMenu(master, values=list(mapping.values()), command=on_change, height=26, font=_font(), dynamic_resizing=False)
        menu.pack(fill="x", pady=(0, 6))
        self._fields[f"option:{key}"] = (menu, mapping)

    def _check(self, master, key: str, label: str) -> None:
        var = ctk.BooleanVar()
        box = ctk.CTkCheckBox(master, text=label, variable=var, font=_font(), command=lambda: self.host.update_selected(**{key: bool(var.get())}))
        box.pack(anchor="w", pady=2)
        self._fields[f"check:{key}"] = var

    def _build_element(self, el: Element) -> None:
        titles = {"text": "Tekstas", "image": "Nuotrauka", "shape": "Forma", "line": "Linija"}
        if el.kind == "text":
            box = self._section(titles["text"])
            textbox = ctk.CTkTextbox(box, height=110, font=_font(theme.FONT_SIZE_BODY), wrap="word")
            textbox.pack(fill="x")
            textbox.bind("<KeyRelease>", lambda _e: self._text_changed())
            self._fields["text"] = textbox
            hint = _label(box, "Žodžius tarp *žvaigždučių* išskirsime akcento spalva.")
            hint.configure(wraplength=250, justify="left")
            hint.pack(anchor="w", pady=(2, 6))
            self._option(box, "style", "Teksto stilius", STYLE_LABELS, target="apply_text_style")
            font_map = {"": "Pagal temą"} | {k: fonts.family_label(k) + ("" if fonts.is_available(k) else " *") for k in fonts.family_keys()}
            self._option(box, "font", "Šriftas", font_map)
            self._slider(box, "size", "Dydis", 12, 220, transform=lambda v: int(round(v)))
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x")
            self._check(row, "bold", "Paryškintas")
            self._check(row, "uppercase", "DIDŽIOSIOS RAIDĖS")
            self._check(row, "autofit", "Sumažinti, kad tilptų")
            self._color(box, "color", "Teksto spalva")
            self._color(box, "highlight", "Išskirtų žodžių spalva")
            self._option(box, "align", "Lygiavimas", ALIGN_LABELS)
            self._option(box, "valign", "Vertikalus lygiavimas", VALIGN_LABELS)
            self._slider(box, "line_spacing", "Eilučių tarpai", 0.8, 2.2, fmt="{:.2f}", transform=lambda v: round(v, 2))
        elif el.kind == "image":
            box = self._section(titles["image"])
            ctk.CTkButton(box, text="Pakeisti nuotrauką…", height=28, command=self.host.replace_image).pack(fill="x", pady=(0, 6))
            self._option(box, "fit", "Įtalpinimas", FIT_LABELS)
            self._slider(box, "zoom", "Priartinimas", 1.0, 3.0, fmt="{:.2f}×", transform=lambda v: round(v, 3))
            self._slider(box, "offset_x", "Pozicija horizontaliai", -1.0, 1.0, fmt="{:+.2f}", transform=lambda v: round(v, 3))
            self._slider(box, "offset_y", "Pozicija vertikaliai", -1.0, 1.0, fmt="{:+.2f}", transform=lambda v: round(v, 3))
            self._slider(box, "radius", "Apvalinti kampai", 0, 300, transform=lambda v: int(round(v)))
        elif el.kind == "shape":
            box = self._section(titles["shape"])
            self._option(box, "shape", "Forma", SHAPE_LABELS)
            self._color(box, "fill", "Užpildas", allow_none=True)
            self._color(box, "stroke", "Kontūras", allow_none=True)
            self._slider(box, "stroke_width", "Kontūro storis", 0, 40, transform=lambda v: int(round(v)))
            self._slider(box, "radius", "Kampų apvalinimas", 0, 300, transform=lambda v: int(round(v)))
        elif el.kind == "line":
            box = self._section(titles["line"])
            self._color(box, "color", "Spalva")
            self._slider(box, "width", "Storis", 1, 60, transform=lambda v: int(round(v)))

        box = self._section("Padėtis ir išvaizda")
        grid = ctk.CTkFrame(box, fg_color="transparent")
        grid.pack(fill="x")
        for i, (key, label) in enumerate((("x", "X"), ("y", "Y"), ("w", "Plotis"), ("h", "Aukštis"), ("rotation", "Sukimas °"))):
            _label(grid, label).grid(row=i // 2 * 2, column=i % 2, sticky="w", padx=(0, 6))
            entry = ctk.CTkEntry(grid, width=110, height=24, font=_font())
            entry.grid(row=i // 2 * 2 + 1, column=i % 2, sticky="w", padx=(0, 6), pady=(0, 4))
            entry.bind("<Return>", lambda _e, k=key: self._geometry_entered(k))
            entry.bind("<FocusOut>", lambda _e, k=key: self._geometry_entered(k))
            self._fields[f"geo:{key}"] = entry
        self._slider(box, "opacity", "Skaidrumas", 0.05, 1.0, fmt="{:.0%}", transform=lambda v: round(v, 3))

        box = self._section("Sluoksniai ir veiksmai")
        layer = ctk.CTkFrame(box, fg_color="transparent")
        layer.pack(fill="x")
        layer.grid_columnconfigure((0, 1), weight=1)
        for i, (text, where) in enumerate((("↑ Aukštyn", "forward"), ("↓ Žemyn", "backward"), ("⤒ Į viršų", "front"), ("⤓ Į apačią", "back"))):
            ctk.CTkButton(layer, text=text, width=10, height=26, font=_font(), command=lambda w=where: self.host.reorder_selected(w)).grid(row=i // 2, column=i % 2, sticky="ew", padx=1, pady=1)
        actions = ctk.CTkFrame(box, fg_color="transparent")
        actions.pack(fill="x", pady=(6, 0))
        ctk.CTkButton(actions, text="Dubliuoti", height=28, command=self.host.duplicate_selected).pack(side="left", expand=True, fill="x", padx=(0, 2))
        ctk.CTkButton(actions, text="Ištrinti", height=28, fg_color=theme.DANGER, hover_color="#C9475A", command=self.host.delete_selected).pack(side="left", expand=True, fill="x", padx=(2, 0))
        ctk.CTkButton(box, text="Kopijuoti į visas skaidres", height=28, command=self.host.copy_selected_to_all).pack(fill="x", pady=(6, 10))

    def _build_slide(self) -> None:
        box = self._section(f"Skaidrė {self.host.slide_index + 1}")
        self._option(box, "role", "Skaidrės paskirtis", SLIDE_ROLES, target="set_slide_role")
        self._option(box, "type", "Fonas", BG_LABELS, target="background")
        slide = self.host.current_slide()
        kind = slide.background.get("type", "color") if slide else "color"
        if kind == "gradient":
            self._color(box, "color1", "Gradiento pradžia", target="background")
            self._color(box, "color2", "Gradiento pabaiga", target="background")
            self._option(box, "direction", "Gradiento kryptis", {"vertical": "Vertikalus", "diagonal": "Įstrižas"}, target="background")
        elif kind == "image":
            ctk.CTkButton(box, text="Fono nuotrauka…", height=28, command=self.host.choose_background_image).pack(fill="x", pady=(4, 4))
            self._slider(box, "overlay", "Patamsinimas virš nuotraukos", 0.0, 0.85, fmt="{:.0%}", target="background", transform=lambda v: round(v, 3))
            self._color(box, "overlay_color", "Patamsinimo spalva", target="background")
        else:
            self._color(box, "color", "Fono spalva", target="background")
        ctk.CTkButton(box, text="Pritaikyti šį foną visoms skaidrėms", height=28, command=self.host.background_to_all).pack(fill="x", pady=(6, 0))

        box = self._section("Karuselės paletė")
        note = _label(box, "Keičia visų skaidrių spalvas; tekstai ir nuotraukos lieka.")
        note.configure(wraplength=250, justify="left")
        note.pack(anchor="w")
        self._option(box, "palette", "Paletė", {k: v[0] for k, v in PALETTES.items()} | {"custom": "Sava"}, target="set_palette")
        for role in COLOR_ROLES:
            field = ColorField(box, ROLE_LABELS[role], resolve=self.host.doc.project.theme.resolve, on_change=lambda value, r=role: self.host.set_theme_color(r, value))
            field.pack(fill="x", pady=3)
            self._fields[f"theme:{role}"] = field
        tip = _label(self, "Patarimas: pasirinkite elementą skaidrėje, kad pamatytumėte jo nustatymus.")
        tip.configure(wraplength=250, justify="left")
        tip.pack(fill="x", padx=8, pady=12)

    # --- values ---------------------------------------------------------------------------

    def refresh(self) -> None:
        """Pushes current values into the built fields (no rebuild)."""
        self._loading = True
        try:
            el = self.host.selected_element()
            if el is not None:
                self._refresh_element(el)
            else:
                self._refresh_slide()
        finally:
            self._loading = False

    def _set_option(self, key: str, value: str | None) -> None:
        entry = self._fields.get(f"option:{key}")
        if entry:
            menu, mapping = entry
            menu.set(mapping.get(value or "", mapping.get("", next(iter(mapping.values())))))

    def _refresh_element(self, el: Element) -> None:
        props = el.props
        textbox = self._fields.get("text")
        if textbox is not None and textbox.focus_get() is not textbox._textbox:
            current = textbox.get("1.0", "end-1c")
            if current != props.get("text", ""):
                textbox.delete("1.0", "end")
                textbox.insert("1.0", props.get("text", ""))
        for key in ("style", "font", "align", "valign", "fit", "shape"):
            if f"option:{key}" in self._fields:
                self._set_option(key, props.get(key) or ("top" if key == "valign" else ""))
        for name, field in self._fields.items():
            if name.startswith("slider:"):
                key = name[7:]
                value = el.opacity if key == "opacity" else props.get(key, 0)
                field.set(float(value or 0))
            elif name.startswith("color:"):
                field.set(props.get(name[6:]))
            elif name.startswith("check:"):
                field.set(bool(props.get(name[6:], key_default(name[6:]))))
        for key in ("x", "y", "w", "h", "rotation"):
            entry = self._fields.get(f"geo:{key}")
            if entry is not None and entry.focus_get() is not entry._entry:
                entry.delete(0, "end")
                entry.insert(0, f"{getattr(el, key):.0f}")

    def _refresh_slide(self) -> None:
        slide = self.host.current_slide()
        if slide is None:
            return
        bg = slide.background
        self._set_option("role", slide.role)
        self._set_option("type", bg.get("type", "color"))
        self._set_option("direction", bg.get("direction", "vertical"))
        self._set_option("palette", self.host.doc.project.theme.palette_key)
        defaults = {"color": "theme:background", "color1": "theme:background", "color2": "theme:primary", "overlay_color": "#000000"}
        for key, default in defaults.items():
            if f"color:{key}" in self._fields:
                self._fields[f"color:{key}"].set(bg.get(key) or default)
        if "slider:overlay" in self._fields:
            self._fields["slider:overlay"].set(float(bg.get("overlay", 0.0)))
        for role in COLOR_ROLES:
            self._fields[f"theme:{role}"].set(f"theme:{role}")

    def _text_changed(self) -> None:
        textbox = self._fields.get("text")
        if textbox is None or self._loading:
            return
        self.host.update_selected(text=textbox.get("1.0", "end-1c"))

    def _geometry_entered(self, key: str) -> None:
        entry = self._fields.get(f"geo:{key}")
        el = self.host.selected_element()
        if entry is None or el is None:
            return
        try:
            value = float(entry.get().replace(",", "."))
        except ValueError:
            self.refresh()
            return
        if key in ("w", "h"):
            value = max(10.0, value)
        if abs(value - getattr(el, key)) > 0.01:
            self.host.update_selected(**{key: value % 360 if key == "rotation" else value})


def key_default(key: str) -> bool:
    return key == "autofit"
