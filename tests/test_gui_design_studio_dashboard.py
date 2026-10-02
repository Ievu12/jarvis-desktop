"""Tests for jarvis.gui.views.design_studio.dashboard.DesignStudioView:
widget wiring and rendering, using a real (withdrawn) CTk root with
jarvis.design_studio.db/.storage redirected to a per-test tmp_path.
jarvis.design_studio.brief.generate_design_brief and
jarvis.design_studio.variants.generate_variants are mocked for these
tests (no real LLM/Pillow-rendering call) - the real, unmocked pipeline
(prompt -> real Claude-generated DesignBrief -> real Brand-Kit-colored
3-variant Pillow render -> "Use this" -> real Export with the
no-overwrite protection, and real "Duplicate" creating a genuinely new
project) is covered end-to-end by manual testing during development.

Confirms: button/status states, routing GenerationTaskResults by
(kind, self), the (project, brief, variants)-tuple-vs-error-string
unpacking safety (module brief's "Never overwrite an existing design
automatically" bug class already found and fixed once in
jarvis.gui.views.video_studio.dashboard - checked before unpacking
here too), the variant picker renders 3 variants with Use this/
Duplicate/Edit per variant, "Use this" selects that variant as the
project's current design, Export never overwrites a previous export,
and reopening a saved design from Recent Designs shows the previously
selected design (not the variant picker) without re-rendering."""

from __future__ import annotations

import time
from typing import Literal
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest
from PIL import Image

