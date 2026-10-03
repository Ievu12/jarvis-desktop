"""Top-level view for the sidebar's "Karuselių kūrimas" item: the
project library (create a new carousel, continue, rename, duplicate,
delete) and the editor screen, one shown at a time."""

from __future__ import annotations

from datetime import datetime
from tkinter import messagebox
from typing import Callable

import customtkinter as ctk
from PIL import Image

from jarvis.carousel_studio import storage
from jarvis.carousel_studio.model import FORMATS, MAX_SLIDES, MIN_SLIDES
from jarvis.carousel_studio.storage import ProjectSummary, StorageError
from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.carousel_studio.editor_screen import EditorScreen
from jarvis.gui.widgets import Card, SectionHeader

FORMAT_SHORT = {"portrait": "4:5", "square": "1:1", "story": "9:16"}


def _font(size: int = theme.FONT_SIZE_SMALL, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=size, weight="bold" if bold else "normal")


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return ""


class LibraryScreen(ctk.CTkFrame):
    def __init__(self, master, *, on_open: Callable[[str], None]) -> None:
        super().__init__(master, fg_color="transparent")
        self._on_open = on_open
        self._images: list[ctk.CTkImage] = []

        ctk.CTkLabel(self, text="🧩  Karuselių kūrimas", font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_HERO, weight="bold"), text_color=theme.TEXT_PRIMARY, anchor="w").pack(fill="x", pady=(0, 2))
        ctk.CTkLabel(self, text="Kurkite vientiso dizaino Instagram karuseles: redaguokite kiekvieną skaidrę ir eksportuokite paruoštus įrašus.", font=_font(theme.FONT_SIZE_BODY), text_color=theme.TEXT_SECONDARY, anchor="w").pack(fill="x", pady=(0, theme.SPACE_MD))

        form = Card(self)
        form.pack(fill="x", pady=(0, theme.SPACE_MD))
        SectionHeader(form, "Nauja karuselė").pack(fill="x", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_SM))
        row = ctk.CTkFrame(form, fg_color="transparent")
        row.pack(fill="x", padx=theme.SPACE_MD, pady=(0, theme.SPACE_MD))
        self.name_entry = ctk.CTkEntry(row, placeholder_text="Pavadinimas, pvz. „5 klaidos prižiūrint odą“", width=320, height=34)
        self.name_entry.pack(side="left", padx=(0, theme.SPACE_SM))
        self.format_var = ctk.StringVar(value=FORMATS["portrait"][0])
        ctk.CTkSegmentedButton(row, values=[v[0] for v in FORMATS.values()], variable=self.format_var, height=34).pack(side="left", padx=theme.SPACE_SM)
        ctk.CTkLabel(row, text="Skaidrių:", font=_font(theme.FONT_SIZE_BODY), text_color=theme.TEXT_SECONDARY).pack(side="left", padx=(theme.SPACE_SM, 4))
        self.count_var = ctk.StringVar(value="5")
        ctk.CTkOptionMenu(row, values=[str(n) for n in range(MIN_SLIDES, MAX_SLIDES + 1)], variable=self.count_var, width=70, height=34).pack(side="left")
        ctk.CTkButton(row, text="Kurti karuselę", height=34, command=self._create).pack(side="left", padx=theme.SPACE_MD)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x")
        SectionHeader(header, "Mano karuselės").pack(side="left")
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.list_frame.pack(fill="both", expand=True, pady=(theme.SPACE_SM, 0))

    def refresh(self) -> None:
        for child in self.list_frame.winfo_children():
            child.destroy()
        self._images.clear()
        try:
            projects = storage.list_projects()
        except Exception as exc:  # noqa: BLE001 - shown in the list area
            ctk.CTkLabel(self.list_frame, text=f"Nepavyko nuskaityti projektų: {exc}", text_color=theme.DANGER).pack(anchor="w")
            return
        if not projects:
            ctk.CTkLabel(self.list_frame, text="Kol kas karuselių nėra. Sukurkite pirmąją aukščiau.", font=_font(theme.FONT_SIZE_BODY), text_color=theme.TEXT_MUTED).pack(anchor="w", pady=theme.SPACE_MD)
            return
        for summary in projects:
            self._row(summary)

    def _row(self, summary: ProjectSummary) -> None:
        card = Card(self.list_frame)
        card.pack(fill="x", pady=4)
        thumb = summary.thumbnail_path
        if thumb.is_file():
            try:
                with Image.open(thumb) as img:
                    pil = img.copy()
                height = 96
                image = ctk.CTkImage(light_image=pil, dark_image=pil, size=(round(pil.width * height / pil.height), height))
                self._images.append(image)
                label = ctk.CTkLabel(card, text="", image=image, cursor="hand2")
                label.pack(side="left", padx=theme.SPACE_SM, pady=theme.SPACE_SM)
                label.bind("<Button-1>", lambda _e, pid=summary.id: self._on_open(pid))
            except OSError:
                pass
        info = ctk.CTkFrame(card, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True, padx=theme.SPACE_SM)
        ctk.CTkLabel(info, text=summary.name, font=_font(theme.FONT_SIZE_SUBTITLE, True), text_color=theme.TEXT_PRIMARY, anchor="w").pack(fill="x")
        ctk.CTkLabel(info, text=f"{FORMAT_SHORT.get(summary.format, summary.format)} · {summary.slide_count} skaidrės · atnaujinta {_when(summary.updated_at)}", font=_font(), text_color=theme.TEXT_SECONDARY, anchor="w").pack(fill="x")
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(side="right", padx=theme.SPACE_SM)
        ctk.CTkButton(actions, text="Tęsti", width=80, command=lambda: self._on_open(summary.id)).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="Pervadinti", width=90, fg_color=theme.BG_CARD_HOVER, command=lambda: self._rename(summary)).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="Dubliuoti", width=90, fg_color=theme.BG_CARD_HOVER, command=lambda: self._duplicate(summary)).pack(side="left", padx=2)
        ctk.CTkButton(actions, text="Ištrinti", width=80, fg_color=theme.DANGER, hover_color="#C9475A", command=lambda: self._delete(summary)).pack(side="left", padx=2)

    def _create(self) -> None:
        fmt = next(k for k, v in FORMATS.items() if v[0] == self.format_var.get())
        name = self.name_entry.get().strip() or "Nauja karuselė"
        try:
            project = storage.create_project(name, fmt, int(self.count_var.get()))
        except (OSError, StorageError) as exc:
            messagebox.showerror("Karuselių kūrimas", str(exc), parent=self.winfo_toplevel())
            return
        self.name_entry.delete(0, "end")
        self._on_open(project.id)

    def _rename(self, summary: ProjectSummary) -> None:
        dialog = ctk.CTkInputDialog(text="Naujas pavadinimas:", title="Pervadinti karuselę")
        name = dialog.get_input()
        if name and name.strip():
            storage.rename_project(summary.id, name)
            self.refresh()

    def _duplicate(self, summary: ProjectSummary) -> None:
        storage.duplicate_project(summary.id)
        self.refresh()

    def _delete(self, summary: ProjectSummary) -> None:
        if messagebox.askyesno("Ištrinti karuselę", f"Ištrinti „{summary.name}“? Šio veiksmo atšaukti negalima.", parent=self.winfo_toplevel()):
            storage.delete_project(summary.id)
            self.refresh()


class CarouselStudioView(ctk.CTkFrame):
    def __init__(self, master, *, llm: LLMClient | None = None, navigate: Callable[..., None] | None = None) -> None:
        super().__init__(master, fg_color="transparent")
        self.llm = llm
        self._navigate = navigate
        self.library = LibraryScreen(self, on_open=self.open_project)
        self.editor = EditorScreen(self, on_back=self.show_library)
        self.library.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=theme.SPACE_LG)
        self.library.refresh()

    def refresh(self) -> None:
        if self.library.winfo_ismapped() or not self.editor.winfo_ismapped():
            self.library.refresh()

    def show_library(self) -> None:
        self.editor.pack_forget()
        self.library.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=theme.SPACE_LG)
        self.library.refresh()

    def open_project(self, project_id: str) -> None:
        try:
            project = storage.load_project(project_id)
        except StorageError as exc:
            messagebox.showerror("Karuselių kūrimas", str(exc), parent=self.winfo_toplevel())
            return
        self.library.pack_forget()
        self.editor.pack(fill="both", expand=True, padx=theme.SPACE_SM, pady=theme.SPACE_SM)
        self.editor.load(project)
