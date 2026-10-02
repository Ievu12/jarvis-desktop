"""AI Design Studio dashboard (module brief, section 1): the top-level
view for the sidebar's "🎨 AI Design Studio" nav item - a plain-
language prompt box, Format/Style dropdowns, a [✨ CREATE DESIGN]
button, a 3-variant picker, a Brand Kit panel, and a "Recent Designs"
strip.

Stage 2 of this feature's staged rollout adds Brand Kit and the
3-variant picker on top of Stage 1's Design Brief + Create Design +
Export + Storage: a person types a request, JARVIS generates a
structured DesignBrief (jarvis.design_studio.brief), applies the
configured Brand Kit's colors to the style (jarvis.design_studio
.brand_kit.apply_brand_kit() - a no-op if no Brand Kit is saved), then
renders 3 style variants (jarvis.design_studio.variants) shown side by
side with [Use this]/[Regenerate]/[Duplicate] actions per variant -
[Edit] and AI Rewrite are Stage 3 work, not built yet; that button says
so plainly rather than hiding it, per this feature's own brief's "Do
not claim a feature works unless it has been tested."

Both the LLM brief-generation call and the Pillow rendering (3 variants
per generation) are real, possibly-slow-enough-to-notice work, so both
run through jarvis.gui.worker.run_generation_in_background() on this
view's own polled result queue, exactly like every other feature
module's generation panels in this codebase (jarvis.gui.views
.video_studio's panels, jarvis.gui.views.instagram_ai_manager
.content_studio's tools - see either's own docstring for why a
dedicated queue per feature area).
"""

from __future__ import annotations

import dataclasses
import queue
import shutil
import time
from pathlib import Path
from typing import Any, Callable

import customtkinter as ctk

from jarvis.core.llm import LLMClient
from jarvis.design_studio import db, storage
from jarvis.design_studio.brand_kit import apply_brand_kit, get_brand_kit
from jarvis.design_studio.brief import FORMAT_CHOICES, DesignBrief, generate_design_brief
from jarvis.design_studio.render import RenderResult
from jarvis.design_studio.storage import DesignProject, StorageError
from jarvis.design_studio.styles import AUTO_STYLE, STYLE_CHOICES, resolve_style
from jarvis.design_studio.variants import DesignVariant, generate_variants
from jarvis.gui import theme
from jarvis.gui.views.design_studio.brand_kit_panel import BrandKitPanel
from jarvis.gui.views.design_studio.common import DesignCard, LabeledDropdown, format_file_size, status_label
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background

_QUEUE_POLL_INTERVAL_MS = 100
_RECENT_DESIGNS_LIMIT = 12

_FORMAT_LABELS = {
    "story": "Story 9:16", "post": "Post 4:5", "square": "Square 1:1",
    "reel_cover": "Reel Cover 9:16", "carousel": "Carousel 4:5",
}
_FORMAT_DROPDOWN_VALUES = tuple(_FORMAT_LABELS[f] for f in FORMAT_CHOICES)
_FORMAT_LABEL_TO_KEY = {v: k for k, v in _FORMAT_LABELS.items()}

_STYLE_LABELS = {AUTO_STYLE: "Choose automatically"} | {
    name: name.replace("_", " ").title() for name in STYLE_CHOICES if name != AUTO_STYLE
}
_STYLE_DROPDOWN_VALUES = tuple(_STYLE_LABELS[s] for s in STYLE_CHOICES)
_STYLE_LABEL_TO_KEY = {v: k for k, v in _STYLE_LABELS.items()}


