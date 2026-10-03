"""„📚 Šablonų biblioteka“ screen of the carousel studio: browse the
templates (jarvis.carousel_studio.templates) by category, design style
and name, sort by newest/most popular/favorites, mark favorites (⭐),
preview every slide in any style before choosing, „Įkvėpti mane 🎲“,
and „Naudoti šį šabloną“, which creates an ordinary carousel project
and opens it in the editor.

Card thumbnails are rendered a few at a time in the background of the
Tk loop (and cached), so the screen opens instantly even with hundreds
of templates."""

from __future__ import annotations

import random
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk
from PIL import Image

from jarvis.carousel_studio import storage
from jarvis.carousel_studio.model import FORMATS, Project
from jarvis.carousel_studio.render import render_slide
from jarvis.carousel_studio.templates import catalog, placeholder, usage, use_template
from jarvis.carousel_studio.templates.catalog import Library, Template
from jarvis.gui import theme
from jarvis.gui.widgets import Card

ALL_CATEGORIES = "Visos kategorijos"
ALL_STYLES = "Visi dizaino stiliai"
PAGE_SIZE = 24
COLUMNS = 4
CARD_W = 150
PREVIEW_W = 330
STRIP_W = 64


def _font(size: int = theme.FONT_SIZE_SMALL, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=size, weight="bold" if bold else "normal")


