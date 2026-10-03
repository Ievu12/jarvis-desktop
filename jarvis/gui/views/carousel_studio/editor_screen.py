"""The carousel editor screen: tools on the left, the large interactive
slide in the center, element/slide settings on the right, the slide
thumbnail strip at the bottom and a top bar with undo/redo, format and
export. Owns the CarouselDocument and autosaves it shortly after every
change."""

from __future__ import annotations

import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable

import customtkinter as ctk

from jarvis.carousel_studio import export, storage
from jarvis.carousel_studio.editor import CarouselDocument, EditorError
from jarvis.carousel_studio.model import FORMATS, MAX_SLIDES, TEXT_STYLES, Element, Project, Slide
from jarvis.carousel_studio.storage import StorageError
from jarvis.gui import theme
from jarvis.gui.views.carousel_studio.inspector import Inspector
from jarvis.gui.views.carousel_studio.slide_canvas import SlideCanvas
from jarvis.gui.views.carousel_studio.thumbnail_strip import ThumbnailStrip

AUTOSAVE_DELAY_MS = 700
IMAGE_TYPES = [("Paveikslėliai", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("Visi failai", "*.*")]


def _shift(event) -> bool:
    return isinstance(event.state, int) and bool(event.state & 0x0001)


def _font(size: int = theme.FONT_SIZE_SMALL, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=size, weight="bold" if bold else "normal")


class EditorScreen(ctk.CTkFrame):
    def __init__(self, master, *, on_back: Callable[[], None]) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_back = on_back
        self.doc: CarouselDocument = CarouselDocument(Project(name=""))
        self.slide_index = 0
        self.selected_id: str | None = None
        self._clipboard: dict | None = None
        self._save_job: str | None = None
        self._thumb_job: str | None = None
        self._dirty = False
        self._build()
        self._bind_keys()

    # --- layout -------------------------------------------------------------------------

    def _build(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(1, weight=1)

        top = ctk.CTkFrame(self, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_CARD)
        top.grid(row=0, column=0, columnspan=3, sticky="ew", padx=4, pady=(0, 6))
        ctk.CTkButton(top, text="← Projektai", width=96, height=30, command=self.back).pack(side="left", padx=8, pady=8)
        self.name_entry = ctk.CTkEntry(top, width=260, height=30, font=_font(theme.FONT_SIZE_BODY, True))
        self.name_entry.pack(side="left", padx=4)
        self.name_entry.bind("<Return>", lambda _e: self._rename())
        self.name_entry.bind("<FocusOut>", lambda _e: self._rename())
        self.undo_button = ctk.CTkButton(top, text="↶ Atšaukti", width=90, height=30, command=self.undo)
        self.undo_button.pack(side="left", padx=(12, 2))
        self.redo_button = ctk.CTkButton(top, text="↷ Grąžinti", width=90, height=30, command=self.redo)
        self.redo_button.pack(side="left", padx=2)
        self.format_menu = ctk.CTkOptionMenu(top, values=[v[0] for v in FORMATS.values()], width=230, height=30, command=self._format_chosen, dynamic_resizing=False)
        self.format_menu.pack(side="left", padx=12)
        self.export_button = ctk.CTkButton(top, text="⬇ Eksportuoti", width=120, height=30, fg_color=theme.SUCCESS, hover_color="#2FB57C", text_color=theme.BG_PRIMARY, command=self._export_menu)
        self.export_button.pack(side="right", padx=8)
        self.status = ctk.CTkLabel(top, text="", font=_font(), text_color=theme.TEXT_MUTED)
        self.status.pack(side="right", padx=8)

        self.tools = self._build_tools()
        self.tools.grid(row=1, column=0, sticky="ns", padx=(4, 6))

        center = ctk.CTkFrame(self, fg_color=theme.BG_PRIMARY, corner_radius=theme.RADIUS_CARD)
        center.grid(row=1, column=1, sticky="nsew")
        self.canvas = SlideCanvas(center, on_select=self._canvas_selected, on_edited=self._canvas_edited, on_double_click=self._focus_text)
        self.canvas.pack(fill="both", expand=True, padx=2, pady=2)

        self.inspector = Inspector(self, self)
        self.inspector.grid(row=1, column=2, sticky="ns", padx=(6, 4))

        bottom = ctk.CTkFrame(self, fg_color=theme.BG_CARD, corner_radius=theme.RADIUS_CARD)
        bottom.grid(row=2, column=0, columnspan=3, sticky="ew", padx=4, pady=(6, 0))
        buttons = ctk.CTkFrame(bottom, fg_color="transparent")
        buttons.pack(side="left", fill="y", padx=8, pady=8)
        ctk.CTkButton(buttons, text="+ Skaidrė", width=110, height=28, command=self.add_slide).pack(pady=2)
        ctk.CTkButton(buttons, text="Dubliuoti", width=110, height=28, command=self.duplicate_slide).pack(pady=2)
        ctk.CTkButton(buttons, text="Ištrinti", width=110, height=28, fg_color=theme.DANGER, hover_color="#C9475A", command=self.delete_slide).pack(pady=2)
        self.count_label = ctk.CTkLabel(buttons, text="", font=_font(), text_color=theme.TEXT_SECONDARY)
        self.count_label.pack(pady=(2, 0))
        self.strip = ThumbnailStrip(bottom, on_open=self.open_slide, on_move=self.move_slide)
        self.strip.pack(side="left", fill="x", expand=True, padx=(0, 8), pady=8)

    def _build_tools(self) -> ctk.CTkScrollableFrame:
        panel = ctk.CTkScrollableFrame(self, fg_color=theme.BG_CARD, width=170, corner_radius=theme.RADIUS_CARD)

        def header(text: str) -> None:
            ctk.CTkLabel(panel, text=text, font=_font(theme.FONT_SIZE_BODY, True), text_color=theme.TEXT_PRIMARY, anchor="w").pack(fill="x", padx=6, pady=(12, 4))

        def tool(text: str, command: Callable[[], None]) -> None:
            ctk.CTkButton(panel, text=text, anchor="w", height=30, fg_color=theme.BG_CARD_HOVER, hover_color=theme.BORDER_SUBTLE, command=command).pack(fill="x", padx=6, pady=2)

        header("Tekstas")
        tool("H  Antraštė", lambda: self.add_text("heading"))
        tool("H2 Paantraštė", lambda: self.add_text("subheading"))
        tool("T  Tekstas", lambda: self.add_text("body"))
        tool("t  Prierašas", lambda: self.add_text("caption"))
        header("Vaizdai")
        tool("🖼  Nuotrauka…", self.add_image)
        tool("🌄  Fono nuotrauka…", self.choose_background_image)
        header("Formos")
        tool("▭  Stačiakampis", lambda: self.add_shape("rect"))
        tool("▢  Apvalinti kampai", lambda: self.add_shape("rounded"))
        tool("◯  Apskritimas", lambda: self.add_shape("ellipse"))
        tool("―  Linija", self.add_line)
        header("Rodymas")
        self.margins_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(panel, text="Saugios paraštės", variable=self.margins_var, font=_font(), command=self._toggle_margins).pack(anchor="w", padx=6, pady=4)
        ctk.CTkLabel(
            panel, justify="left", wraplength=150, font=_font(theme.FONT_SIZE_CAPTION), text_color=theme.TEXT_MUTED,
            text="Ctrl+Z / Ctrl+Y – atšaukti / grąžinti\nCtrl+C / Ctrl+V – kopijuoti\nCtrl+D – dubliuoti\nDelete – ištrinti\nRodyklės – pastumti (Shift ×10)\nShift tempiant – be prilipimo / išlaikyti proporcijas",
        ).pack(fill="x", padx=6, pady=(12, 4))
        return panel

    def _bind_keys(self) -> None:
        root = self.winfo_toplevel()
        bindings = {
            "<Control-z>": self.undo, "<Control-Z>": self.undo, "<Control-y>": self.redo, "<Control-Shift-Z>": self.redo,
            "<Delete>": self.delete_selected, "<Control-d>": self.duplicate_selected,
            "<Control-c>": self.copy_selected, "<Control-v>": self.paste,
        }
        for sequence, handler in bindings.items():
            root.bind(sequence, lambda e, h=handler: self._key(e, h), add="+")
        for key, (dx, dy) in {"<Left>": (-1, 0), "<Right>": (1, 0), "<Up>": (0, -1), "<Down>": (0, 1)}.items():
            root.bind(key, lambda e, d=(dx, dy): self._key(e, lambda: self.nudge(*d, big=_shift(e))), add="+")

    def _key(self, event, handler: Callable[[], None]) -> str | None:
        if not self.winfo_ismapped():
            return None
        focus = self.focus_get()
        if isinstance(focus, (tk.Entry, tk.Text)):
            return None  # typing in a field: keep the field's own shortcuts
        handler()
        return "break"

    # --- loading / saving -----------------------------------------------------------------

    def load(self, project: Project) -> None:
        self.flush_save()
        self.doc = CarouselDocument(project, on_change=self._doc_changed)
        self.slide_index = 0
        self.selected_id = None
        self._clipboard = None
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, project.name)
        self.format_menu.set(FORMATS[project.format][0])
        self.status.configure(text="Išsaugota")
        self._refresh(rebuild=True)

    @property
    def assets_dir(self) -> Path:
        return storage.assets_dir(self.doc.project.id)

    def _doc_changed(self) -> None:
        self._dirty = True
        self.status.configure(text="Saugoma…")
        if self._save_job:
            self.after_cancel(self._save_job)
        self._save_job = self.after(AUTOSAVE_DELAY_MS, self.flush_save)

    def flush_save(self) -> None:
        if self._save_job:
            self.after_cancel(self._save_job)
            self._save_job = None
        if not self._dirty or not self.doc.project.name:
            return
        try:
            storage.save_project(self.doc.project)
            self._dirty = False
            self.status.configure(text="Išsaugota ✓")
        except (OSError, StorageError) as exc:
            self.status.configure(text=f"Nepavyko išsaugoti: {exc}")

    def back(self) -> None:
        self._rename()
        self.flush_save()
        self._on_back()

    # --- refresh ------------------------------------------------------------------------------

    def _refresh(self, *, rebuild: bool = False, thumbs_now: bool = True) -> None:
        slides = self.doc.slides
        self.slide_index = max(0, min(self.slide_index, len(slides) - 1))
        if self.selected_element() is None:
            self.selected_id = None
        self.canvas.set_slide(self.doc, self.slide_index, self.assets_dir, self.selected_id)
        if thumbs_now:
            self.strip.set_doc(self.doc, self.assets_dir, self.slide_index)
        else:
            self._schedule_thumbs()
        self.inspector.show(force=rebuild)
        self.undo_button.configure(state="normal" if self.doc.can_undo else "disabled")
        self.redo_button.configure(state="normal" if self.doc.can_redo else "disabled")
        self.count_label.configure(text=f"{len(slides)} / {MAX_SLIDES} skaidrių")

    def _schedule_thumbs(self) -> None:
        if self._thumb_job:
            self.after_cancel(self._thumb_job)
        self._thumb_job = self.after(250, lambda: (setattr(self, "_thumb_job", None), self.strip.set_doc(self.doc, self.assets_dir, self.slide_index)))

    def _error(self, exc: Exception) -> None:
        messagebox.showwarning("Karuselių kūrimas", str(exc), parent=self.winfo_toplevel())

    # --- host API used by Inspector -----------------------------------------------------------

    def current_slide(self) -> Slide | None:
        if not self.doc.slides:
            return None
        return self.doc.slides[max(0, min(self.slide_index, len(self.doc.slides) - 1))]

    def selected_element(self) -> Element | None:
        slide = self.current_slide()
        if slide is None or self.selected_id is None:
            return None
        return slide.find(self.selected_id)

    def update_selected(self, **changes) -> None:
        el = self.selected_element()
        if el is None:
            return
        self.doc.update_element(self.slide_index, el.id, **changes)
        self.canvas.redraw()
        self._schedule_thumbs()
        self.inspector.refresh()
        self._update_history_buttons()

    def apply_text_style(self, style: str) -> None:
        preset = TEXT_STYLES.get(style)
        if preset is None or self.selected_element() is None:
            return
        self.doc.break_coalescing()
        self.update_selected(style=style, size=preset["size"], bold=preset["bold"], color=preset["color"], font_role=preset["font_role"], line_spacing=preset["line_spacing"])
        self.doc.break_coalescing()

    def update_background(self, **background) -> None:
        self.doc.set_background(self.slide_index, **background)
        self._refresh(rebuild="type" in background, thumbs_now=False)

    def set_slide_role(self, role: str) -> None:
        self.doc.set_slide_role(self.slide_index, role)
        self._refresh()

    def set_palette(self, key: str) -> None:
        if key == "custom":
            return
        self.doc.set_palette(key)
        self._refresh()

    def set_theme_color(self, role: str, value: str | None) -> None:
        resolved = self.doc.project.theme.resolve(value)
        if resolved:
            self.doc.set_theme_color(role, resolved)
            self._refresh(thumbs_now=False)

    def background_to_all(self) -> None:
        self.doc.apply_background_to_all(self.slide_index)
        self._refresh()

    def reorder_selected(self, where: str) -> None:
        el = self.selected_element()
        if el is not None:
            self.doc.reorder_element(self.slide_index, el.id, where)
            self._refresh()

    def duplicate_selected(self) -> None:
        el = self.selected_element()
        if el is not None:
            dup = self.doc.duplicate_element(self.slide_index, el.id)
            self.selected_id = dup.id
            self._refresh()

    def delete_selected(self) -> None:
        el = self.selected_element()
        if el is not None:
            self.doc.delete_element(self.slide_index, el.id)
            self.selected_id = None
            self._refresh()

    def copy_selected_to_all(self) -> None:
        el = self.selected_element()
        if el is not None:
            self.doc.copy_element_to_all(self.slide_index, el.id)
            self._refresh()

    def copy_selected(self) -> None:
        el = self.selected_element()
        if el is not None:
            self._clipboard = el.to_dict()
            self.status.configure(text="Nukopijuota")

    def paste(self) -> None:
        if not self._clipboard:
            return
        el = Element.from_dict(self._clipboard).clone()
        slide = self.current_slide()
        if slide is not None and any(abs(e.x - el.x) < 1 and abs(e.y - el.y) < 1 for e in slide.elements):
            el.x += 30
            el.y += 30
        self.doc.add_element(self.slide_index, el)
        self.selected_id = el.id
        self._refresh()

    def nudge(self, dx: int, dy: int, *, big: bool = False) -> None:
        el = self.selected_element()
        if el is None:
            return
        step = 10 if big else 2
        self.update_selected(x=el.x + dx * step, y=el.y + dy * step)

    def replace_image(self) -> None:
        el = self.selected_element()
        if el is None or el.kind != "image":
            return
        imported = self._import_image()
        if imported:
            self.update_selected(path=imported[0])
            self.doc.break_coalescing()

    def choose_background_image(self) -> None:
        imported = self._import_image()
        if imported:
            self.doc.set_background(self.slide_index, type="image", path=imported[0])
            self.doc.break_coalescing()
            self.selected_id = None
            self._refresh(rebuild=True)

    # --- tools -------------------------------------------------------------------------------

    def _import_image(self) -> tuple[str, tuple[int, int]] | None:
        path = filedialog.askopenfilename(parent=self.winfo_toplevel(), title="Pasirinkite nuotrauką", filetypes=IMAGE_TYPES)
        if not path:
            return None
        try:
            return storage.import_asset(self.doc.project.id, path)
        except StorageError as exc:
            self._error(exc)
            return None

    def _select_new(self, element: Element) -> None:
        self.selected_id = element.id
        self._refresh()

    def add_text(self, style: str) -> None:
        self._select_new(self.doc.new_text(self.slide_index, style))

    def add_shape(self, shape: str) -> None:
        self._select_new(self.doc.new_shape(self.slide_index, shape))

    def add_line(self) -> None:
        self._select_new(self.doc.new_line(self.slide_index))

    def add_image(self) -> None:
        imported = self._import_image()
        if imported:
            self._select_new(self.doc.new_image(self.slide_index, *imported))

    def _toggle_margins(self) -> None:
        self.canvas.show_margins = bool(self.margins_var.get())
        self.canvas.redraw()

    # --- canvas callbacks --------------------------------------------------------------------

    def _canvas_selected(self, element_id: str | None) -> None:
        self.selected_id = element_id
        self.doc.break_coalescing()
        self.inspector.show()

    def _canvas_edited(self, final: bool) -> None:
        self.inspector.refresh()
        if final:
            self.doc.changed()
            self._schedule_thumbs()
            self._update_history_buttons()

    def _focus_text(self, element_id: str) -> None:
        self.selected_id = element_id
        self.inspector.show()
        textbox = self.inspector._fields.get("text")
        if textbox is not None:
            textbox.focus_set()
            textbox.tag_add("sel", "1.0", "end-1c")

    def _update_history_buttons(self) -> None:
        self.undo_button.configure(state="normal" if self.doc.can_undo else "disabled")
        self.redo_button.configure(state="normal" if self.doc.can_redo else "disabled")

    # --- slides -------------------------------------------------------------------------------

    def open_slide(self, index: int) -> None:
        if index != self.slide_index:
            self.slide_index = index
            self.selected_id = None
            self.doc.break_coalescing()
            self._refresh()

    def add_slide(self) -> None:
        try:
            self.slide_index = self.doc.add_slide(self.slide_index)
        except EditorError as exc:
            return self._error(exc)
        self.selected_id = None
        self._refresh()

    def duplicate_slide(self) -> None:
        try:
            self.slide_index = self.doc.duplicate_slide(self.slide_index)
        except EditorError as exc:
            return self._error(exc)
        self.selected_id = None
        self._refresh()

    def delete_slide(self) -> None:
        try:
            self.slide_index = self.doc.delete_slide(self.slide_index)
        except EditorError as exc:
            return self._error(exc)
        self.selected_id = None
        self._refresh()

    def move_slide(self, src: int, dst: int) -> None:
        self.slide_index = self.doc.move_slide(src, dst)
        self._refresh()

    # --- project --------------------------------------------------------------------------------

    def undo(self) -> None:
        if self.doc.undo():
            self._after_history()

    def redo(self) -> None:
        if self.doc.redo():
            self._after_history()

    def _after_history(self) -> None:
        self.name_entry.delete(0, "end")
        self.name_entry.insert(0, self.doc.project.name)
        self.format_menu.set(FORMATS[self.doc.project.format][0])
        self._refresh(rebuild=True)

    def _rename(self) -> None:
        if self.doc.project.name:
            self.doc.rename(self.name_entry.get())
            self.doc.break_coalescing()

    def _format_chosen(self, label: str) -> None:
        key = next(k for k, v in FORMATS.items() if v[0] == label)
        self.doc.set_format(key)
        self._refresh()

    # --- export -----------------------------------------------------------------------------------

    def _export_menu(self) -> None:
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Visos skaidrės – PNG", command=lambda: self.export_images("png"))
        menu.add_command(label="Visos skaidrės – JPG", command=lambda: self.export_images("jpg"))
        menu.add_command(label="Tik ši skaidrė – PNG", command=lambda: self.export_images("png", only_current=True))
        menu.add_command(label="Tik ši skaidrė – JPG", command=lambda: self.export_images("jpg", only_current=True))
        menu.add_separator()
        menu.add_command(label="ZIP archyvas (PNG)", command=lambda: self.export_archive("png"))
        menu.add_command(label="ZIP archyvas (JPG)", command=lambda: self.export_archive("jpg"))
        menu.add_command(label="PDF peržiūrai", command=self.export_pdf)
        x = self.export_button.winfo_rootx()
        y = self.export_button.winfo_rooty() + self.export_button.winfo_height()
        menu.tk_popup(x, y)

    def _confirm_layout(self) -> bool:
        issues = export.check_layout(self.doc.project)
        if not issues:
            return True
        lines = "\n".join(f"• {i.slide_number} skaidrė: {i.message}" for i in issues[:10])
        return messagebox.askyesno(
            "Patikrinkite prieš eksportą",
            f"Radau galimų problemų:\n\n{lines}\n\nVis tiek eksportuoti?",
            parent=self.winfo_toplevel(),
        )

    def _run_export(self, job: Callable[[], str]) -> None:
        self.flush_save()
        self.export_button.configure(state="disabled", text="Eksportuojama…")
        result: dict[str, str] = {}

        def worker() -> None:
            try:
                result["ok"] = job()
            except Exception as exc:  # noqa: BLE001 - shown to the person
                result["error"] = str(exc)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        def poll() -> None:
            if thread.is_alive():
                self.after(100, poll)
                return
            self.export_button.configure(state="normal", text="⬇ Eksportuoti")
            if "error" in result:
                messagebox.showerror("Eksportas nepavyko", result["error"], parent=self.winfo_toplevel())
            else:
                self.status.configure(text=result["ok"])

        poll()

    def _snapshot(self) -> Project:
        return Project.from_dict(self.doc.project.to_dict())

    def export_images(self, fmt: str, *, only_current: bool = False) -> None:
        if not self._confirm_layout():
            return
        folder = filedialog.askdirectory(parent=self.winfo_toplevel(), title="Kur išsaugoti skaidres?")
        if not folder:
            return
        project, assets = self._snapshot(), self.assets_dir
        indices = [self.slide_index] if only_current else None
        self._run_export(lambda: f"Eksportuota: {len(export.export_images(project, Path(folder), fmt=fmt, assets_dir=assets, indices=indices))} failai")

    def export_archive(self, fmt: str) -> None:
        if not self._confirm_layout():
            return
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), title="Išsaugoti ZIP", defaultextension=".zip",
            initialfile=f"{export.safe_filename(self.doc.project.name)}.zip", filetypes=[("ZIP", "*.zip")],
        )
        if not path:
            return
        project, assets = self._snapshot(), self.assets_dir
        self._run_export(lambda: f"ZIP išsaugotas: {export.export_zip(project, Path(path), fmt=fmt, assets_dir=assets).name}")

    def export_pdf(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self.winfo_toplevel(), title="Išsaugoti PDF", defaultextension=".pdf",
            initialfile=f"{export.safe_filename(self.doc.project.name)}.pdf", filetypes=[("PDF", "*.pdf")],
        )
        if not path:
            return
        project, assets = self._snapshot(), self.assets_dir
        self._run_export(lambda: f"PDF išsaugotas: {export.export_pdf(project, Path(path), assets_dir=assets).name}")