from jarvis.design_studio import db, storage
from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.render import RenderResult
from jarvis.design_studio.variants import DesignVariant
from jarvis.gui.views.design_studio import dashboard as dashboard_module
from jarvis.gui.views.design_studio.dashboard import DesignStudioView


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "design_studio.db"
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(db, "DESIGN_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(storage, "DESIGN_STUDIO_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(dashboard_module.db, "DESIGN_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(dashboard_module.storage, "DESIGN_STUDIO_PROJECTS_DIR", projects_dir)
    # Brand Kit lives in the same DB file (see jarvis.design_studio
    # .brand_kit's own docstring) - redirect it too so
    # get_brand_kit()/apply_brand_kit() (called unconditionally by
    # _create_design(), even with an unconfigured/empty kit) never
    # touch the real .jarvis/design_studio.db.
    import jarvis.design_studio.brand_kit as brand_kit_module

    monkeypatch.setattr(brand_kit_module, "DESIGN_STUDIO_DB_FILE", db_file)


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
    a fresh DesignStudioView (with several CTkOptionMenu dropdowns) in
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


def _find_buttons(widget) -> list[ctk.CTkButton]:
    buttons = []

    def _walk(w):
        if isinstance(w, ctk.CTkButton):
            buttons.append(w)
        for c in w.winfo_children():
            _walk(c)

    _walk(widget)
    return buttons


def _fake_brief(**overrides) -> DesignBrief:
    defaults = dict(
        topic="yoga", objective="educate", audience="wellness beginners", tone="calm",
        headline="3 Poses To Start Your Day", supporting_text="Stretch and breathe.",
        cta="Save this", format="story", style="yoga",
    )
    defaults.update(overrides)
    return DesignBrief(**defaults)


def _fake_render_result(tmp_path, name="design.jpg") -> RenderResult:
    path = tmp_path / name
    Image.new("RGB", (1080, 1920), (200, 200, 200)).save(path)
    return RenderResult(output_path=path, width=1080, height=1920, file_size_bytes=path.stat().st_size)


def _fake_variants(tmp_path) -> list[DesignVariant]:
    labels: tuple[Literal["A", "B", "C"], ...] = ("A", "B", "C")
    styles = ("minimal", "elegant", "bold")
    return [
        DesignVariant(
            label=label, style=style,
            render_result=_fake_render_result(tmp_path, f"variant_{label.lower()}.jpg"), error=None,
        )
        for label, style in zip(labels, styles)
    ]


# --- construction ------------------------------------------------------------------------


def test_builds_with_llm(root):
    view = DesignStudioView(root, llm=MagicMock())
    assert isinstance(view, ctk.CTkFrame)


def test_builds_without_llm(root):
    view = DesignStudioView(root, llm=None)
    assert isinstance(view, ctk.CTkFrame)


def test_refresh_does_not_raise(root):
    view = DesignStudioView(root, llm=MagicMock())
    view.refresh()


def test_shows_no_designs_message_when_empty(root):
    view = DesignStudioView(root, llm=MagicMock())
    texts = _collect_texts(view._recent_designs_container)
    assert any("No designs yet" in t for t in texts)


def test_has_brand_kit_panel(root):
    view = DesignStudioView(root, llm=MagicMock())
    assert view._brand_kit_panel is not None


# --- create design flow (mocked) ----------------------------------------------------------


def test_empty_prompt_shows_error_without_calling_llm(root):
    llm = MagicMock()
    view = DesignStudioView(root, llm=llm)
    view._on_create_clicked()
    texts = _collect_texts(view._status_container)
    assert any("Describe what you want" in t for t in texts)
    llm.send.assert_not_called()


def test_no_llm_shows_clear_error(root, tmp_path):
    view = DesignStudioView(root, llm=None)
    view._prompt_entry.insert("1.0", "Create a Story about yoga.")
    view._on_create_clicked()
    texts = _collect_texts(view._status_container)
    assert any("not available" in t for t in texts)


def test_create_design_click_renders_three_variants(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "Create a Story about morning yoga.")

    brief = _fake_brief()
    variants = _fake_variants(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_design_brief", MagicMock(return_value=brief))
        m.setattr(dashboard_module, "generate_variants", MagicMock(return_value=variants))
        view._on_create_clicked()
        _pump(root, lambda: view._current_project is not None)

    assert view._current_project is not None
    texts = _collect_texts(view._variants_container)
    assert any("VARIANT A" in t for t in texts)
    assert any("VARIANT B" in t for t in texts)
    assert any("VARIANT C" in t for t in texts)

    saved = db.get_project(view._current_project.project_id)
    assert saved is not None
    assert saved.brief_data is not None
    assert saved.brief_data["headline"] == "3 Poses To Start Your Day"


def test_create_design_brief_failure_shows_error(root, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some request")

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_design_brief", MagicMock(return_value=None))
        view._on_create_clicked()
        _pump(root, lambda: any("couldn't create" in t.lower() for t in _collect_texts(view._status_container)))

    texts = _collect_texts(view._status_container)
    assert any("couldn't create" in t.lower() for t in texts)


def test_one_variant_failure_still_shows_the_other_two(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some request")
    brief = _fake_brief()
    variants = _fake_variants(tmp_path)
    failed_variant = DesignVariant(label="B", style="elegant", render_result=None, error="render boom")
    variants[1] = failed_variant

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_design_brief", MagicMock(return_value=brief))
        m.setattr(dashboard_module, "generate_variants", MagicMock(return_value=variants))
        view._on_create_clicked()
        _pump(root, lambda: view._current_project is not None)

    texts = _collect_texts(view._variants_container)
    assert any("render boom" in t for t in texts)
    assert any("VARIANT A" in t for t in texts)
    assert any("VARIANT C" in t for t in texts)


def test_create_design_generation_exception_shows_error_not_crash(root, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some request")

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_design_brief", MagicMock(side_effect=RuntimeError("network down")))
        view._on_create_clicked()
        _pump(root, lambda: any("network down" in t for t in _collect_texts(view._status_container)))

    texts = _collect_texts(view._status_container)
    assert any("network down" in t for t in texts)


# --- variant picker actions ----------------------------------------------------------------


def _create_with_fake_variants(view, tmp_path, monkeypatch):
    view._prompt_entry.insert("1.0", "Create a Story about yoga.")
    brief = _fake_brief()
    variants = _fake_variants(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_design_brief", MagicMock(return_value=brief))
        m.setattr(dashboard_module, "generate_variants", MagicMock(return_value=variants))
        view._on_create_clicked()
        _pump(view.winfo_toplevel(), lambda: view._current_project is not None)
    return brief, variants


def test_use_this_selects_variant_as_current_design(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    brief, variants = _create_with_fake_variants(view, tmp_path, monkeypatch)
    project = view._current_project
    assert project is not None

    render_result = variants[0].render_result
    assert render_result is not None
    view._on_use_variant_clicked(project, brief, variants[0])
    texts = _collect_texts(view._design_container)
    assert any("Story" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.status == "rendered"
    assert saved.design_path == str(render_result.output_path)


def test_use_this_clears_the_variant_picker(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    brief, variants = _create_with_fake_variants(view, tmp_path, monkeypatch)
    project = view._current_project
    assert project is not None

    assert len(view._variants_container.winfo_children()) > 0
    view._on_use_variant_clicked(project, brief, variants[0])
    assert len(view._variants_container.winfo_children()) == 0


def test_duplicate_creates_a_genuinely_new_project(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    _, variants = _create_with_fake_variants(view, tmp_path, monkeypatch)
    project = view._current_project
    assert project is not None

    before_count = len(db.list_projects())
    view._on_duplicate_variant_clicked(project, variants[0])
    after_count = len(db.list_projects())
    assert after_count == before_count + 1


def test_edit_button_shows_not_yet_available_message(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    _create_with_fake_variants(view, tmp_path, monkeypatch)
    view._on_edit_clicked()
    texts = _collect_texts(view._status_container)
    assert any("coming in a later update" in t for t in texts)


def test_regenerate_all_calls_create_again(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "Create a Story about yoga.")
    brief = _fake_brief()
    variants = _fake_variants(tmp_path)
    generate_variants_mock = MagicMock(return_value=variants)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_design_brief", MagicMock(return_value=brief))
        m.setattr(dashboard_module, "generate_variants", generate_variants_mock)
        view._on_create_clicked()
        _pump(root, lambda: view._current_project is not None)
        calls_before = generate_variants_mock.call_count
        view._on_regenerate_clicked()
        _pump(root, lambda: generate_variants_mock.call_count > calls_before)

    assert generate_variants_mock.call_count > calls_before


# --- export ----------------------------------------------------------------------------


def test_export_copies_file_and_never_overwrites(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    brief, variants = _create_with_fake_variants(view, tmp_path, monkeypatch)
    project = view._current_project
    assert project is not None
    render_result = variants[0].render_result
    assert render_result is not None
    view._on_use_variant_clicked(project, brief, variants[0])

    view._on_export_clicked(project, render_result)
    exports = list(project.exports_dir.iterdir())
    assert len(exports) == 1
    original_export = exports[0]

    # A second export must NOT overwrite the first (module brief:
    # "Never overwrite an existing design automatically.")
    view._on_export_clicked(project, render_result)
    exports_after = list(project.exports_dir.iterdir())
    assert len(exports_after) == 2
    assert original_export.is_file()  # untouched


# --- reopening from Recent Designs --------------------------------------------------------


def test_open_design_shows_saved_design_without_rerendering(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    brief, variants = _create_with_fake_variants(view, tmp_path, monkeypatch)
    project = view._current_project
    assert project is not None
    view._on_use_variant_clicked(project, brief, variants[0])
    project_id = project.project_id

    view2 = DesignStudioView(root, llm=MagicMock())
    generate_mock = MagicMock(side_effect=AssertionError("should not re-render"))
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_variants", generate_mock)
        view2._open_design(project_id)
        root.update()

    texts = _collect_texts(view2._design_container)
    assert any("Story" in t for t in texts)


def test_open_nonexistent_design_shows_error(root):
    view = DesignStudioView(root, llm=MagicMock())
    view._open_design("does-not-exist")
    root.update()
    texts = _collect_texts(view._status_container)
    assert any("could no longer be found" in t for t in texts)


def test_open_project_public_wrapper_delegates_to_open_design(root):
    # Regression test for the Content Studio -> Design Studio handoff:
    # a PUBLIC open_project() entry point (called by
    # jarvis.gui.app._navigate()'s own open_project_id mechanism, on
    # behalf of Content Studio's "Preview / Continue" action) must
    # delegate to _open_design() - already covered end-to-end by
    # test_open_design_shows_saved_design_without_rerendering above
    # (real saved design loading). Checked via a mock instead of
    # constructing a second full DesignStudioView, matching
    # tests/test_gui_reel_generator_dashboard.py's own identical
    # reasoning (this codebase has hit real Windows Tcl resource limits
    # from too many GUI widgets accumulating across many views sharing
    # one Tk root in a single test file).
    view = DesignStudioView(root, llm=MagicMock())
    with patch.object(view, "_open_design") as mock_open_design:
        view.open_project("some-project-id")
    mock_open_design.assert_called_once_with("some-project-id")


def test_created_design_appears_in_recent_designs(root, tmp_path, monkeypatch):
    view = DesignStudioView(root, llm=MagicMock())
    _create_with_fake_variants(view, tmp_path, monkeypatch)
    texts = _collect_texts(view._recent_designs_container)
    assert not any("No designs yet" in t for t in texts)