class DesignStudioView(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None, navigate: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._navigate = navigate
        self._result_queue: "queue.Queue[Any]" = queue.Queue()
        self._current_project: DesignProject | None = None

        SectionHeader(self, "AI Design Studio").pack(
            anchor="w", padx=theme.SPACE_LG, pady=(theme.SPACE_LG, theme.SPACE_SM),
        )

        self._scroll = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self._scroll.pack(fill="both", expand=True, padx=theme.SPACE_LG, pady=(0, theme.SPACE_LG))

        self._build_prompt_area()

        self._status_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._variants_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._variants_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._design_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._design_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

        self._brand_kit_panel = BrandKitPanel(self._scroll)
        self._brand_kit_panel.pack(fill="x", pady=(theme.SPACE_LG, 0))

        self._recent_section_label = SectionHeader(self._scroll, "Recent Designs")
        self._recent_section_label.pack(anchor="w", pady=(theme.SPACE_LG, theme.SPACE_SM))
        self._recent_designs_container = ctk.CTkFrame(self._scroll, fg_color="transparent")
        self._recent_designs_container.pack(fill="x")

        self._refresh_recent_designs()
        self._poll_queue()

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate()
        calls on every panel it shows - re-lists recent designs (cheap,
        local SQLite read) on every visit, same convention
        jarvis.gui.views.video_studio.dashboard.VideoStudioView.refresh()
        already established."""
        self._refresh_recent_designs()

    def open_project(self, project_id: str) -> None:
        """Public wrapper over _open_design() - loads and displays an
        already-created design project exactly as clicking that
        project's own card in "Recent Designs" already does. Exists so
        a caller OUTSIDE this module (jarvis.gui.app._navigate(), on
        behalf of jarvis.content_studio's own "Preview / Continue"
        action) can open a specific linked project - see
        jarvis.gui.views.reel_generator.dashboard.ReelGeneratorView
        .open_project()'s own docstring for the identical reasoning
        applied to the equivalent Reel Generator case. _open_design()
        itself is completely unmodified."""
        self._open_design(project_id)

    # --- prompt area ---------------------------------------------------------------------

    def _build_prompt_area(self) -> None:
        card = Card(self._scroll)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="What do you want to create?",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._prompt_entry = ctk.CTkTextbox(inner, height=70, wrap="word", fg_color=theme.BG_SURFACE)
        self._prompt_entry.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._prompt_entry.insert("1.0", "")

        ctk.CTkLabel(
            inner,
            text='Example: "Create an Instagram Story about 3 morning yoga exercises."',
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        controls_row = ctk.CTkFrame(inner, fg_color="transparent")
        controls_row.pack(fill="x", pady=(0, theme.SPACE_MD))
        self._format_dropdown = LabeledDropdown(controls_row, "Format:", _FORMAT_DROPDOWN_VALUES)
        self._format_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))
        self._style_dropdown = LabeledDropdown(controls_row, "Style:", _STYLE_DROPDOWN_VALUES)
        self._style_dropdown.pack(side="left")

        self._create_button = ctk.CTkButton(
            inner, text="✨ CREATE DESIGN", command=self._on_create_clicked, height=40,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
        )
        self._create_button.pack(anchor="w")

    def _on_create_clicked(self) -> None:
        request_text = self._prompt_entry.get("1.0", "end").strip()
        if not request_text:
            self._set_status("Describe what you want to create first.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        format_key = _FORMAT_LABEL_TO_KEY.get(self._format_dropdown.get())
        style_key = _STYLE_LABEL_TO_KEY.get(self._style_dropdown.get(), AUTO_STYLE)

        self._set_status("Creating your design brief and 3 variants...", kind="loading")
        self._create_button.configure(state="disabled")
        self._clear_container(self._design_container)
        self._clear_container(self._variants_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _create_design(llm, request_text, format_key, style_key),
            self._result_queue, source=("create", self),
        )

    def _on_regenerate_clicked(self) -> None:
        self._on_create_clicked()

    # --- status/container helpers ----------------------------------------------------------

    def _set_status(self, text: str, *, kind: str = "muted") -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()
        status_label(self._status_container, text, kind=kind).pack(anchor="w")

    def _clear_status(self) -> None:
        for widget in self._status_container.winfo_children():
            widget.destroy()

    def _clear_container(self, container: ctk.CTkFrame) -> None:
        for widget in container.winfo_children():
            widget.destroy()

    # --- variant picker --------------------------------------------------------------------

    def _render_variants(self, project: DesignProject, brief: DesignBrief, variants: list[DesignVariant]) -> None:
        self._clear_container(self._variants_container)
        self._clear_container(self._design_container)

        row = ctk.CTkFrame(self._variants_container, fg_color="transparent")
        row.pack(fill="x")
        for i in range(3):
            row.columnconfigure(i, weight=1)

        for i, variant in enumerate(variants):
            card = Card(row)
            card.grid(row=0, column=i, sticky="nsew", padx=theme.SPACE_SM)
            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="both", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

            ctk.CTkLabel(
                inner, text=f"VARIANT {variant.label}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
                text_color=theme.ACCENT_PRIMARY, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))
            ctk.CTkLabel(
                inner, text=variant.style.replace("_", " ").title(),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_SM))

            if variant.error is not None or variant.render_result is None:
                ctk.CTkLabel(
                    inner, text=f"Couldn't render: {variant.error}",
                    font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                    text_color=theme.DANGER, anchor="w", justify="left", wraplength=200,
                ).pack(anchor="w", pady=(0, theme.SPACE_SM))
                continue

            try:
                from PIL import Image

                pil_image = Image.open(variant.render_result.output_path)
                preview_height = 260
                preview_width = int(preview_height * variant.render_result.width / variant.render_result.height)
                ctk_image = ctk.CTkImage(
                    light_image=pil_image, dark_image=pil_image, size=(preview_width, preview_height),
                )
                ctk.CTkLabel(inner, image=ctk_image, text="").pack(pady=(0, theme.SPACE_SM))
            except Exception:
                pass

            button_col = ctk.CTkFrame(inner, fg_color="transparent")
            button_col.pack(fill="x")
            ctk.CTkButton(
                button_col, text="Use this", command=lambda v=variant: self._on_use_variant_clicked(project, brief, v),
                height=28,
            ).pack(fill="x", pady=(0, theme.SPACE_XS))
            small_row = ctk.CTkFrame(button_col, fg_color="transparent")
            small_row.pack(fill="x")
            ctk.CTkButton(
                small_row, text="Duplicate", command=lambda v=variant: self._on_duplicate_variant_clicked(project, v),
                height=26, width=80, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left", padx=(0, theme.SPACE_XS))
            ctk.CTkButton(
                small_row, text="Edit", command=self._on_edit_clicked,
                height=26, width=70, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left")

        regenerate_row = ctk.CTkFrame(self._variants_container, fg_color="transparent")
        regenerate_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(
            regenerate_row, text="Regenerate All 3", command=self._on_regenerate_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w")

    def _on_edit_clicked(self) -> None:
        self._set_status(
            "The lightweight editor (text/color/image adjustments) is coming in a later update.", kind="muted",
        )

    def _on_use_variant_clicked(self, project: DesignProject, brief: DesignBrief, variant: DesignVariant) -> None:
        if variant.render_result is None:
            return
        db.save_design_path(project.project_id, str(variant.render_result.output_path))
        self._clear_status()
        self._render_design_result(project, brief, variant.render_result)
        self._refresh_recent_designs()

    def _on_duplicate_variant_clicked(self, project: DesignProject, variant: DesignVariant) -> None:
        if variant.render_result is None:
            return
        try:
            new_project = storage.create_project()
        except StorageError as e:
            self._set_status(f"Couldn't duplicate: {e}", kind="error")
            return
        record = db.get_project(project.project_id)
        prompt = record.original_prompt if record is not None else "(duplicated design)"
        db.create_project_record(new_project.project_id, f"{prompt} (duplicate)")
        duplicate_path = new_project.variants_dir / variant.render_result.output_path.name
        try:
            shutil.copy2(variant.render_result.output_path, duplicate_path)
        except OSError as e:
            self._set_status(f"Couldn't duplicate: {e}", kind="error")
            return
        if record is not None and record.brief_data is not None:
            db.save_brief(new_project.project_id, record.brief_data)
        db.save_design_path(new_project.project_id, str(duplicate_path))
        self._set_status(f"Duplicated as a new project ({new_project.project_id[:8]}...).", kind="muted")
        self._refresh_recent_designs()

    # --- design display --------------------------------------------------------------------

    def _render_design_result(self, project: DesignProject, brief: DesignBrief, render_result: RenderResult) -> None:
        self._clear_container(self._design_container)
        self._clear_container(self._variants_container)
        card = Card(self._design_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        try:
            from PIL import Image

            pil_image = Image.open(render_result.output_path)
            preview_height = 400
            preview_width = int(preview_height * render_result.width / render_result.height)
            ctk_image = ctk.CTkImage(
                light_image=pil_image, dark_image=pil_image, size=(preview_width, preview_height),
            )
            ctk.CTkLabel(inner, image=ctk_image, text="").pack(pady=(0, theme.SPACE_SM))
        except Exception:
            pass

        ctk.CTkLabel(
            inner, text=f"{brief.format_label}  ·  {brief.style.replace('_', ' ').title()} style",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        info_row = ctk.CTkFrame(inner, fg_color="transparent")
        info_row.pack(fill="x", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            info_row,
            text=(
                f"Resolution: {render_result.width}×{render_result.height}   "
                f"File size: {format_file_size(render_result.file_size_bytes)}"
            ),
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w")

        button_row = ctk.CTkFrame(inner, fg_color="transparent")
        button_row.pack(fill="x")
        ctk.CTkButton(
            button_row, text="Export", command=lambda: self._on_export_clicked(project, render_result),
            width=100, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="Open Folder", command=lambda: self._open_folder(render_result.output_path),
            width=110, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
            border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _on_export_clicked(self, project: DesignProject, render_result: RenderResult) -> None:
        export_path = project.exports_dir / render_result.output_path.name
        if export_path.exists():
            # Module brief: "Never overwrite an existing design
            # automatically." - a repeated Export click gets a
            # distinct, non-colliding filename instead of silently
            # replacing the previous export.
            export_path = project.exports_dir / f"{render_result.output_path.stem}_{int(time.time())}{render_result.output_path.suffix}"
        try:
            shutil.copy2(render_result.output_path, export_path)
        except OSError as e:
            self._set_status(f"Export failed: {e}", kind="error")
            return
        if self._current_project is not None:
            db.set_status(self._current_project.project_id, "exported")
        self._set_status(f"Exported to {export_path}", kind="muted")

    def _open_folder(self, path: Path) -> None:
        import os

        try:
            os.startfile(path.parent)  # noqa: S606 - opening a folder the person's own design was just saved into, not an arbitrary/untrusted path
        except OSError as e:
            self._set_status(f"Couldn't open the folder: {e}", kind="error")

    # --- queue polling -------------------------------------------------------------------

    def _poll_queue(self) -> None:
        try:
            while True:
                result = self._result_queue.get_nowait()
                self._handle_result(result)
        except queue.Empty:
            pass
        self.after(_QUEUE_POLL_INTERVAL_MS, self._poll_queue)

    def _handle_result(self, result: Any) -> None:
        if not isinstance(result, GenerationTaskResult) or not isinstance(result.source, tuple):
            return
        kind, owner = result.source
        if owner is not self:
            return
        if kind == "create":
            self._handle_create_result(result)

    def _handle_create_result(self, result: GenerationTaskResult) -> None:
        self._create_button.configure(state="normal")
        if result.error:
            self._set_status(f"Design creation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            # _create_design() returns a plain error string instead of
            # a (project, brief, variants) tuple when creation failed -
            # checked BEFORE unpacking, per the same unpacking-a-str-
            # silently-"succeeds" hazard jarvis.gui.views.video_studio
            # .dashboard's own _handle_upload_result() documents for
            # the identical pattern.
            self._set_status(result.value, kind="error")
            return

        project, brief, variants = result.value
        self._current_project = project
        self._clear_status()
        self._render_variants(project, brief, variants)
        self._refresh_recent_designs()

    # --- recent designs ----------------------------------------------------------------------

    def _refresh_recent_designs(self) -> None:
        self._clear_container(self._recent_designs_container)
        records = db.list_projects(limit=_RECENT_DESIGNS_LIMIT)
        if not records:
            ctk.CTkLabel(
                self._recent_designs_container, text="No designs yet - describe what you want to create above.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w")
            return

        grid = ctk.CTkFrame(self._recent_designs_container, fg_color="transparent")
        grid.pack(fill="x")
        columns = 4
        for i in range(columns):
            grid.columnconfigure(i, weight=1)
        for index, record in enumerate(records):
            row, col = divmod(index, columns)
            card = DesignCard(
                grid, prompt=record.original_prompt, status=record.status, created_at=record.created_at,
                design_path=record.design_path, on_click=lambda r=record: self._open_design(r.id),
            )
            card.grid(row=row, column=col, sticky="nsew", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

    def _open_design(self, project_id: str) -> None:
        record = db.get_project(project_id)
        if record is None:
            self._set_status("That design could no longer be found.", kind="error")
            return
        project = storage.project_paths(project_id)
        self._current_project = project

        if record.brief_data is not None and record.design_path is not None:
            brief = DesignBrief(**record.brief_data)
            design_path = Path(record.design_path)
            if design_path.is_file():
                render_result = RenderResult(
                    output_path=design_path, width=0, height=0,
                    file_size_bytes=design_path.stat().st_size,
                )
                # width/height aren't stored in the DB - re-probe from
                # the file itself rather than assuming FORMAT_DIMENSIONS
                # still matches (a person could have manually replaced
                # the file) - cheap, since Pillow only reads the header.
                try:
                    from PIL import Image

                    with Image.open(design_path) as img:
                        render_result = dataclasses.replace(render_result, width=img.width, height=img.height)
                except Exception:
                    pass
                self._clear_status()
                self._render_design_result(project, brief, render_result)
                return

        self._set_status(
            "This design's file is missing from disk - it may have been moved or deleted.", kind="error",
        )


def _create_design(llm: LLMClient, request_text: str, format_key: str | None, style_key: str):
    """Runs on a background thread - creates a new project, generates
    the design brief, applies the configured Brand Kit (a no-op if
    none is saved - see jarvis.design_studio.brand_kit.apply_brand_kit()'s
    own docstring), and renders 3 style variants. Returns
    (project, brief, list[DesignVariant]) on success, or a plain error
    string on any failure before variant generation even starts
    (project creation, brief generation) - mirrors
    jarvis.gui.views.video_studio.dashboard
    ._create_and_analyze_project()'s exact "string on failure, tuple on
    success" convention, including the caller-side type check that
    convention's own comment there explains is required BEFORE
    unpacking - see _handle_create_result() above. Once variant
    generation itself starts, a single variant's own failure is
    recorded on that DesignVariant rather than failing the whole call
    (see jarvis.design_studio.variants.generate_variants()'s own
    docstring) - so this function only returns an error string for
    failures BEFORE that point."""
    try:
        project = storage.create_project()
    except StorageError as e:
        return str(e)

    db.create_project_record(project.project_id, request_text)

    brief = generate_design_brief(
        llm, request_text,
        forced_format=format_key if format_key else None,
        forced_style=style_key if style_key != AUTO_STYLE else None,
    )
    if brief is None:
        return "JARVIS couldn't create a design brief for that request - try rephrasing it."
    db.save_brief(project.project_id, dataclasses.asdict(brief))

    brand_kit = get_brand_kit()
    variants = generate_variants(
        brief, output_dir=project.variants_dir,
        style_transform=lambda style: apply_brand_kit(style, brand_kit),
    )
    return project, brief, variants
