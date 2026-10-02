"""My Projects panel (module brief, section 7): "Content Library" -
lists every saved Content Studio project with its topic, per-content-
type workflow status, and creation date, and lets a person reopen one.

Stage 1 of this feature's staged rollout (see jarvis.content_studio's
own __init__.py docstring) shows the saved plan and per-content-type
status only - actually reopening a linked jarvis.reel_generator/
jarvis.design_studio project for editing is Stage 2, not built yet."""

from __future__ import annotations

import customtkinter as ctk

from jarvis.content_studio import db
from jarvis.content_studio.plan import CONTENT_TYPES, ContentPlanItem
from jarvis.gui import theme
from jarvis.gui.views.content_studio.common import ContentProjectCard, status_label
from jarvis.gui.widgets import Card, SectionHeader

_PROJECTS_LIMIT = 50


class MyProjectsPanel(ctk.CTkFrame):
    def __init__(self, master) -> None:
        super().__init__(master, fg_color="transparent")

        SectionHeader(self, "My Projects").pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._detail_container = ctk.CTkFrame(self, fg_color="transparent")
        self._detail_container.pack(fill="x", pady=(0, theme.SPACE_MD))

        self._grid_container = ctk.CTkFrame(self, fg_color="transparent")
        self._grid_container.pack(fill="x")

        self.refresh()

    def refresh(self) -> None:
        """Matches the refresh() contract jarvis.gui.app._navigate()
        calls on every panel it shows - re-lists projects (cheap, local
        SQLite read) on every visit, same convention every other
        feature module's own dashboard already established (see
        jarvis.gui.views.reel_generator.dashboard.ReelGeneratorView
        .refresh())."""
        self._clear_container(self._grid_container)
        records = db.list_projects(limit=_PROJECTS_LIMIT)
        if not records:
            ctk.CTkLabel(
                self._grid_container, text="No projects yet - create one from the New Content tab.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w")
            return

        grid = ctk.CTkFrame(self._grid_container, fg_color="transparent")
        grid.pack(fill="x")
        columns = 3
        for i in range(columns):
            grid.columnconfigure(i, weight=1)
        for index, record in enumerate(records):
            row, col = divmod(index, columns)
            statuses = {ct: record.status_for(ct) for ct in CONTENT_TYPES}
            card = ContentProjectCard(
                grid, topic=record.topic, statuses=statuses, created_at=record.created_at,
                on_click=lambda r=record: self._open_project(r.id),
            )
            card.grid(row=row, column=col, sticky="nsew", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

    def _open_project(self, project_id: str) -> None:
        record = db.get_project(project_id)
        self._clear_container(self._detail_container)
        if record is None:
            status_label(self._detail_container, "That project could no longer be found.", kind="error").pack(anchor="w")
            return

        card = Card(self._detail_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text=record.topic,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        if record.plan_data is None:
            ctk.CTkLabel(
                inner, text="This project has no saved content plan.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w")
            return

        items = record.plan_data.get("items", [])
        for item_data in items:
            content_type = item_data.get("content_type")
            if content_type is None:
                continue
            item = ContentPlanItem(
                content_type=content_type, angle=item_data.get("angle", ""),
                objective=item_data.get("objective", ""), cta=item_data.get("cta", ""),
            )
            status = record.status_for(content_type)
            row = ctk.CTkFrame(inner, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
            row.pack(fill="x", pady=(0, theme.SPACE_XS))
            row_inner = ctk.CTkFrame(row, fg_color="transparent")
            row_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)
            ctk.CTkLabel(
                row_inner, text=f"{item.label}  ·  {status.upper()}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=theme.TEXT_SECONDARY, anchor="w",
            ).pack(anchor="w")
            ctk.CTkLabel(
                row_inner, text=item.angle,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=560, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def _clear_container(self, container: ctk.CTkFrame) -> None:
        for widget in container.winfo_children():
            widget.destroy()
