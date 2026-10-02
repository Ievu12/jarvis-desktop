"""Tests for jarvis.gui.views.content_studio.dashboard.ContentStudioView
and its two tabs (New Content, My Projects), covering Stage 1 (topic ->
content plan) and Stage 2 (orchestration: "Create this Reel/Post/Story/
Carousel" actually creating a linked jarvis.reel_generator/
jarvis.design_studio project). Uses a real (withdrawn) CTk root with
jarvis.content_studio.db/.storage AND jarvis.reel_generator.db/.storage
AND jarvis.design_studio.db/.storage all redirected to per-test
tmp_path locations. jarvis.content_studio.plan.generate_content_plan and
jarvis.content_studio.orchestrator.create_reel_project()/
.create_design_project() are mocked for these tests (no real LLM call) -
same pattern as tests/test_gui_reel_generator_dashboard.py (see that
file's own docstring for the full rationale).

Confirms: button/status states, the shared-queue routing-by-source
convention for BOTH the content-plan source (the panel itself) and a
per-content-type creation source ((content_type, panel) tuples - Stage
2's own addition), the tuple-vs-error-string unpacking safety for both,
a created project immediately appears in My Projects with the correct
per-content-type status badges, opening a project shows its saved plan,
"Create this Reel/Post/Story/Carousel" shows a creating state then a
created state with the linked project id persisted, a failed creation
shows the real error message with a Retry action that can succeed on a
second attempt, and "Create this PDF" still shows a "not built yet"
message (Stage 3, unaffected by this stage)."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.content_studio import db, orchestrator, storage
from jarvis.design_studio import db as design_db
from jarvis.design_studio import storage as design_storage
from jarvis.gui.views.content_studio import dashboard as dashboard_module
from jarvis.gui.views.content_studio import new_content as new_content_module
from jarvis.gui.views.content_studio.dashboard import ContentStudioView
from jarvis.reel_generator import db as reel_db
from jarvis.reel_generator import storage as reel_storage


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "content_studio.db"
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(db, "CONTENT_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(storage, "CONTENT_STUDIO_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(new_content_module.db, "CONTENT_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(new_content_module.storage, "CONTENT_STUDIO_PROJECTS_DIR", projects_dir)

    reel_db_file = tmp_path / "reel_generator.db"
    reel_projects_dir = tmp_path / "reel_projects"
    monkeypatch.setattr(reel_db, "REEL_GENERATOR_DB_FILE", reel_db_file)
    monkeypatch.setattr(reel_storage, "REEL_GENERATOR_PROJECTS_DIR", reel_projects_dir)
    monkeypatch.setattr(orchestrator.reel_db, "REEL_GENERATOR_DB_FILE", reel_db_file)
    monkeypatch.setattr(orchestrator.reel_storage, "REEL_GENERATOR_PROJECTS_DIR", reel_projects_dir)

    design_db_file = tmp_path / "design_studio.db"
    design_projects_dir = tmp_path / "design_projects"
    monkeypatch.setattr(design_db, "DESIGN_STUDIO_DB_FILE", design_db_file)
    monkeypatch.setattr(design_storage, "DESIGN_STUDIO_PROJECTS_DIR", design_projects_dir)
    monkeypatch.setattr(orchestrator.design_db, "DESIGN_STUDIO_DB_FILE", design_db_file)
    monkeypatch.setattr(orchestrator.design_storage, "DESIGN_STUDIO_PROJECTS_DIR", design_projects_dir)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_child_views(root):
    """Destroys every child widget of the shared module-scoped `root`
    after each test - see tests/test_gui_reel_generator_dashboard.py's
    own identical fixture docstring for why: this file also constructs
    a fresh ContentStudioView (with several CTkOptionMenu dropdowns
    nested inside its own reel_generator/design_studio-style panels) in
    nearly every test without destroying the previous one, and Windows
    imposes a hard, finite cap on live Tcl menu handles per process."""
    yield
    for child in list(root.winfo_children()):
        child.destroy()


def _pump(root, predicate, *, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.02)


def _collect_texts(widget) -> list[str]:
    texts = []

    def _walk(w):
        try:
            t = w.cget("text")
            if t:
                texts.append(t)
        except Exception:
            pass
        for c in w.winfo_children():
            _walk(c)

    _walk(widget)
    return texts


def _fake_plan_data(topic: str = "5 minute morning yoga") -> dict[str, Any]:
    from jarvis.content_studio.plan import CONTENT_TYPES

    return {
        "topic": topic,
        "items": [
            {"content_type": ct, "angle": f"{ct} angle for {topic}", "objective": "educate", "cta": f"{ct} cta"}
            for ct in CONTENT_TYPES
        ],
    }


def _fake_plan(topic: str = "5 minute morning yoga"):
    from jarvis.content_studio.plan import ContentPlan, ContentPlanItem

    data = _fake_plan_data(topic)
    items = tuple(ContentPlanItem(**item) for item in data["items"])
    return ContentPlan(topic=topic, items=items)


# --- construction ------------------------------------------------------------------------


def test_builds_with_llm(root):
    view = ContentStudioView(root, llm=MagicMock())
    assert isinstance(view, ctk.CTkFrame)


def test_builds_without_llm(root):
    view = ContentStudioView(root, llm=None)
    assert isinstance(view, ctk.CTkFrame)


def test_refresh_does_not_raise(root):
    view = ContentStudioView(root, llm=MagicMock())
    view.refresh()


def test_shows_no_projects_message_when_empty(root):
    view = ContentStudioView(root, llm=MagicMock())
    texts = _collect_texts(view.my_projects_panel)
    assert any("No projects yet" in t for t in texts)


# --- New Content: create plan flow (mocked) -------------------------------------------------


def test_empty_topic_shows_error_without_calling_llm(root):
    llm = MagicMock()
    view = ContentStudioView(root, llm=llm)
    view.new_content_panel._on_create_clicked()
    texts = _collect_texts(view.new_content_panel._status_container)
    assert any("Describe your topic" in t for t in texts)
    llm.send.assert_not_called()


def test_no_llm_shows_clear_error(root):
    view = ContentStudioView(root, llm=None)
    view.new_content_panel._topic_entry.insert("1.0", "5 minute morning yoga")
    view.new_content_panel._on_create_clicked()
    texts = _collect_texts(view.new_content_panel._status_container)
    assert any("not available" in t for t in texts)


def _create_with_fake_plan(view, monkeypatch, topic="5 minute morning yoga"):
    view.new_content_panel._topic_entry.insert("1.0", topic)
    plan = _fake_plan(topic)
    with monkeypatch.context() as m:
        m.setattr(new_content_module, "generate_content_plan", MagicMock(return_value=plan))
        view.new_content_panel._on_create_clicked()
        _pump(view.winfo_toplevel(), lambda: view.new_content_panel._current_project is not None)
    return plan


def test_create_plan_click_renders_plan(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)

    texts = _collect_texts(view.new_content_panel._plan_container)
    assert any("AI CONTENT PLAN" in t for t in texts)
    assert any("Instagram Reel" in t for t in texts)
    assert any("PDF" in t for t in texts)

    project = view.new_content_panel._current_project
    assert project is not None
    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.plan_data is not None
    assert saved.topic == "5 minute morning yoga"


def test_create_plan_failure_shows_error(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    view.new_content_panel._topic_entry.insert("1.0", "some topic")
    with monkeypatch.context() as m:
        m.setattr(new_content_module, "generate_content_plan", MagicMock(return_value=None))
        view.new_content_panel._on_create_clicked()
        _pump(root, lambda: any(
            "couldn't create" in t.lower() for t in _collect_texts(view.new_content_panel._status_container)
        ))
    texts = _collect_texts(view.new_content_panel._status_container)
    assert any("couldn't create" in t.lower() for t in texts)


def test_create_plan_generation_exception_shows_error_not_crash(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    view.new_content_panel._topic_entry.insert("1.0", "some topic")
    with monkeypatch.context() as m:
        m.setattr(new_content_module, "generate_content_plan", MagicMock(side_effect=RuntimeError("network down")))
        view.new_content_panel._on_create_clicked()
        _pump(root, lambda: any(
            "network down" in t for t in _collect_texts(view.new_content_panel._status_container)
        ))
    texts = _collect_texts(view.new_content_panel._status_container)
    assert any("network down" in t for t in texts)


def test_create_pdf_still_shows_not_built_yet_message(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    view.new_content_panel._on_create_pdf_clicked()
    texts = _collect_texts(view.new_content_panel._status_container)
    assert any("coming in a later update" in t for t in texts)


# --- Stage 2: orchestration - "Create this Reel/Post/Story/Carousel" -----------------------


def _fake_reel_creation_result(project_id: str = "reel-gen-project-1"):
    from jarvis.reel_generator.brief import ReelBrief
    from jarvis.reel_generator.script import ReelScript, ScriptSegment

    brief = ReelBrief(
        topic="morning yoga", audience="beginners", objective="educate", tone="calm",
        cta="Try it", style="yoga", duration_seconds=20, language="lt",
    )
    script = ReelScript(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=5, text="hi"),
        ScriptSegment(kind="value", start_seconds=5, end_seconds=15, text="val"),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="save"),
    ))
    return orchestrator.ReelCreationResult(reel_generator_project_id=project_id, brief=brief, script=script)


def _fake_design_creation_result(project_id: str = "design-studio-project-1"):
    from jarvis.design_studio.render import RenderResult

    render_result = RenderResult(output_path=MagicMock(), width=1080, height=1350, file_size_bytes=12345)
    return orchestrator.DesignCreationResult(design_studio_project_id=project_id, render_result=render_result)


def _click_create_type(view, content_type: str):
    project = view.new_content_panel._current_project
    plan = view.new_content_panel._current_plan
    assert project is not None and plan is not None
    item = plan.item_for(content_type)
    assert item is not None
    view.new_content_panel._on_create_type_clicked(project, item)


def test_create_reel_shows_creating_then_created_state(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None

    fake_result = _fake_reel_creation_result()
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(return_value=fake_result))
        _click_create_type(view, "reel")
        assert "reel" in view.new_content_panel._creating_types  # immediate "creating" state, before the bg call resolves
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.status_for("reel") == "created"
    assert saved.reel_generator_project_id == "reel-gen-project-1"

    texts = _collect_texts(view.new_content_panel._plan_container)
    assert any("CREATED" in t for t in texts)


def test_create_reel_end_to_end_creates_real_linked_project(root, monkeypatch):
    # Unlike the test above (which mocks orchestrator.create_reel_project
    # itself), this one mocks only the LLM calls one level deeper, so
    # the orchestrator's own real storage.create_project()/
    # db.create_project_record() calls run for real - proving Stage 2
    # actually creates a genuine, independently-readable Reel Generator
    # project, not just a mocked success.
    from jarvis.reel_generator import brief as reel_brief_module
    from jarvis.reel_generator import script as reel_script_module

    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None

    fake_brief = _fake_reel_creation_result().brief
    fake_script = _fake_reel_creation_result().script
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "generate_reel_brief", MagicMock(return_value=fake_brief))
        m.setattr(orchestrator, "generate_reel_script", MagicMock(return_value=fake_script))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.status_for("reel") == "created"
    linked_id = saved.reel_generator_project_id
    assert linked_id is not None

    linked = reel_db.get_project(linked_id)
    assert linked is not None
    assert linked.brief_data is not None
    assert linked.script_data is not None


def test_create_post_end_to_end_creates_real_rendered_design(root, monkeypatch):
    from jarvis.design_studio.brief import DesignBrief

    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None

    fake_brief = DesignBrief(
        topic="morning yoga", objective="educate", audience="beginners", tone="calm",
        headline="5 Min Yoga", supporting_text="Start your day right.", cta="Save this",
        format="post", style="yoga",
    )
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "generate_design_brief", MagicMock(return_value=fake_brief))
        _click_create_type(view, "post")
        _pump(view.winfo_toplevel(), lambda: "post" not in view.new_content_panel._creating_types)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.status_for("post") == "created"
    linked_id = saved.post_design_project_id
    assert linked_id is not None

    linked = design_db.get_project(linked_id)
    assert linked is not None
    assert linked.design_path is not None
    from pathlib import Path

    assert Path(linked.design_path).is_file()


def test_create_reel_failure_shows_real_error_with_retry(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None

    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(return_value="AI Reel Generator failed: network error"))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.status_for("reel") == "failed"
    assert saved.error_for("reel") == "AI Reel Generator failed: network error"

    texts = _collect_texts(view.new_content_panel._plan_container)
    assert any("network error" in t for t in texts)
    assert any("Retry" in t for t in texts)

    status_texts = _collect_texts(view.new_content_panel._status_container)
    assert any("network error" in t for t in status_texts)


def test_retry_after_failure_can_succeed(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None

    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(return_value="first attempt failed"))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)
    assert db.get_project(project.project_id).status_for("reel") == "failed"  # type: ignore[union-attr]

    fake_result = _fake_reel_creation_result()
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(return_value=fake_result))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.status_for("reel") == "created"
    assert saved.error_for("reel") is None  # the earlier failure's error is cleared
    assert saved.reel_generator_project_id == "reel-gen-project-1"


def test_create_type_exception_shows_error_not_crash(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None

    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(side_effect=RuntimeError("boom")))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.status_for("reel") == "failed"
    texts = _collect_texts(view.new_content_panel._status_container)
    assert any("boom" in t for t in texts)


def test_create_type_no_llm_shows_error(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    plan = view.new_content_panel._current_plan
    assert project is not None and plan is not None
    item = plan.item_for("reel")
    assert item is not None

    view.new_content_panel._llm = None
    view.new_content_panel._on_create_type_clicked(project, item)
    texts = _collect_texts(view.new_content_panel._status_container)
    assert any("not available" in t for t in texts)


def test_created_reel_shows_preview_and_regenerate_actions(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    fake_result = _fake_reel_creation_result()
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(return_value=fake_result))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    texts = _collect_texts(view.new_content_panel._plan_container)
    assert any("Preview" in t for t in texts)
    assert any("Regenerate" in t for t in texts)


def test_preview_continue_navigates_to_reel_generator_with_linked_project_id(root, monkeypatch):
    # Regression test for the reported handoff bug: clicking "Preview /
    # Continue" on a created Reel must navigate to AI Reel Generator
    # AND tell it exactly which project to open (via
    # jarvis.gui.app._navigate()'s own open_project_id keyword) so the
    # existing Reel Generator opens with the Content-Studio-generated
    # brief/script already populated, instead of an empty "What Reel do
    # you want to create?" form. Calls the button's own command
    # (_navigate_to) directly rather than walking the widget tree to
    # find and click the real button - _render_item_row()'s own button
    # wiring (tested separately by
    # test_created_reel_shows_preview_and_regenerate_actions, which
    # confirms the button text is actually rendered) is what connects
    # the real click to this same call.
    navigate_mock = MagicMock()
    view = ContentStudioView(root, llm=MagicMock(), navigate=navigate_mock)
    _create_with_fake_plan(view, monkeypatch)
    fake_result = _fake_reel_creation_result(project_id="reel-gen-project-xyz")
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(return_value=fake_result))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    view.new_content_panel._navigate_to("reel_generator", "reel-gen-project-xyz")
    navigate_mock.assert_called_once_with("reel_generator", open_project_id="reel-gen-project-xyz")


def test_preview_continue_navigates_to_design_studio_with_linked_project_id(root, monkeypatch):
    from jarvis.design_studio.render import RenderResult

    navigate_mock = MagicMock()
    view = ContentStudioView(root, llm=MagicMock(), navigate=navigate_mock)
    _create_with_fake_plan(view, monkeypatch)
    fake_result = orchestrator.DesignCreationResult(
        design_studio_project_id="design-project-xyz",
        render_result=RenderResult(output_path=MagicMock(), width=1080, height=1350, file_size_bytes=1),
    )
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_design_project", MagicMock(return_value=fake_result))
        _click_create_type(view, "post")
        _pump(view.winfo_toplevel(), lambda: "post" not in view.new_content_panel._creating_types)

    view.new_content_panel._navigate_to("design_studio", "design-project-xyz")
    navigate_mock.assert_called_once_with("design_studio", open_project_id="design-project-xyz")


def test_created_project_status_survives_reopening_from_my_projects(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None
    fake_result = _fake_reel_creation_result()
    with monkeypatch.context() as m:
        m.setattr(orchestrator, "create_reel_project", MagicMock(return_value=fake_result))
        _click_create_type(view, "reel")
        _pump(view.winfo_toplevel(), lambda: "reel" not in view.new_content_panel._creating_types)

    view.my_projects_panel.refresh()
    texts = _collect_texts(view.my_projects_panel)
    assert any("Reel: Created" in t for t in texts)


# --- New project appears in My Projects ------------------------------------------------------


def test_created_project_appears_in_my_projects(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    texts = _collect_texts(view.my_projects_panel)
    assert not any("No projects yet" in t for t in texts)
    assert any("5 minute morning yoga" in t for t in texts)


def test_my_projects_shows_all_planned_badge_for_new_project(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    texts = _collect_texts(view.my_projects_panel)
    assert any("All planned" in t for t in texts)


def test_my_projects_shows_status_badge_after_approval(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None
    db.set_content_type_status(project.project_id, "reel", "approved")
    view.my_projects_panel.refresh()
    texts = _collect_texts(view.my_projects_panel)
    assert any("Reel: Approved" in t for t in texts)


def test_open_project_shows_saved_plan_details(root, monkeypatch):
    view = ContentStudioView(root, llm=MagicMock())
    _create_with_fake_plan(view, monkeypatch)
    project = view.new_content_panel._current_project
    assert project is not None

    view.my_projects_panel._open_project(project.project_id)
    texts = _collect_texts(view.my_projects_panel._detail_container)
    assert any("5 minute morning yoga" in t for t in texts)
    assert any("reel angle for 5 minute morning yoga" in t for t in texts)


def test_open_nonexistent_project_shows_error(root):
    view = ContentStudioView(root, llm=MagicMock())
    view.my_projects_panel._open_project("does-not-exist")
    texts = _collect_texts(view.my_projects_panel._detail_container)
    assert any("could no longer be found" in t for t in texts)
