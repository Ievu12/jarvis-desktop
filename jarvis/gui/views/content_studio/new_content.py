"""New Content panel (module brief, sections 1-2, Stage 2 sections
3-4): a plain-language topic prompt + [Generate Content Plan] button,
showing the resulting per-content-type angle/objective/CTA plan, with a
[Create this X] action per content type that actually creates a linked
jarvis.reel_generator or jarvis.design_studio project (Stage 2's own
orchestration - see jarvis.content_studio.orchestrator's own docstring
for the exact reuse mechanism).

Stage 1 covered topic -> ContentPlan generation and display only.
Stage 2 (this update) adds: [Create this Reel/Post/Story/Carousel]
actually calling jarvis.content_studio.orchestrator.create_reel_project()
/.create_design_project(), a per-content-type creating/created/failed
UI state (module brief Stage 2 requirements 7-8), a real error message
with a [Retry] action on failure (never faked success - requirements
10-11), and Preview/Regenerate/Approve/Export next-step actions once a
project is linked (requirement 9) - Regenerate/Approve/Export route to
the LINKED project's own existing UI actions in
jarvis.gui.views.reel_generator.dashboard/jarvis.gui.views.design_studio
.dashboard (via this panel's own `navigate` callback opening that
module's nav tab) rather than reimplementing script-approval or export
here - this orchestrator's own job ends once the linked project exists
and is reviewable; PDF's own [Create this PDF] remains "not built yet"
(Stage 3).

The content-plan LLM call AND each content-type's own creation call run
through jarvis.gui.worker.run_generation_in_background() on the SHARED
result queue this panel is constructed with (see
jarvis.gui.views.content_studio.dashboard's own docstring for why one
shared queue/poll loop serves every tab)."""

from __future__ import annotations

import dataclasses
import queue
from typing import Any, Callable

import customtkinter as ctk

from jarvis.content_studio import db, orchestrator, storage
from jarvis.content_studio.db import ProjectRecord
from jarvis.content_studio.plan import CONTENT_TYPES, ContentPlan, ContentPlanItem, generate_content_plan
from jarvis.content_studio.storage import ContentProject, StorageError
from jarvis.core.llm import LLMClient
from jarvis.gui import theme
from jarvis.gui.views.content_studio.common import status_label, workflow_badge_color
from jarvis.gui.widgets import Card, SectionHeader
from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background

# content_type -> the sidebar nav key its linked project's own module
# lives under (jarvis.gui.sidebar.NAV_ITEMS) - used only to route the
# person to that module's own existing UI for Regenerate/Approve/Export
# (this panel's own orchestration ends once the linked project exists;
# it never reimplements those actions - see this module's own
# docstring).
_NAV_KEY_BY_CONTENT_TYPE = {
    "reel": "reel_generator", "story": "design_studio", "post": "design_studio", "carousel": "design_studio",
}


class NewContentPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, llm: LLMClient | None, result_queue: "queue.Queue[Any]",
        on_plan_created: Callable[[], None] | None = None,
        # Typed Callable[..., None] rather than this codebase's usual
        # Callable[[str], None] navigate signature (see e.g.
        # jarvis.gui.views.reel_generator.dashboard's own `navigate`
        # param) - this panel is the one caller that actually needs
        # jarvis.gui.app._navigate()'s own open_project_id keyword
        # argument (see that method's own docstring), so its own type
        # hint must allow passing it.
        navigate: Callable[..., None] | None = None,
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self._llm = llm
        self._result_queue = result_queue
        self._on_plan_created = on_plan_created
        self._navigate = navigate
        self._current_project: ContentProject | None = None
        self._current_plan: ContentPlan | None = None
        self._creating_types: set[str] = set()

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_LG, pady=theme.SPACE_LG)

        ctk.CTkLabel(
            inner, text="What's your topic?",
            font=ctk.CTkFont(family=theme.FONT_FAMILY, size=theme.FONT_SIZE_TITLE, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._topic_entry = ctk.CTkTextbox(inner, height=60, wrap="word", fg_color=theme.BG_SURFACE)
        self._topic_entry.pack(fill="x", pady=(0, theme.SPACE_SM))

        ctk.CTkLabel(
            inner, text='Example: "5 minučių rytinė joga geresnei dienos pradžiai"',
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_MD))

        self._create_button = ctk.CTkButton(
            inner, text="✨ CREATE CONTENT PLAN", command=self._on_create_clicked, height=40,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
        )
        self._create_button.pack(anchor="w")

        self._status_container = ctk.CTkFrame(self, fg_color="transparent")
        self._status_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._plan_container = ctk.CTkFrame(self, fg_color="transparent")
        self._plan_container.pack(fill="x", pady=(theme.SPACE_MD, 0))

    def handle_result(self, result: GenerationTaskResult) -> None:
        """Called by the parent dashboard's own queue-poll loop when a
        result whose `.source` identifies this panel arrives - see
        jarvis.gui.views.content_studio.dashboard's own docstring for
        the shared-queue routing convention this mirrors. `result.source`
        is either this panel itself (the content-plan call) or a
        (content_type, panel) tuple (a per-content-type creation call -
        see _on_create_type_clicked() below)."""
        if isinstance(result.source, tuple):
            content_type, _panel = result.source
            self._handle_create_type_result(content_type, result)
            return
        self._handle_create_plan_result(result)

    def _handle_create_plan_result(self, result: GenerationTaskResult) -> None:
        self._create_button.configure(state="normal")
        if result.error:
            self._set_status(f"Content plan creation failed: {result.error}", kind="error")
            return
        if isinstance(result.value, str):
            # _create_content_plan() returns a plain error string
            # instead of a (project, plan) tuple when creation failed -
            # checked BEFORE unpacking, per this codebase's established
            # "string on failure, tuple on success" convention (see
            # jarvis.gui.views.reel_generator.dashboard's own
            # _handle_create_result() for the identical pattern).
            self._set_status(result.value, kind="error")
            return

        project, plan = result.value
        self._current_project = project
        self._current_plan = plan
        self._clear_status()
        self._render_plan(project, plan)
        if self._on_plan_created is not None:
            self._on_plan_created()

    def _on_create_clicked(self) -> None:
        topic = self._topic_entry.get("1.0", "end").strip()
        if not topic:
            self._set_status("Describe your topic first.", kind="error")
            return
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return

        self._set_status("Creating your content plan...", kind="loading")
        self._create_button.configure(state="disabled")
        self._clear_container(self._plan_container)
        llm = self._llm
        run_generation_in_background(
            lambda: _create_content_plan(llm, topic), self._result_queue, source=self,
        )

    def _render_plan(self, project: ContentProject, plan: ContentPlan) -> None:
        self._clear_container(self._plan_container)
        card = Card(self._plan_container)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="AI CONTENT PLAN",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        record = db.get_project(project.project_id)

        for content_type in CONTENT_TYPES:
            item = plan.item_for(content_type)
            if item is None:
                continue
            self._render_item_row(inner, project, item, record)

    def _render_item_row(
        self, master: ctk.CTkFrame, project: ContentProject, item: ContentPlanItem, record: ProjectRecord | None,
    ) -> None:
        row = ctk.CTkFrame(master, fg_color=theme.BG_SURFACE, corner_radius=theme.RADIUS_BUTTON)
        row.pack(fill="x", pady=(0, theme.SPACE_XS))
        row_inner = ctk.CTkFrame(row, fg_color="transparent")
        row_inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        header_row = ctk.CTkFrame(row_inner, fg_color="transparent")
        header_row.pack(fill="x")
        ctk.CTkLabel(
            header_row, text=item.label,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(side="left")

        status = record.status_for(item.content_type) if record is not None else "planned"
        if item.content_type in self._creating_types:
            status = "creating"
        if status != "planned":
            ctk.CTkLabel(
                header_row, text=status.upper(),
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION, weight="bold"),
                text_color=workflow_badge_color(status), anchor="w",
            ).pack(side="left", padx=(theme.SPACE_SM, 0))

        ctk.CTkLabel(
            row_inner, text=f"Angle: {item.angle}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w", wraplength=520, justify="left",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
        ctk.CTkLabel(
            row_inner, text=f"CTA: {item.cta}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=520, justify="left",
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        error = record.error_for(item.content_type) if record is not None else None
        if status == "failed" and error:
            ctk.CTkLabel(
                row_inner, text=f"Error: {error}",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.DANGER, anchor="w", wraplength=520, justify="left",
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

        button_row = ctk.CTkFrame(row_inner, fg_color="transparent")
        button_row.pack(fill="x", pady=(theme.SPACE_SM, 0))

        if item.content_type == "pdf":
            ctk.CTkButton(
                button_row, text="Create this PDF", command=self._on_create_pdf_clicked,
                width=160, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=1, border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left")
            return

        linked_project_id = record.linked_project_id_for(item.content_type) if record is not None else None

        if item.content_type in self._creating_types:
            ctk.CTkButton(
                button_row, text="Creating...", state="disabled", width=160, height=26,
                fg_color=theme.BG_CARD, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left")
            return

        if status == "failed":
            ctk.CTkButton(
                button_row, text=f"🔄 Retry {item.label}",
                command=lambda: self._on_create_type_clicked(project, item), width=160, height=26,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left")
            return

        if linked_project_id is None:
            ctk.CTkButton(
                button_row, text=f"Create this {item.label}",
                command=lambda: self._on_create_type_clicked(project, item), width=160, height=26,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            ).pack(side="left")
            return

        # A project is already linked - offer Preview/Regenerate/Edit/
        # Approve/Export as navigation into that module's own existing
        # UI (module brief Stage 2 requirement 9) rather than
        # reimplementing any of those actions here. Passing
        # linked_project_id through means the target view opens THIS
        # SPECIFIC project (its real, already-saved brief/script/etc. -
        # see jarvis.gui.app._navigate()'s own docstring for the
        # open_project_id mechanism), not just its generic dashboard.
        nav_key = _NAV_KEY_BY_CONTENT_TYPE[item.content_type]
        ctk.CTkButton(
            button_row, text="👁️ Preview / Continue",
            command=lambda: self._navigate_to(nav_key, linked_project_id), width=150, height=26,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
            border_width=1, border_color=theme.BORDER_SUBTLE,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            button_row, text="🔄 Regenerate", command=lambda: self._on_create_type_clicked(project, item),
            width=110, height=26, fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
            border_width=1, border_color=theme.BORDER_SUBTLE,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        ).pack(side="left")

    def _navigate_to(self, nav_key: str, linked_project_id: str) -> None:
        if self._navigate is not None:
            self._navigate(nav_key, open_project_id=linked_project_id)
        else:
            self._set_status(f"Open '{nav_key}' from the sidebar to continue this project.", kind="muted")

    def _on_create_pdf_clicked(self) -> None:
        self._set_status(
            "The PDF Creator is coming in a later update - it is not built yet.", kind="muted",
        )

    def _on_create_type_clicked(self, project: ContentProject, item: ContentPlanItem) -> None:
        if self._llm is None:
            self._set_status("JARVIS AI is not available (no API key configured).", kind="error")
            return
        if item.content_type in self._creating_types:
            return  # already in flight - the button is disabled while creating, but guard anyway

        self._creating_types.add(item.content_type)
        db.set_content_type_status(project.project_id, item.content_type, "creating")
        self._clear_status()
        self._refresh_plan_display()

        llm = self._llm
        run_generation_in_background(
            lambda: _create_linked_project(llm, item),
            self._result_queue, source=(item.content_type, self),
        )
        # _handle_create_type_result() reads self._current_project for
        # the project to link/update - `source` only needs to identify
        # (content_type, panel) for routing this result back here.

    def _handle_create_type_result(self, content_type: str, result: GenerationTaskResult) -> None:
        self._creating_types.discard(content_type)
        project = self._current_project
        if project is None:
            return

        if result.error:
            db.set_content_type_failed(project.project_id, content_type, result.error)
            self._set_status(f"{content_type.title()} creation failed: {result.error}", kind="error")
            self._refresh_plan_display()
            return
        if isinstance(result.value, str):
            # create_reel_project()/create_design_project() return a
            # plain error string on failure - checked BEFORE unpacking,
            # per this module's own established convention.
            db.set_content_type_failed(project.project_id, content_type, result.value)
            self._set_status(result.value, kind="error")
            self._refresh_plan_display()
            return

        linked_result = result.value
        if content_type == "reel":
            db.link_content_type_project(project.project_id, "reel", linked_result.reel_generator_project_id)
        else:
            db.link_content_type_project(project.project_id, content_type, linked_result.design_studio_project_id)
        db.set_content_type_status(project.project_id, content_type, "created")

        self._clear_status()
        self._refresh_plan_display()
        if self._on_plan_created is not None:
            self._on_plan_created()

    def _refresh_plan_display(self) -> None:
        if self._current_project is not None and self._current_plan is not None:
            self._render_plan(self._current_project, self._current_plan)

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


def _create_content_plan(llm: LLMClient, topic: str):
    """Runs on a background thread - creates a new Content Studio
    project, then generates its content plan. Returns (project, plan)
    on success, or a plain error string on any failure - mirrors
    jarvis.gui.views.reel_generator.dashboard._create_reel_concept()'s
    exact "string on failure, tuple on success" convention."""
    try:
        project = storage.create_project()
    except StorageError as e:
        return str(e)

    db.create_project_record(project.project_id, topic)

    plan = generate_content_plan(llm, topic)
    if plan is None:
        return "JARVIS couldn't create a content plan for that topic - try rephrasing it."
    db.save_plan(project.project_id, dataclasses.asdict(plan))

    return project, plan


def _create_linked_project(llm: LLMClient, item: ContentPlanItem):
    """Runs on a background thread - Stage 2's own dispatch to
    jarvis.content_studio.orchestrator, routing by content_type. Returns
    whatever the underlying orchestrator function returns
    (ReelCreationResult/DesignCreationResult on success, a plain error
    string on failure) - this function itself never raises beyond what
    those functions already guarantee not to."""
    if item.content_type == "reel":
        return orchestrator.create_reel_project(llm, item)
    return orchestrator.create_design_project(llm, item)
