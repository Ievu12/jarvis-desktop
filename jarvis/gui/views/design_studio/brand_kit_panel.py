"""My Brand panel (module brief, section 6): configure the person's
Personal Brand Kit - brand name, logo, primary/secondary/accent color,
Instagram username - which jarvis.design_studio.brand_kit.apply_brand_kit()
then folds into every new design automatically.

Preferred fonts (module brief) is NOT exposed as a UI control here -
this codebase has exactly two confirmed-working fonts (Arial/Arial
Bold, per jarvis.design_studio.styles' own docstring), so a font-choice
UI would offer only one real choice; the field exists in
jarvis.design_studio.brand_kit.BrandKit for forward compatibility
(documented on that field itself) but isn't user-facing yet.

Colors are entered as plain hex strings (e.g. "#FF6B9D") rather than a
color-picker widget - customtkinter has no built-in color picker, and
adding a picker dependency/widget for three fields is disproportionate
for this stage; a hex entry with a live-preview swatch (this panel's
own _ColorField) is simple and reliable, matching the module brief's
own "Keep the editor simple" instruction stated for a different
section (10) but equally applicable here.

Logo upload uses tkinter.filedialog.askopenfilename(), the established
pattern jarvis.gui.views.video_studio.dashboard's own docstring
documents (no drag-and-drop anywhere in this GUI) - copied into
jarvis.config.DESIGN_STUDIO_BRAND_KIT_DIR via jarvis.design_studio
.storage.SUPPORTED_IMAGE_EXTENSIONS' same validation
jarvis.design_studio.storage.save_uploaded_asset() uses for a
per-project upload, but saved into the Brand Kit's own shared directory
instead (a logo is reused across every project, not scoped to one -
see jarvis.config.DESIGN_STUDIO_BRAND_KIT_DIR's own docstring)."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

from jarvis.config import DESIGN_STUDIO_BRAND_KIT_DIR
from jarvis.design_studio.brand_kit import BrandKit, get_brand_kit, reset_brand_kit, save_brand_kit
from jarvis.design_studio.storage import SUPPORTED_IMAGE_EXTENSIONS
from jarvis.gui import theme
from jarvis.gui.views.design_studio.common import status_label
from jarvis.gui.widgets import Card, SectionHeader

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_LOGO_FILETYPES = (("Image files", "*.png *.jpg *.jpeg *.webp"), ("All files", "*.*"))


class _ColorField(ctk.CTkFrame):
    """A labeled hex-color entry with a live swatch preview - the
    swatch updates on every keystroke if the current text is a valid
    hex color, and shows a neutral placeholder otherwise (never crashes
    on invalid/partial input)."""

    def __init__(self, master, label: str, **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        self._swatch = ctk.CTkFrame(
            row, width=28, height=28, fg_color=theme.BG_SURFACE, corner_radius=6,
            border_width=1, border_color=theme.BORDER_SUBTLE,
        )
        self._swatch.pack(side="left", padx=(0, theme.SPACE_SM))
        self._swatch.pack_propagate(False)
        self._entry = ctk.CTkEntry(row, placeholder_text="#RRGGBB")
        self._entry.pack(side="left", fill="x", expand=True)
        self._entry.bind("<KeyRelease>", lambda _e: self._update_swatch())

    def _update_swatch(self) -> None:
        value = self._entry.get().strip()
        if _HEX_COLOR_RE.match(value):
            self._swatch.configure(fg_color=value)
        else:
            self._swatch.configure(fg_color=theme.BG_SURFACE)

    def get(self) -> str | None:
        value = self._entry.get().strip()
        return value if _HEX_COLOR_RE.match(value) else None

    def set(self, value: str | None) -> None:
        self._entry.delete(0, "end")
        if value:
            self._entry.insert(0, value)
        self._update_swatch()


class BrandKitPanel(ctk.CTkFrame):
    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")
        self._logo_path: str | None = None

        SectionHeader(self, "MY BRAND").pack(anchor="w", pady=(0, theme.SPACE_SM))

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        self._brand_name_entry = ctk.CTkEntry(inner, placeholder_text="Brand name")
        self._brand_name_entry.pack(fill="x", pady=(0, theme.SPACE_SM))

        self._instagram_entry = ctk.CTkEntry(inner, placeholder_text="Instagram username (without @)")
        self._instagram_entry.pack(fill="x", pady=(0, theme.SPACE_MD))

        colors_row = ctk.CTkFrame(inner, fg_color="transparent")
        colors_row.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._primary_field = _ColorField(colors_row, "Primary color:")
        self._primary_field.pack(side="left", padx=(0, theme.SPACE_MD), fill="x", expand=True)
        self._secondary_field = _ColorField(colors_row, "Secondary color:")
        self._secondary_field.pack(side="left", padx=(0, theme.SPACE_MD), fill="x", expand=True)
        self._accent_field = _ColorField(colors_row, "Accent color:")
        self._accent_field.pack(side="left", fill="x", expand=True)

        logo_row = ctk.CTkFrame(inner, fg_color="transparent")
        logo_row.pack(fill="x", pady=(0, theme.SPACE_MD))
        ctk.CTkButton(
            logo_row, text="Upload Logo", command=self._on_upload_logo_clicked, width=130,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_SM))
        self._logo_label = ctk.CTkLabel(
            logo_row, text="No logo uploaded",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED, anchor="w",
        )
        self._logo_label.pack(side="left")

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(button_row, text="Save Brand Kit", command=self._on_save_clicked, width=140).pack(
            side="left", padx=(0, theme.SPACE_SM),
        )
        ctk.CTkButton(
            button_row, text="Reset", command=self._on_reset_clicked, width=100,
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

        self._status_container = ctk.CTkFrame(inner, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._load_from_saved()

    # --- load/save ---------------------------------------------------------------------------

    def _load_from_saved(self) -> None:
        kit = get_brand_kit()
        self._brand_name_entry.delete(0, "end")
        if kit.brand_name:
            self._brand_name_entry.insert(0, kit.brand_name)
        self._instagram_entry.delete(0, "end")
        if kit.instagram_username:
            self._instagram_entry.insert(0, kit.instagram_username)
        self._primary_field.set(kit.primary_color)
        self._secondary_field.set(kit.secondary_color)
        self._accent_field.set(kit.accent_color)
        self._logo_path = kit.logo_path
        self._logo_label.configure(text=Path(kit.logo_path).name if kit.logo_path else "No logo uploaded")

    def _on_upload_logo_clicked(self) -> None:
        selected = filedialog.askopenfilename(title="Select a logo image", filetypes=_LOGO_FILETYPES)
        if not selected:
            return
        source = Path(selected)
        if source.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
            supported = ", ".join(sorted(e.lstrip(".").upper() for e in SUPPORTED_IMAGE_EXTENSIONS))
            self._set_status(f"Unsupported file type. Supported formats: {supported}.", kind="error")
            return

        DESIGN_STUDIO_BRAND_KIT_DIR.mkdir(parents=True, exist_ok=True)
        destination = DESIGN_STUDIO_BRAND_KIT_DIR / source.name
        try:
            shutil.copy2(source, destination)
        except OSError as e:
            self._set_status(f"Couldn't upload logo: {e}", kind="error")
            return
        self._logo_path = str(destination)
        self._logo_label.configure(text=destination.name)
        self._set_status("Logo uploaded - click Save Brand Kit to keep it.", kind="muted")

    def _on_save_clicked(self) -> None:
        kit = BrandKit(
            brand_name=self._brand_name_entry.get().strip() or None,
            logo_path=self._logo_path,
            primary_color=self._primary_field.get(),
            secondary_color=self._secondary_field.get(),
            accent_color=self._accent_field.get(),
            preferred_fonts=[],
            preferred_style=None,
            instagram_username=self._instagram_entry.get().strip() or None,
        )
        save_brand_kit(kit)
        self._set_status("Brand Kit saved - it will be applied to new designs automatically.", kind="muted")

    def _on_reset_clicked(self) -> None:
        reset_brand_kit()
        self._load_from_saved()
        self._set_status("Brand Kit reset.", kind="muted")

    # --- status --------------------------------------------------------------------------

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")