class TemplateLibraryScreen(ctk.CTkFrame):
    def __init__(self, master, *, on_use: Callable[[str], None], on_back: Callable[[], None],
                 lib: Library | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_use = on_use
        self._on_back = on_back
        self.lib = lib or catalog.library()
        self._rng = random.Random()
        self._projects: dict[tuple[str, str], Project] = {}
        self._pil_cache: dict[tuple, Image.Image] = {}
        self._images: list[ctk.CTkImage] = []  # keep CTkImages alive
        self._thumb_queue: list[tuple[ctk.CTkLabel, Template]] = []
        self._thumb_after: str | None = None
        self._search_after: str | None = None
        self.results: list[Template] = []
        self._shown = 0
        self.selected: Template | None = None
        self.preview_style: str | None = None
        self.preview_slide = 0
        self.favorites: set[str] = usage.favorites()
        self._cards: dict[str, ctk.CTkFrame] = {}

        self._build_header()
        self._build_filters()
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, pady=(theme.SPACE_SM, 0))
        self.grid_frame = ctk.CTkScrollableFrame(body, fg_color="transparent")
        self.grid_frame.pack(side="left", fill="both", expand=True)
        for column in range(COLUMNS):
            self.grid_frame.grid_columnconfigure(column, weight=1, uniform="tpl")
        self.preview = Card(body, width=PREVIEW_W + 40)
        self.preview.pack(side="right", fill="y", padx=(theme.SPACE_SM, 0))
        self.preview.pack_propagate(False)
        self._build_preview()
        self.apply_filters()

    # --- header and filters ---------------------------------------------------------------

    def _build_header(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x", pady=(0, theme.SPACE_SM))
        ctk.CTkButton(row, text="← Mano karuselės", width=140, height=32, fg_color=theme.BG_CARD_HOVER,
                      command=self._on_back).pack(side="left")
        ctk.CTkLabel(row, text="📚  Šablonų biblioteka", font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
                     text_color=theme.TEXT_PRIMARY).pack(side="left", padx=theme.SPACE_MD)
        ctk.CTkButton(row, text="Įkvėpti mane 🎲", width=150, height=32, fg_color=theme.ACCENT_VIOLET,
                      command=self.inspire).pack(side="right")
        lib = self.lib
        ctk.CTkLabel(row, text=f"{len(lib.templates)} šablonų · {len(lib.categories)} kategorijų · {len(lib.styles)} stilių · 4:5",
                     font=_font(), text_color=theme.TEXT_SECONDARY).pack(side="right", padx=theme.SPACE_MD)
        if lib.problems:
            ctk.CTkLabel(self, text="Kai kurie šablonų failai praleisti: " + "; ".join(lib.problems[:3]),
                         font=_font(), text_color=theme.STATUS_WAITING, anchor="w", wraplength=900).pack(fill="x")

    def _build_filters(self) -> None:
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        self.search_entry = ctk.CTkEntry(row, placeholder_text="🔍 Ieškoti pagal pavadinimą ar temą", width=260, height=32)
        self.search_entry.pack(side="left")
        self.search_entry.bind("<KeyRelease>", lambda _e: self._schedule_search())
        categories = sorted(self.lib.categories.values(), key=lambda c: (c.order, c.label))
        self._category_keys = {c.title: c.key for c in categories}
        self.category_var = ctk.StringVar(value=ALL_CATEGORIES)
        ctk.CTkOptionMenu(row, values=[ALL_CATEGORIES, *self._category_keys], variable=self.category_var, width=210, height=32,
                          command=lambda _v: self.apply_filters()).pack(side="left", padx=(theme.SPACE_SM, 0))
        self._style_keys = {s.label: s.key for s in self.lib.styles.values()}
        self.style_var = ctk.StringVar(value=ALL_STYLES)
        ctk.CTkOptionMenu(row, values=[ALL_STYLES, *self._style_keys], variable=self.style_var, width=180, height=32,
                          command=lambda _v: self.apply_filters()).pack(side="left", padx=(theme.SPACE_SM, 0))
        self._sort_keys = {label: key for key, label in catalog.SORTS.items()}
        self.sort_var = ctk.StringVar(value=catalog.SORTS["all"])
        ctk.CTkSegmentedButton(row, values=list(self._sort_keys), variable=self.sort_var, height=32,
                               command=lambda _v: self.apply_filters()).pack(side="left", padx=(theme.SPACE_SM, 0))
        self.count_label = ctk.CTkLabel(row, text="", font=_font(theme.FONT_SIZE_BODY), text_color=theme.TEXT_SECONDARY)
        self.count_label.pack(side="left", padx=theme.SPACE_MD)

    def _schedule_search(self) -> None:
        if self._search_after is not None:
            self.after_cancel(self._search_after)
        self._search_after = self.after(250, self.apply_filters)

    # --- filtering and the grid -----------------------------------------------------------

    def set_filters(self, *, query: str | None = None, category: str | None = None, style: str | None = None,
                    sort: str | None = None) -> None:
        """Sets filters by key (category/style key, sort key from
        catalog.SORTS; "" clears) and refreshes the grid."""
        if query is not None:
            self.search_entry.delete(0, "end")
            if query:
                self.search_entry.insert(0, query)
        if category is not None:
            self.category_var.set(self.lib.category_label(category) if category else ALL_CATEGORIES)
        if style is not None:
            self.style_var.set(self.lib.style_label(style) if style else ALL_STYLES)
        if sort is not None:
            self.sort_var.set(catalog.SORTS[sort])
        self.apply_filters()

    def apply_filters(self) -> None:
        self._search_after = None
        sort = self._sort_keys.get(self.sort_var.get(), "all")
        self.results = catalog.search(
            self.lib, query=self.search_entry.get(), category=self._category_keys.get(self.category_var.get()),
            style=self._style_keys.get(self.style_var.get()), sort=sort, favorites=self.favorites,
            uses=usage.uses() if sort == "popular" else None,
        )
        self.count_label.configure(text=f"Rasta: {len(self.results)}")
        for child in self.grid_frame.winfo_children():
            child.destroy()
        self._cards.clear()
        self._thumb_queue.clear()
        self._images.clear()
        self._shown = 0
        if not self.results:
            text = ("Dar nepažymėjote mėgstamų šablonų. Spauskite ☆ prie šablono." if sort == "favorites"
                    else "Nieko nerasta. Pabandykite kitą paiešką ar filtrą.")
            ctk.CTkLabel(self.grid_frame, text=text, font=_font(theme.FONT_SIZE_BODY), text_color=theme.TEXT_MUTED).grid(
                row=0, column=0, columnspan=COLUMNS, sticky="w", pady=theme.SPACE_MD)
            return
        self._show_more()
        if self.selected is None or self.selected not in self.results:
            self.select(self.results[0])

    def _show_more(self) -> None:
        more = getattr(self, "_more_button", None)
        if more is not None and more.winfo_exists():
            more.destroy()
        end = min(len(self.results), self._shown + PAGE_SIZE)
        for index in range(self._shown, end):
            self._card(index, self.results[index])
        self._shown = end
        if end < len(self.results):
            self._more_button = ctk.CTkButton(self.grid_frame, text=f"Rodyti daugiau ({len(self.results) - end})",
                                              fg_color=theme.BG_CARD_HOVER, command=self._show_more)
            self._more_button.grid(row=end // COLUMNS + 1, column=0, columnspan=COLUMNS, pady=theme.SPACE_MD)
        self._pump_thumbnails()

    def _card(self, index: int, template: Template) -> None:
        card = Card(self.grid_frame)
        card.grid(row=index // COLUMNS, column=index % COLUMNS, sticky="nsew", padx=4, pady=4)
        self._cards[template.id] = card
        thumb = ctk.CTkLabel(card, text="…", width=CARD_W, height=round(CARD_W * 1.25), fg_color=theme.BG_CARD_HOVER,
                             corner_radius=8, cursor="hand2")
        thumb.pack(padx=8, pady=(8, 4))
        title = ctk.CTkLabel(card, text=template.name, font=_font(theme.FONT_SIZE_BODY, True), text_color=theme.TEXT_PRIMARY,
                             wraplength=CARD_W + 10, justify="left", anchor="w")
        title.pack(fill="x", padx=8)
        info = ctk.CTkLabel(card, text=(f"{self.lib.category_label(template.topic.category)} · {template.layout_label}\n"
                                  f"🎨 {self.lib.style_label(template.style)} · {template.slide_count} skaidr."),
                            font=_font(), text_color=theme.TEXT_SECONDARY, justify="left", anchor="w")
        info.pack(fill="x", padx=8)
        star = ctk.CTkButton(card, text=self._star(template), width=34, height=26, fg_color="transparent",
                             hover_color=theme.BG_CARD_HOVER, text_color=theme.STATUS_WAITING,
                             command=lambda t=template: self.toggle_favorite(t))
        star.pack(anchor="e", padx=6, pady=(0, 6))
        card.star_button = star  # type: ignore[attr-defined]
        for widget in (card, thumb, title, info):
            widget.bind("<Button-1>", lambda _e, t=template: self.select(t))
        self._thumb_queue.append((thumb, template))

    def _star(self, template: Template) -> str:
        return "★" if template.id in self.favorites else "☆"

    def _pump_thumbnails(self) -> None:
        """Renders one queued card thumbnail per Tk idle tick."""
        if self._thumb_after is not None or not self._thumb_queue:
            return

        def step() -> None:
            self._thumb_after = None
            while self._thumb_queue:
                label, template = self._thumb_queue.pop(0)
                if label.winfo_exists():
                    self._set_image(label, self.slide_image(template, template.style, 0, CARD_W))
                    break
            self._pump_thumbnails()

        self._thumb_after = self.after(1, step)

    def finish_thumbnails(self) -> None:
        """Renders every queued thumbnail now (tests, screenshots)."""
        if self._thumb_after is not None:
            self.after_cancel(self._thumb_after)
            self._thumb_after = None
        while self._thumb_queue:
            label, template = self._thumb_queue.pop(0)
            if label.winfo_exists():
                self._set_image(label, self.slide_image(template, template.style, 0, CARD_W))

    def _set_image(self, label: ctk.CTkLabel, pil: Image.Image) -> None:
        image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
        self._images.append(image)
        label.configure(image=image, text="")

    # --- rendering ---------------------------------------------------------------------------

    def project_for(self, template: Template, style: str) -> Project:
        key = (template.id, style)
        if key not in self._projects:
            self._projects[key] = catalog.build_project(template, style=style, lib=self.lib)
        return self._projects[key]

    def slide_image(self, template: Template, style: str, index: int, width: int) -> Image.Image:
        key = (template.id, style, index, width)
        if key not in self._pil_cache:
            project = self.project_for(template, style)
            assets = placeholder.preview_assets_dir(self.lib.styles[style])
            index = max(0, min(index, len(project.slides) - 1))
            self._pil_cache[key] = render_slide(project, project.slides[index], scale=width / project.size[0], assets_dir=assets)
            if len(self._pil_cache) > 800:
                self._pil_cache.pop(next(iter(self._pil_cache)))
        return self._pil_cache[key]

    # --- preview -----------------------------------------------------------------------------

    def _build_preview(self) -> None:
        box = self.preview
        self.preview_title = ctk.CTkLabel(box, text="", font=_font(theme.FONT_SIZE_SUBTITLE, True), text_color=theme.TEXT_PRIMARY,
                                          wraplength=PREVIEW_W, justify="left", anchor="w")
        self.preview_title.pack(fill="x", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, 2))
        self.preview_info = ctk.CTkLabel(box, text="", font=_font(), text_color=theme.TEXT_SECONDARY, justify="left", anchor="w",
                                         wraplength=PREVIEW_W)
        self.preview_info.pack(fill="x", padx=theme.SPACE_MD)
        style_row = ctk.CTkFrame(box, fg_color="transparent")
        style_row.pack(fill="x", padx=theme.SPACE_MD, pady=(6, 0))
        ctk.CTkLabel(style_row, text="Stilius:", font=_font(), text_color=theme.TEXT_SECONDARY).pack(side="left")
        self.preview_style_var = ctk.StringVar(value="")
        ctk.CTkOptionMenu(style_row, values=list(self._style_labels()), variable=self.preview_style_var, width=170, height=28,
                          command=lambda label: self.set_preview_style(self._style_labels()[label])).pack(side="left", padx=6)
        self.star_button = ctk.CTkButton(style_row, text="☆", width=36, height=28, fg_color=theme.BG_CARD_HOVER,
                                         text_color=theme.STATUS_WAITING, command=lambda: self.selected and self.toggle_favorite(self.selected))
        self.star_button.pack(side="right")
        self.big_label = ctk.CTkLabel(box, text="", width=PREVIEW_W, height=round(PREVIEW_W * 1.25), fg_color=theme.BG_CARD_HOVER,
                                      corner_radius=8)
        self.big_label.pack(padx=theme.SPACE_MD, pady=(theme.SPACE_SM, 4))
        self.strip = ctk.CTkScrollableFrame(box, orientation="horizontal", height=STRIP_W * 1.25 + 14, fg_color="transparent")
        self.strip.pack(fill="x", padx=theme.SPACE_SM)
        use_row = ctk.CTkFrame(box, fg_color="transparent")
        use_row.pack(fill="x", padx=theme.SPACE_MD, pady=(4, theme.SPACE_MD))
        self.name_entry = ctk.CTkEntry(use_row, placeholder_text="Karuselės pavadinimas (nebūtina)", height=30)
        self.name_entry.pack(fill="x")
        self.use_button = ctk.CTkButton(use_row, text="Naudoti šį šabloną", height=36, font=_font(theme.FONT_SIZE_BODY, True),
                                        command=self.use_selected)
        self.use_button.pack(fill="x", pady=(6, 0))

    def _style_labels(self) -> dict[str, str]:
        return {s.label: s.key for s in self.lib.styles.values()}

    def select(self, template: Template, *, style: str | None = None) -> None:
        previous = self.selected
        self.selected = template
        self.preview_style = style or template.style
        self.preview_slide = 0
        for template_id in {previous.id if previous else None, template.id} - {None}:
            card = self._cards.get(template_id)
            if card is not None and card.winfo_exists():
                card.configure(border_color=theme.ACCENT_PRIMARY if template_id == template.id else theme.BORDER_SUBTLE,
                               border_width=2 if template_id == template.id else 1)
        self._render_preview()

    def set_preview_style(self, style: str) -> None:
        if self.selected is not None and style in self.lib.styles:
            self.preview_style = style
            self._render_preview()

    def show_slide(self, index: int) -> None:
        self.preview_slide = index
        self._render_big()

    def _render_preview(self) -> None:
        template = self.selected
        if template is None:
            return
        lib = self.lib
        self.preview_title.configure(text=template.name)
        self.preview_info.configure(text=(
            f"Tema: {template.topic.title} · {lib.category_label(template.topic.category)}\n"
            f"Dizaino stilius: {lib.style_label(self.preview_style)}"
            + ("" if self.preview_style == template.style else f" (šablono: {lib.style_label(template.style)})")
            + f"\nIšdėstymas: {template.layout_label}\n"
            f"Skaidrių: {template.slide_count} · {FORMATS[catalog.TEMPLATE_FORMAT][0]}"
        ))
        self.preview_style_var.set(lib.style_label(self.preview_style))
        self.star_button.configure(text=self._star(template))
        for child in self.strip.winfo_children():
            child.destroy()
        for index in range(template.slide_count):
            pil = self.slide_image(template, self.preview_style, index, STRIP_W)
            image = ctk.CTkImage(light_image=pil, dark_image=pil, size=pil.size)
            self._images.append(image)
            label = ctk.CTkLabel(self.strip, text="", image=image, cursor="hand2")
            label.pack(side="left", padx=2)
            label.bind("<Button-1>", lambda _e, i=index: self.show_slide(i))
        self._render_big()

    def _render_big(self) -> None:
        if self.selected is not None:
            self._set_image(self.big_label, self.slide_image(self.selected, self.preview_style, self.preview_slide, PREVIEW_W))

    # --- actions -----------------------------------------------------------------------------

    def toggle_favorite(self, template: Template) -> None:
        if usage.toggle_favorite(template.id):
            self.favorites.add(template.id)
        else:
            self.favorites.discard(template.id)
        card = self._cards.get(template.id)
        if card is not None and card.winfo_exists():
            card.star_button.configure(text=self._star(template))
        if self.selected is template:
            self.star_button.configure(text=self._star(template))
        if self._sort_keys.get(self.sort_var.get()) == "favorites":
            self.apply_filters()

    def inspire(self) -> catalog.Inspiration:
        """Random topic + design style + layout, shown in the preview."""
        idea = catalog.inspire(self.lib, self._rng, category=self._category_keys.get(self.category_var.get()))
        self.select(idea.template, style=idea.style)
        self.preview_title.configure(text=f"🎲 {idea.template.name}")
        return idea

    def use_selected(self) -> Project | None:
        if self.selected is None:
            return None
        try:
            project = use_template(self.selected, style=self.preview_style, name=self.name_entry.get().strip() or None,
                                   lib=self.lib)
        except (OSError, storage.StorageError) as exc:
            messagebox.showerror("Šablonų biblioteka", str(exc), parent=self.winfo_toplevel())
            return None
        self.name_entry.delete(0, "end")
        self._on_use(project.id)
        return project
