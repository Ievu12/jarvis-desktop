"""Tests for jarvis.gui.views.reel_generator.dashboard.ReelGeneratorView:
widget wiring and rendering, using a real (withdrawn) CTk root with
jarvis.reel_generator.db/.storage redirected to a per-test tmp_path.
jarvis.reel_generator.brief.generate_reel_brief and
jarvis.reel_generator.script.generate_reel_script are mocked for these
tests (no real LLM call) - same pattern as
tests/test_gui_design_studio_dashboard.py (see that file's own
docstring for the full rationale).

Confirms: button/status states, routing GenerationTaskResults by
(kind, self), the (project, brief, script)-tuple-vs-error-string
unpacking safety, brief + script are rendered after creation, the
Approve button persists approval to the DB and shows the approved
state, Regenerate produces a fresh (unapproved) script, and reopening
a saved Reel from Recent Reels restores its brief/script/approval
state without calling the LLM again."""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from jarvis.reel_generator import caption as caption_mod
from jarvis.reel_generator import db, storage
from jarvis.reel_generator import project_status as project_status_mod
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript, ScriptSegment
from jarvis.reel_generator.storyboard import Scene, Storyboard
from jarvis.gui.views.reel_generator import dashboard as dashboard_module
from jarvis.gui.views.reel_generator.dashboard import ReelGeneratorView
from jarvis.video_studio import db as video_db
from jarvis.video_studio import storage as video_storage
from jarvis.video_studio.reel import PlannedClip, ReelEditPlan


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "reel_generator.db"
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(dashboard_module.db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(dashboard_module.storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)

    video_db_file = tmp_path / "video_studio.db"
    video_projects_dir = tmp_path / "video_projects"
    monkeypatch.setattr(video_db, "VIDEO_STUDIO_DB_FILE", video_db_file)
    monkeypatch.setattr(video_storage, "VIDEO_STUDIO_PROJECTS_DIR", video_projects_dir)
    monkeypatch.setattr(dashboard_module.footage.video_db, "VIDEO_STUDIO_DB_FILE", video_db_file)
    monkeypatch.setattr(dashboard_module.footage.video_storage, "VIDEO_STUDIO_PROJECTS_DIR", video_projects_dir)


@pytest.fixture(autouse=True)
def _default_visual_plan_generation_disabled(monkeypatch):
    """GENERATE SCENE VISUALS now implicitly runs the Smart Visual
    Director first whenever no visual plan is loaded yet and an LLM is
    available (see _do_generate_visuals()'s own docstring - the fix for
    the reported "still shows a plain text card, not real images" bug).
    Every test in this file that predates that fix uses `llm=MagicMock()`
    for reasons unrelated to visual planning (cover/caption/export/etc.
    tests that only need SOME rendered visuals as a precondition) and
    was written against the OLD "no visual_plan -> plain text card"
    behavior - a bare MagicMock() LLM would otherwise make
    generate_visual_plan()'s real retry-and-log-warnings logic run for
    real on every one of those clicks (slow, and eventually fails,
    turning a previously-successful precondition into an error).

    Defaulting generate_visual_plan() to return None here (matching
    exactly what "no plan" already meant before this fix, and never
    raising or making any network call) keeps every pre-existing test's
    behavior unchanged by default. A test that DOES want to exercise the
    real auto-plan success path overrides this within its own
    `monkeypatch.context()` (see
    test_generate_scene_visuals_renders_real_images() and the Smart
    Visual Director tests below), the same way those tests already
    override jarvis.reel_generator.storyboard.generate_visual_plan for
    the SMART VISUAL DIRECTOR button itself."""
    monkeypatch.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=None))


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_child_views(root):
    """Destroys every child widget of the shared module-scoped `root`
    after each test - this file constructs a fresh ReelGeneratorView
    (each with several CTkOptionMenu dropdowns) in nearly every test
    without ever destroying the previous one, and Windows imposes a
    hard, finite cap on live Tcl menu handles per process ("No more
    menus can be allocated" - a real, reproducible failure this file
    was found to hit near its own end once the tests here accumulated
    past a certain count, not test-logic flakiness). Destroying each
    test's own view(s) before the next test builds new ones keeps the
    live widget count roughly constant across the whole file instead of
    growing unboundedly with every additional test.

    Each ReelGeneratorView schedules its own recurring self.after(...)
    queue-poll loop (see its own _poll_queue()) - destroying a view
    while one of its background-thread calls is still in flight (a real
    possibility here, since several tests intentionally don't wait for
    completion) can otherwise leave a stale callback that fires against
    an already-destroyed widget on a LATER test's own root.update() -
    pumping a few idle update() cycles first lets any in-flight
    background result drain and its next after() callback fire and
    reschedule against a still-alive widget before destruction, instead
    of racing it."""
    yield
    for _ in range(5):
        root.update()
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass


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


def _fake_brief(**overrides: Any) -> ReelBrief:
    defaults: dict[str, Any] = dict(
        topic="morning yoga", audience="wellness beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=20, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _fake_script(**overrides) -> ReelScript:
    segments = overrides.pop("segments", None) or (
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=5, text="Three ways to start your day."),
        ScriptSegment(kind="value", start_seconds=5, end_seconds=15, text="Stretch. Breathe. Move."),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="Save this Reel."),
    )
    return ReelScript(segments=segments)


def _fake_storyboard() -> Storyboard:
    return Storyboard(scenes=(
        Scene(number=1, start_seconds=0, end_seconds=5, segment_kind="hook", voice_text="Three ways to start your day.", on_screen_text="3 ways to start", visual_description="morning light"),
        Scene(number=2, start_seconds=5, end_seconds=15, segment_kind="value", voice_text="Stretch. Breathe. Move.", on_screen_text="Stretch, breathe, move", visual_description="stretching"),
        Scene(number=3, start_seconds=15, end_seconds=20, segment_kind="cta", voice_text="Save this Reel.", on_screen_text="Save this!", visual_description="calm ending"),
    ))


# --- construction ------------------------------------------------------------------------


def test_builds_with_llm(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    assert isinstance(view, ctk.CTkFrame)


def test_builds_without_llm(root):
    view = ReelGeneratorView(root, llm=None)
    assert isinstance(view, ctk.CTkFrame)


def test_refresh_does_not_raise(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view.refresh()


def test_shows_no_reels_message_when_empty(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    texts = _collect_texts(view._recent_reels_container)
    assert any("No Reels yet" in t for t in texts)


# --- create concept flow (mocked) ----------------------------------------------------------


def test_empty_prompt_shows_error_without_calling_llm(root):
    llm = MagicMock()
    view = ReelGeneratorView(root, llm=llm)
    view._on_create_clicked()
    texts = _collect_texts(view._status_container)
    assert any("Describe the Reel" in t for t in texts)
    llm.send.assert_not_called()


def test_no_llm_shows_clear_error(root):
    view = ReelGeneratorView(root, llm=None)
    view._prompt_entry.insert("1.0", "Create a Reel about yoga.")
    view._on_create_clicked()
    texts = _collect_texts(view._status_container)
    assert any("not available" in t for t in texts)


def _create_with_fakes(view, monkeypatch, *, brief=None, script=None):
    view._prompt_entry.insert("1.0", "Create a 20 second Reel about morning yoga.")
    brief = brief or _fake_brief()
    script = script or _fake_script()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_reel_brief", MagicMock(return_value=brief))
        m.setattr(dashboard_module, "generate_reel_script", MagicMock(return_value=script))
        view._on_create_clicked()
        # A generous timeout (not the 5s default) even though
        # generate_reel_brief/.generate_reel_script are mocked and
        # return instantly - this helper is called by tests later in
        # this file that run real ffmpeg encodes and construct many
        # views against one shared root, and under that combined load
        # the daemon thread's queue.put() -> root.after() poll -> this
        # test's own root.update() pump loop has occasionally been
        # observed to take longer than 5s to observe
        # view._current_project becoming non-None, not because the
        # work itself is slow.
        _pump(view.winfo_toplevel(), lambda: view._current_project is not None, timeout=45.0)
    return brief, script


def test_create_click_renders_brief_and_script(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)

    brief_texts = _collect_texts(view._brief_container)
    assert any("REEL BRIEF" in t for t in brief_texts)
    assert any("morning yoga" in t for t in brief_texts)

    script_texts = _collect_texts(view._script_container)
    assert any("SCRIPT PREVIEW" in t for t in script_texts)
    assert any("HOOK" in t for t in script_texts)
    assert any("VALUE" in t for t in script_texts)
    assert any("CTA" in t for t in script_texts)
    assert not any("Approved" in t for t in script_texts)  # not yet approved

    assert view._current_project is not None
    saved = db.get_project(view._current_project.project_id)
    assert saved is not None
    assert saved.brief_data is not None
    assert saved.script_data is not None
    assert saved.script_approved is False


def test_create_brief_failure_shows_error(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some idea")
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_reel_brief", MagicMock(return_value=None))
        view._on_create_clicked()
        _pump(root, lambda: any("couldn't create" in t.lower() for t in _collect_texts(view._status_container)))
    texts = _collect_texts(view._status_container)
    assert any("couldn't create" in t.lower() for t in texts)


def test_create_script_failure_shows_error(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some idea")
    brief = _fake_brief()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_reel_brief", MagicMock(return_value=brief))
        m.setattr(dashboard_module, "generate_reel_script", MagicMock(return_value=None))
        view._on_create_clicked()
        _pump(root, lambda: any("script" in t.lower() for t in _collect_texts(view._status_container)))
    texts = _collect_texts(view._status_container)
    assert any("script" in t.lower() for t in texts)


def test_create_generation_exception_shows_error_not_crash(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some idea")
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_reel_brief", MagicMock(side_effect=RuntimeError("network down")))
        view._on_create_clicked()
        _pump(root, lambda: any("network down" in t for t in _collect_texts(view._status_container)))
    texts = _collect_texts(view._status_container)
    assert any("network down" in t for t in texts)


# --- approval gate -----------------------------------------------------------------------


def test_approve_button_persists_approval_and_triggers_storyboard(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None

    storyboard = _fake_storyboard()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_storyboard", MagicMock(return_value=storyboard))
        view._on_approve_clicked(project)
        _pump(root, lambda: view._current_storyboard is not None)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.script_approved is True

    texts = _collect_texts(view._script_container)
    assert any("Approved" in t for t in texts)

    storyboard_texts = _collect_texts(view._storyboard_container)
    assert any("VISUAL STORYBOARD" in t for t in storyboard_texts)
    assert any("SCENE 1" in t for t in storyboard_texts)


def test_video_is_never_generated_before_approval(root, monkeypatch):
    # Storyboard generation (a prerequisite for scene visuals and the
    # final export) is only ever reachable through
    # _on_generate_storyboard_clicked(), which is itself only ever
    # called from _on_approve_clicked() - there is no path in this view
    # that reaches storyboard/visual/export generation without going
    # through db.approve_script() first (module brief section 4's
    # gate, enforced structurally, not just by a UI hint).
    view = ReelGeneratorView(root, llm=MagicMock())
    assert view._current_storyboard is None
    view._on_export_clicked()  # must be a safe no-op with nothing approved/generated yet
    assert view._current_storyboard is None


def test_regenerate_produces_new_unapproved_script(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    view._on_approve_clicked(project)
    assert db.get_project(project.project_id).script_approved is True  # type: ignore[union-attr]

    new_script = _fake_script(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=5, text="A brand new hook."),
        ScriptSegment(kind="value", start_seconds=5, end_seconds=15, text="A brand new value section."),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="A brand new CTA."),
    ))
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_reel_script", MagicMock(return_value=new_script))
        view._on_regenerate_script_clicked()
        _pump(root, lambda: any("brand new hook" in t for t in _collect_texts(view._script_container)))

    texts = _collect_texts(view._script_container)
    assert any("brand new hook" in t for t in texts)
    assert not any("Approved" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.script_approved is False


def test_edit_script_shows_not_yet_available_message(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    view._on_edit_script_clicked()
    texts = _collect_texts(view._status_container)
    assert any("coming in a later update" in t for t in texts)


# --- reopening from Recent Reels --------------------------------------------------------


def test_open_reel_restores_brief_script_and_approval(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    view._on_approve_clicked(project)
    project_id = project.project_id

    view2 = ReelGeneratorView(root, llm=MagicMock())
    generate_mock = MagicMock(side_effect=AssertionError("should not regenerate"))
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_reel_brief", generate_mock)
        m.setattr(dashboard_module, "generate_reel_script", generate_mock)
        view2._open_reel(project_id)
        root.update()

    brief_texts = _collect_texts(view2._brief_container)
    assert any("morning yoga" in t for t in brief_texts)
    script_texts = _collect_texts(view2._script_container)
    assert any("Approved" in t for t in script_texts)


def test_open_nonexistent_reel_shows_error(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._open_reel("does-not-exist")
    root.update()
    texts = _collect_texts(view._status_container)
    assert any("could no longer be found" in t for t in texts)


def test_open_project_public_wrapper_delegates_to_open_reel(root):
    # Regression test for the Content Studio -> Reel Generator handoff
    # bug: Content Studio's own "Preview / Continue" action needs a
    # PUBLIC entry point (jarvis.gui.app._navigate()'s own
    # open_project_id mechanism calls view.open_project(project_id))
    # rather than reaching into this view's private _open_reel().
    # open_project() must be a genuine, unmodified delegation to
    # _open_reel() (already covered end-to-end by
    # test_open_reel_restores_brief_script_and_approval above - real
    # brief/script loading, Lithuanian content, approval state) - this
    # test checks the delegation itself directly (via a mock) instead
    # of constructing a second full ReelGeneratorView, since this test
    # file already runs 40+ views against one shared Tk root and this
    # codebase has hit real Windows Tcl resource limits ("No more menus
    # can be allocated") from too many CTkOptionMenu widgets
    # accumulating in one process - see this file's own `root` fixture
    # for the existing module-scoped-root convention this respects.
    view = ReelGeneratorView(root, llm=MagicMock())
    with patch.object(view, "_open_reel") as mock_open_reel:
        view.open_project("some-project-id")
    mock_open_reel.assert_called_once_with("some-project-id")


def test_created_reel_appears_in_recent_reels(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    texts = _collect_texts(view._recent_reels_container)
    assert not any("No Reels yet" in t for t in texts)


# --- storyboard / scene visuals / cover / caption / export (Stage 2) ----------------------


def _approve_with_fake_storyboard(view, project, monkeypatch):
    storyboard = _fake_storyboard()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_storyboard", MagicMock(return_value=storyboard))
        view._on_approve_clicked(project)
        _pump(view.winfo_toplevel(), lambda: view._current_storyboard is not None, timeout=45.0)
    return storyboard


def test_generate_scene_visuals_renders_real_images(root, monkeypatch, tmp_path):
    # GENERATE SCENE VISUALS with an available LLM and no visual plan
    # loaded yet now implicitly runs the Smart Visual Director first
    # (see _do_generate_visuals()'s own docstring for the full root-
    # cause fix this covers) - mocking generate_visual_plan() here is
    # the same convention the "Smart Visual Director" tests below use
    # for view._on_generate_visual_plan_clicked(), applied to this
    # button instead, since a bare MagicMock() LLM can't itself produce
    # a usable plan.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        # _fake_visual_plan()'s scenes use visual_source="b_roll" (a real
        # AI-generation-eligible source - see
        # jarvis.reel_generator.scene_render._REAL_IMAGE_VISUAL_SOURCES),
        # so without this mock a machine with a real OPENAI_API_KEY
        # configured (image_generation.is_configured() reads it directly
        # from the environment, with no test-time override anywhere in
        # this codebase) would make a genuine, slow network call here -
        # forcing is_configured() to False keeps this test hermetic
        # regardless of the local environment, matching this test file's
        # own "no live network calls" convention.
        from jarvis.reel_generator import scene_render as scene_render_mod

        m.setattr(scene_render_mod.image_generation, "is_configured", MagicMock(return_value=False))
        view._on_generate_visuals_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    assert len(view._current_visuals) == 3
    assert all(v.error is None for v in view._current_visuals)
    assert all(v.image_path is not None and v.image_path.is_file() for v in view._current_visuals)
    # The auto-generated plan is stored exactly as if SMART VISUAL
    # DIRECTOR had been clicked directly, so RESET SCENE/per-scene
    # regenerate/persistence-across-restart all keep working afterwards.
    assert view._current_visual_plan is visual_plan
    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.visual_plan_data is not None


def test_generate_scene_visuals_without_llm_falls_back_to_text_cards(root, monkeypatch):
    # Regression coverage for the pre-existing, still-valid fallback:
    # with NO LLM configured at all, there is no Smart Visual Director
    # path to auto-run, so this must keep behaving exactly as before -
    # the original, unenriched text-card render - rather than erroring.
    # Built with a real LLM through creation/storyboard (both of THOSE
    # steps already require self._llm - see _on_create_clicked()'s/
    # _on_generate_storyboard_clicked()'s own "JARVIS AI is not
    # available" guards - so llm=None can't reach this point via the
    # normal flow at all); self._llm is cleared only afterwards, right
    # before the GENERATE SCENE VISUALS click this test actually covers,
    # to isolate that one no-LLM code path deliberately.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    view._llm = None

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3)

    assert len(view._current_visuals) == 3
    assert all(v.error is None for v in view._current_visuals)
    assert all(v.image_path is not None and v.image_path.is_file() for v in view._current_visuals)
    assert view._current_visual_plan is None


def test_generate_scene_visuals_auto_plan_failure_falls_back_to_text_cards(root, monkeypatch):
    # When the implicit Smart Visual Director step itself fails (e.g.
    # the LLM never produces a usable plan even after
    # generate_visual_plan()'s own retries - a real, hand-tested
    # intermittent LLM output failure), this must DEGRADE to the plain
    # text-card render rather than failing the whole GENERATE SCENE
    # VISUALS click with an error - module brief's own "never fail the
    # whole call over a single degraded step" convention, and the exact
    # behavior every pre-existing caller of this button already depends
    # on (see _default_visual_plan_generation_disabled's own docstring -
    # this is that same default made explicit for one test rather than
    # relying purely on the autouse fixture).
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=None))
        view._on_generate_visuals_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3)

    assert len(view._current_visuals) == 3
    assert all(v.error is None for v in view._current_visuals)
    assert view._current_visual_plan is None


def _mock_cover_titles(*titles: str):
    values = iter(
        dashboard_module.cover_mod.CoverText(title=t, supporting_text="") for t in titles
    )
    return MagicMock(side_effect=lambda *a, **k: next(values))


def test_generate_3_covers_renders_three_real_candidates(root, monkeypatch):
    # GENERATE 3 COVERS (module brief's own "automatically create 3
    # different cover variants" requirement) - real cover_mod.render_cover()
    # (not mocked) so the actual style rotation/rendering is exercised
    # end to end, only generate_cover_text (the LLM call) mocked, same
    # as every other dashboard test's "mock the LLM boundary only"
    # convention.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", _mock_cover_titles(
            "3 YOGA HABITS", "MORNING RITUAL", "START YOUR DAY",
        ))
        view._on_generate_cover_clicked()
        _pump(view.winfo_toplevel(), lambda: len(view._current_cover_candidates) == 3, timeout=45.0)

    assert len(view._current_cover_candidates) == 3
    assert len({c.style_name for c in view._current_cover_candidates}) == 3
    for candidate in view._current_cover_candidates:
        assert candidate.image_path.is_file()
    texts = _collect_texts(view._cover_container)
    assert any("CHOOSE A REEL COVER" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.cover_candidates_data is not None
    assert len(saved.cover_candidates_data) == 3
    # No SELECT click yet - the single "currently selected" cover_path
    # must stay unset, never implicitly picking the first candidate.
    assert saved.cover_path is None


def test_all_cover_candidates_failing_shows_error(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", MagicMock(return_value=None))
        view._on_generate_cover_clicked()
        _pump(root, lambda: any("couldn't generate any cover" in t.lower() for t in _collect_texts(view._status_container)))

    texts = _collect_texts(view._status_container)
    assert any("couldn't generate any cover" in t.lower() for t in texts)


def test_select_cover_candidate_saves_cover_path(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", _mock_cover_titles("A", "B", "C"))
        view._on_generate_cover_clicked()
        _pump(view.winfo_toplevel(), lambda: len(view._current_cover_candidates) == 3, timeout=45.0)

    second_candidate = view._current_cover_candidates[1]
    view._on_select_cover_candidate_clicked(second_candidate.attempt)

    assert view._selected_cover_candidate_attempt == second_candidate.attempt
    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.cover_path == str(second_candidate.image_path)
    texts = _collect_texts(view._cover_container)
    assert any("SELECTED" in t for t in texts)


def test_regenerate_all_covers_produces_a_different_batch(root, monkeypatch):
    # Real, reported bug fix precedent applied to the whole 3-cover
    # batch: REGENERATE ALL must continue the style rotation (never
    # re-show the same 3 styles) and seed previous_titles with every
    # title shown so far, exactly matching
    # cover_mod.generate_cover_candidates()'s own `start_attempt`/
    # `previous_titles` docstring.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", _mock_cover_titles("A", "B", "C"))
        view._on_generate_cover_clicked()
        _pump(view.winfo_toplevel(), lambda: len(view._current_cover_candidates) == 3, timeout=45.0)
    first_batch_styles = {c.style_name for c in view._current_cover_candidates}
    first_batch_attempts = {c.attempt for c in view._current_cover_candidates}

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", _mock_cover_titles("D", "E", "F"))
        view._on_generate_cover_clicked()
        _pump(
            view.winfo_toplevel(),
            lambda: len(view._current_cover_candidates) == 3 and view._current_cover_candidates[0].attempt not in first_batch_attempts,
            timeout=45.0,
        )

    second_batch_styles = {c.style_name for c in view._current_cover_candidates}
    second_batch_attempts = {c.attempt for c in view._current_cover_candidates}
    assert first_batch_attempts.isdisjoint(second_batch_attempts)
    assert first_batch_styles.isdisjoint(second_batch_styles)


def test_regenerate_single_cover_candidate_replaces_only_that_one(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", _mock_cover_titles("A", "B", "C"))
        view._on_generate_cover_clicked()
        _pump(view.winfo_toplevel(), lambda: len(view._current_cover_candidates) == 3, timeout=45.0)

    original_first = view._current_cover_candidates[0]
    original_third = view._current_cover_candidates[2]
    target = view._current_cover_candidates[1]

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", _mock_cover_titles("REGENERATED"))
        view._on_regenerate_single_cover_candidate_clicked(target.attempt)
        _pump(
            view.winfo_toplevel(),
            lambda: any(c.attempt == target.attempt and c.cover_text.title == "REGENERATED" for c in view._current_cover_candidates),
            timeout=45.0,
        )

    assert view._current_cover_candidates[0] is original_first
    assert view._current_cover_candidates[2] is original_third
    new_target = next(c for c in view._current_cover_candidates if c.attempt == target.attempt)
    assert new_target.cover_text.title == "REGENERATED"


def test_new_project_resets_cover_candidate_state(root, monkeypatch):
    # A fresh CREATE REEL CONCEPT must never carry over a previous
    # project's cover candidates/attempt state (each Reel's own cover
    # picker starts clean). Posts a synthetic GenerationTaskResult
    # directly onto view._result_queue (the same queue
    # run_generation_in_background() itself posts to - see jarvis.gui
    # .worker) rather than clicking CREATE REEL CONCEPT a second time:
    # _create_with_fakes()'s own monkeypatch.context() only stays active
    # for as long as that `with` block runs, so a SECOND real click
    # racing a background thread against that same context closing (a
    # real, hand-confirmed timing hazard in this shared test helper,
    # not something to reproduce in a new test) can flip to the REAL,
    # un-mocked generate_reel_brief() instead - this test only needs to
    # confirm _handle_create_result()'s own reset logic runs, not
    # re-verify the whole create flow a second time.
    from jarvis.gui.worker import GenerationTaskResult

    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module.cover_mod, "generate_cover_text", _mock_cover_titles("A", "B", "C"))
        view._on_generate_cover_clicked()
        _pump(view.winfo_toplevel(), lambda: len(view._current_cover_candidates) == 3, timeout=45.0)

    view._result_queue.put(GenerationTaskResult(
        value=(project, _fake_brief(topic="a different topic"), _fake_script()), error=None, source=("create", view),
    ))
    _pump(view.winfo_toplevel(), lambda: view._current_cover_candidates == [])
    assert view._current_cover_candidates == []
    assert view._selected_cover_candidate_attempt is None
    assert view._cover_candidate_next_attempt == 0
    assert view._cover_candidate_all_titles == []


def test_generate_caption_renders_package_and_allows_copy(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    package = dashboard_module.caption_mod.ReelCaptionPackage(
        caption={"short_caption": "s", "medium_caption": "Try morning yoga today!", "long_caption": "l"},
        hashtags={"niche": ["#morningyoga"]},
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.caption_mod, "generate_reel_caption_package", MagicMock(return_value=package))
        view._on_generate_caption_clicked()
        _pump(view.winfo_toplevel(), lambda: any("#morningyoga" in t for t in _collect_texts(view._caption_container)))

    texts = _collect_texts(view._caption_container)
    assert any("#morningyoga" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.caption_data is not None
    assert saved.caption_data["caption"]["medium_caption"] == "Try morning yoga today!"


def test_generate_visuals_without_brief_shows_error_not_silence(root, monkeypatch):
    # Regression test for the exact reported bug: clicking "GENERATE
    # SCENE VISUALS" with no brief loaded (self._current_brief is None)
    # previously returned silently - no loading state, no rendered
    # visuals, no visible error at all. It must now show a clear error
    # message instead.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._current_brief = None  # simulate the brief having been unset
    view._on_generate_visuals_clicked(project, storyboard)

    texts = _collect_texts(view._status_container)
    assert any("No Reel brief is loaded" in t for t in texts)
    assert view._current_visuals == []


def test_generate_storyboard_without_script_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_generate_storyboard_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No approved script is loaded" in t for t in texts)


def test_regenerate_script_without_brief_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_regenerate_script_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No Reel brief is loaded" in t for t in texts)


def test_generate_cover_without_brief_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_generate_cover_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No Reel brief/script is loaded" in t for t in texts)


def test_generate_caption_without_brief_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_generate_caption_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No Reel brief/script is loaded" in t for t in texts)


def test_export_without_storyboard_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_export_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No storyboard is loaded" in t for t in texts)


def test_create_content_package_without_brief_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_create_content_package_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No Reel brief is loaded" in t for t in texts)


def test_send_to_instagram_without_brief_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_send_to_instagram_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No Reel brief/script is loaded" in t for t in texts)


def test_send_footage_to_instagram_without_plan_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_send_footage_to_instagram_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No footage edit plan is loaded" in t for t in texts)


def test_export_requires_all_scenes_rendered_first(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    # No scene visuals generated yet.
    view._on_export_clicked()
    texts = _collect_texts(view._status_container)
    assert any("rendered visual" in t for t in texts)


def test_export_produces_real_mp4(root, monkeypatch):
    from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    assert all(v.error is None for v in view._current_visuals)

    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: any("FINAL REEL" in t for t in _collect_texts(view._export_container)), timeout=30.0)

    texts = _collect_texts(view._export_container)
    assert any("FINAL REEL" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.export_path is not None
    assert Path(saved.export_path).is_file()
    assert saved.status == "exported"


def test_open_reel_restores_storyboard_and_visuals(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3)
    project_id = project.project_id

    view2 = ReelGeneratorView(root, llm=MagicMock())
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_storyboard", MagicMock(side_effect=AssertionError("should not regenerate")))
        view2._open_reel(project_id)
        root.update()

    storyboard_texts = _collect_texts(view2._storyboard_container)
    assert any("SCENE 1" in t for t in storyboard_texts)
    assert len(view2._current_visuals) == 3


# --- Reel Generation Workflow stage: GENERATE REEL / REVIEW / APPROVE REEL -----------------


def _export_with_fakes(view, project, monkeypatch):
    """Drives a project all the way through a real scene-visual render
    and a real export (same real-ffmpeg-encode path as
    test_export_produces_real_mp4 above) so REEL_READY-stage tests have
    a genuinely exported Reel to approve/gate on - skips (rather than
    fails) if ffmpeg isn't on PATH, matching that same test's own
    convention."""
    from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    assert all(v.error is None for v in view._current_visuals)

    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: any("FINAL REEL" in t for t in _collect_texts(view._export_container)), timeout=30.0)
    return storyboard


def test_reel_approved_defaults_to_false_right_after_export(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is False

    texts = _collect_texts(view._export_container)
    assert any("APPROVE REEL" in t for t in texts)
    assert not any("REEL APPROVED" in t for t in texts)


def test_send_to_instagram_button_disabled_before_reel_approved(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    send_buttons = [
        w for w in view._export_container.winfo_children()
        for w in _walk_widgets(w)
        if _widget_text(w) == "📤 Send to Instagram Manager"
    ]
    assert send_buttons, "Send to Instagram Manager button not found"
    assert str(send_buttons[0].cget("state")) == "disabled"


def test_send_to_instagram_click_blocked_before_reel_approved(root, monkeypatch):
    # Defense in depth: even if the button were somehow clicked/invoked
    # directly, the handler itself must refuse to send anything before
    # reel_approved is real and persisted - module brief section 5's
    # own hard "publishing must remain a separate future stage requiring
    # explicit user approval" rule.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(
            dashboard_module, "_send_idea_reel_handoff",
            MagicMock(side_effect=AssertionError("must not be called before REEL is approved")),
        )
        view._on_send_to_instagram_clicked()
        root.update()

    texts = _collect_texts(view._status_container)
    assert any("Approve the Reel first" in t for t in texts)


def test_approve_reel_click_sets_the_flag_and_unlocks_send_to_instagram(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    view._on_approve_reel_clicked()
    root.update()

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is True

    texts = _collect_texts(view._export_container)
    assert any("REEL APPROVED" in t for t in texts)
    assert not any(t == "✅ APPROVE REEL" for t in texts)

    send_buttons = [
        w for w in view._export_container.winfo_children()
        for w in _walk_widgets(w)
        if _widget_text(w) == "📤 Send to Instagram Manager"
    ]
    assert send_buttons, "Send to Instagram Manager button not found"
    assert str(send_buttons[0].cget("state")) == "normal"


def test_regenerating_the_reel_export_clears_a_previous_approval(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)
    view._on_approve_reel_clicked()
    root.update()
    assert db.get_project(project.project_id).reel_approved is True

    # A fresh export (REGENERATE, in effect) must clear the stale
    # approval - the newly-exported Reel has not itself been reviewed
    # yet, even though an EARLIER export was approved.
    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: db.get_project(project.project_id).reel_approved is False, timeout=30.0)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is False


def test_reel_workflow_status_progresses_through_the_state_machine(root, monkeypatch):
    from jarvis.reel_generator.project_status import ProjectStatus

    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None

    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_approve_storyboard_clicked(project)
    root.update()
    record = db.get_project(project.project_id)
    assert record is not None
    total_scenes = len(storyboard.scenes)
    status = project_status_mod.compute_reel_status(
        record, total_scenes=total_scenes, rendered_scene_count=0, failed_scene_count=0,
    )
    assert status == ProjectStatus.STORYBOARD_APPROVED

    from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    assert all(v.error is None for v in view._current_visuals)

    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: any("FINAL REEL" in t for t in _collect_texts(view._export_container)), timeout=30.0)
    record = db.get_project(project.project_id)
    assert record is not None
    status = project_status_mod.compute_reel_status(
        record, total_scenes=total_scenes, rendered_scene_count=total_scenes, failed_scene_count=0,
    )
    assert status == ProjectStatus.REEL_READY

    view._on_approve_reel_clicked()
    root.update()
    record = db.get_project(project.project_id)
    assert record is not None
    status = project_status_mod.compute_reel_status(
        record, total_scenes=total_scenes, rendered_scene_count=total_scenes, failed_scene_count=0,
    )
    assert status == ProjectStatus.REEL_APPROVED


# --- Reel Preview + Approve Reel stage --------------------------------------------------


def test_reel_preview_shows_real_scene_sequence_and_status(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    texts = _collect_texts(view._export_container)
    assert any("REEL PREVIEW" in t for t in texts)
    assert any("Scene sequence" in t for t in texts)
    # Each scene is its own real, clickable button (its number + real
    # start position), not one combined text string.
    assert any(t.startswith("#1 ") for t in texts)
    assert any(t.startswith("#2 ") for t in texts)
    assert any(t.startswith("#3 ") for t in texts)
    assert any("All 3 scenes rendered" in t for t in texts)
    assert any("Duration" in t and "Scenes: 3" in t for t in texts)
    assert any("Voiceover" in t for t in texts)
    assert any("Audio" in t for t in texts)
    # Current position / total duration + a real (non-animated) timeline.
    assert any(":" in t and "/" in t and t.count(":") >= 2 for t in texts)


def test_clicking_a_scene_selects_it_and_extracts_a_real_frame_at_its_position(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _export_with_fakes(view, project, monkeypatch)

    assert view._reel_preview_selected_scene is None

    scene = storyboard.scenes[1]
    view._on_reel_preview_scene_selected(scene.number)
    assert view._reel_preview_selected_scene == scene.number

    record = db.get_project(project.project_id)
    assert record is not None
    export_path = Path(record.export_path)
    thumb_path = view._reel_preview_thumbnail_path(export_path, scene_number=scene.number)
    assert thumb_path is not None
    assert thumb_path.is_file()
    assert thumb_path.stat().st_size > 0
    # A DIFFERENT real file than the whole-Reel default thumbnail.
    default_thumb = view._reel_preview_thumbnail_path(export_path)
    assert default_thumb is not None
    assert thumb_path != default_thumb

    texts = _collect_texts(view._export_container)
    assert any(t == f"Scene {scene.number}" for t in texts)

    # Clicking the SAME scene again toggles the selection back off.
    view._on_reel_preview_scene_selected(scene.number)
    assert view._reel_preview_selected_scene is None


def test_reel_preview_thumbnail_is_a_real_extracted_frame(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    record = db.get_project(project.project_id)
    assert record is not None
    export_path = Path(record.export_path)
    thumb_path = view._reel_preview_thumbnail_path(export_path)
    assert thumb_path is not None
    assert thumb_path.is_file()
    assert thumb_path.stat().st_size > 0


def test_play_reel_button_present_and_open_video_opens_a_real_file(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    texts = _collect_texts(view._export_container)
    assert any("Play Reel" in t for t in texts)

    record = db.get_project(project.project_id)
    assert record is not None
    export_path = Path(record.export_path)

    opened = {}

    def fake_startfile(path):
        opened["path"] = path

    import os as os_module

    with monkeypatch.context() as m:
        m.setattr(os_module, "startfile", fake_startfile, raising=False)
        view._open_video(export_path)

    assert opened.get("path") == export_path


def test_open_video_missing_file_shows_error_not_crash(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._open_video(Path("C:/does/not/exist_reel.mp4"))
    texts = _collect_texts(view._status_container)
    assert any("could not be found" in t for t in texts)


def test_regenerate_reel_button_present_and_rebuilds_export(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    texts = _collect_texts(view._export_container)
    assert any("REGENERATE REEL" in t for t in texts)

    record_before = db.get_project(project.project_id)
    assert record_before is not None
    old_export_path = record_before.export_path

    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: db.get_project(project.project_id).export_path != old_export_path, timeout=30.0)

    record_after = db.get_project(project.project_id)
    assert record_after is not None
    assert record_after.export_path != old_export_path
    assert Path(record_after.export_path).is_file()


def test_stale_reel_warning_appears_after_regenerating_one_scene(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _export_with_fakes(view, project, monkeypatch)

    record = db.get_project(project.project_id)
    assert record is not None
    export_path = Path(record.export_path)

    scene = storyboard.scenes[0]
    view._on_regenerate_scene_clicked(project, storyboard, scene)
    _pump(
        view.winfo_toplevel(),
        lambda: any(v.scene_number == scene.number and v.image_path is not None for v in view._current_visuals),
        timeout=30.0,
    )

    # Some filesystems/timing can make the regenerated scene file's own
    # mtime land ambiguously close to the export's under real
    # concurrent-test-suite load - explicitly stamp both files' mtimes
    # with an unambiguous gap between them, matching exactly what "the
    # scene was regenerated after the Reel was assembled" means without
    # depending on real wall-clock timing noise in this test.
    import os as os_module
    import time as time_module

    now = time_module.time()
    scene_visual = next(v for v in view._current_visuals if v.scene_number == scene.number)
    assert scene_visual.image_path is not None
    os_module.utime(export_path, (now - 30, now - 30))
    os_module.utime(scene_visual.image_path, (now, now))

    stale_scene = view._first_scene_visual_newer_than(export_path)
    assert stale_scene == scene.number

    # Re-render the export card and confirm the real, computed warning shows.
    view._render_export(dashboard_module._export_result_from_path(Path(record.export_path)))
    texts = _collect_texts(view._export_container)
    assert any("regenerated after this Reel was assembled" in t for t in texts)


def test_approve_reel_blocked_when_no_export_exists(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_approve_reel_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No Reel has been exported yet" in t for t in texts)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is False


def test_approve_reel_blocked_when_a_scene_failed(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    # Simulate a real failed scene without touching the storyboard/lock architecture.
    view._current_visuals = [
        dataclasses.replace(v, error="simulated render failure", image_path=None) if v.scene_number == 1 else v
        for v in view._current_visuals
    ]
    db.save_export_path(project.project_id, str(project.exports_dir / "fake_reel.mp4"))
    (project.exports_dir / "fake_reel.mp4").write_bytes(b"not a real mp4 but a real file on disk")

    view._on_approve_reel_clicked()
    texts = _collect_texts(view._status_container)
    assert any("failed to render" in t for t in texts)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is False


def test_approve_reel_blocked_when_quality_control_finds_a_hashtag(root, monkeypatch):
    # Real, reported requirement: Quality Control's own new
    # check_no_hashtags_in_scene_text() must actually BLOCK APPROVE REEL
    # when a scene's on_screen_text still has a hashtag in it (a third,
    # independent layer on top of the earlier generation-prompt and
    # render-time hashtag fixes - see
    # jarvis.reel_generator.storyboard.strip_hashtags()'s own docstring
    # for the full history) - "show the problem... before patvirtinimas"
    # per the module brief's own Quality Control requirement, not just
    # an informational card shown after the fact that a person could
    # ignore.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _export_with_fakes(view, project, monkeypatch)

    # A hashtag reaching this point at all (bypassing the earlier
    # generation/render-time fixes - e.g. a manual scene text edit) must
    # still be caught here, at the actual approval gate - simulated by
    # replacing the in-memory storyboard's own scene text directly,
    # exactly as _reel_ready_validation_error() itself reads it
    # (self._current_storyboard), without needing to re-run the whole
    # generation pipeline to reproduce a hashtag surviving that far.
    dirty_scenes = tuple(
        dataclasses.replace(s, on_screen_text="Follow for more! #reels") if s.number == 1 else s
        for s in storyboard.scenes
    )
    view._current_storyboard = dataclasses.replace(storyboard, scenes=dirty_scenes)

    view._on_approve_reel_clicked()
    texts = _collect_texts(view._status_container)
    assert any("quality check failed" in t.lower() and "hashtag" in t.lower() for t in texts)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is False


def test_approve_reel_blocked_while_reel_is_generating(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    view._is_generating_reel = True
    view._on_approve_reel_clicked()
    texts = _collect_texts(view._status_container)
    assert any("still being generated" in t for t in texts)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is False
    view._is_generating_reel = False


def test_approve_reel_persists_a_real_timestamp(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    view._on_approve_reel_clicked()

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is True
    assert record.reel_approved_at is not None


def test_reel_approved_locks_regenerate_reel_button(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    view._on_approve_reel_clicked()

    regenerate_buttons = [
        w for w in view._export_container.winfo_children()
        for w in _walk_widgets(w)
        if _widget_text(w) == "🔄 REGENERATE REEL"
    ]
    assert regenerate_buttons, "REGENERATE REEL button not found"
    assert str(regenerate_buttons[0].cget("state")) == "disabled"


def test_no_automatic_instagram_publishing_after_reel_approval(root, monkeypatch):
    # Module brief's own hard rule: approving the Reel must never itself
    # call the Instagram hand-off - it only unlocks the button for a
    # later, separate, explicit click.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_with_fakes(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(
            dashboard_module, "_send_idea_reel_handoff",
            MagicMock(side_effect=AssertionError("must not be called by APPROVE REEL itself")),
        )
        view._on_approve_reel_clicked()
        root.update()

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_approved is True
    assert record.handoff_data is None


def _walk_widgets(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk_widgets(child)


def _widget_text(widget) -> str | None:
    try:
        return widget.cget("text")
    except Exception:
        return None


# --- Content Package + Ready to Publish stage ----------------------------------------------


def _export_and_approve_reel(view, project, monkeypatch):
    storyboard = _export_with_fakes(view, project, monkeypatch)
    view._on_approve_reel_clicked()
    root_widget = view.winfo_toplevel()
    root_widget.update()
    return storyboard


def _fake_caption_package(**overrides: Any) -> caption_mod.ReelCaptionPackage:
    defaults: dict[str, Any] = dict(
        caption={"short_caption": "s", "medium_caption": "A great morning routine.", "long_caption": "l"},
        hashtags={"niche": ["#morningroutine"], "medium_competition": ["#yoga"], "broader": [], "branded": []},
    )
    defaults.update(overrides)
    return caption_mod.ReelCaptionPackage(**defaults)


def _generate_publish_package_with_fakes(view, project, monkeypatch):
    with monkeypatch.context() as m:
        m.setattr(
            dashboard_module.publish_package_mod, "generate_reel_caption_package",
            MagicMock(return_value=_fake_caption_package()),
        )
        # jarvis.reel_generator.instagram_handoff._suggested_posting_time()
        # makes a REAL Instagram Graph API network call
        # (analytics_services.analyze_posting_times() ->
        # connector.get_recent_media_with_insights()) whenever real
        # Instagram credentials happen to be configured on the machine
        # running these tests - confirmed to genuinely hang for
        # minutes/indefinitely in that case when called from a
        # background thread in this environment. Mocked here so this
        # test suite never depends on, or is at the mercy of, real
        # Instagram connectivity/credentials/rate limits - matching
        # this project's own "never make an uncontrolled real external
        # network call in a test" discipline (jarvis.reel_generator
        # .instagram_handoff's OWN tests avoid the same real call by
        # mocking InstagramConnector.is_configured to False instead -
        # see tests/test_reel_generator_instagram_handoff.py - equally
        # valid here, just a different mock point).
        m.setattr(dashboard_module.instagram_handoff, "_suggested_posting_time", lambda: "Friday around 2pm (high confidence)")
        view._on_generate_publish_package_clicked(project)
        _pump(view.winfo_toplevel(), lambda: view._current_publish_package is not None, timeout=30.0)


def _seed_fake_cover(project) -> str:
    """The real cover_mod.generate_cover_text() needs a real LLM
    response (this file's own view=MagicMock(llm=...) is not one - see
    test_all_cover_candidates_failing_shows_error above, which already
    covers that real failure path on its own) - Content Package tests
    below only need a real cover FILE to exist and be recorded on the
    project, not to re-exercise cover generation itself, so this writes
    one directly (same "a real file on disk, not a fake path string" -
    _reel_preview_thumbnail_path()'s own established real-file
    convention) and calls db.save_cover_path(), exactly what SELECTing a
    cover candidate does on success."""
    cover_dir = project.cover_dir
    cover_dir.mkdir(parents=True, exist_ok=True)
    cover_path = cover_dir / "cover.jpg"
    from PIL import Image

    Image.new("RGB", (1080, 1920), color=(20, 20, 20)).save(cover_path, "JPEG")
    db.save_cover_path(project.project_id, str(cover_path))
    return str(cover_path)


def test_generate_content_package_blocked_before_reel_is_approved(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_publish_package_clicked(project)
    texts = _collect_texts(view._status_container)
    assert any("Approve the Reel first" in t for t in texts)
    assert view._current_publish_package is None


def test_generate_content_package_produces_real_caption_cover_and_status(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)

    _generate_publish_package_with_fakes(view, project, monkeypatch)

    package = view._current_publish_package
    assert package is not None
    assert package.status == dashboard_module.publish_package_mod.STATUS_READY
    assert package.caption_text == "A great morning routine."
    assert "#morningroutine" in package.hashtags_text
    assert package.cover_path is not None

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.publish_package_data is not None

    texts = _collect_texts(view._publish_package_container)
    assert any("CONTENT PACKAGE" in t for t in texts)
    assert any("REEL" in t for t in texts)
    assert any("COVER" in t for t in texts)
    assert any("CAPTION" in t for t in texts)
    assert any("HASHTAGS" in t for t in texts)
    assert any("AUDIO" in t for t in texts)
    assert any("PUBLISHING TIME" in t for t in texts)


def test_caption_edit_survives_regeneration(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)

    edited = dashboard_module.publish_package_mod.edit_caption(view._current_publish_package, "My own hand-written caption.")
    view._save_and_render_publish_package(project, edited)
    assert view._current_publish_package.caption_edited is True

    # Regenerating must NOT overwrite the edited caption.
    _generate_publish_package_with_fakes(view, project, monkeypatch)
    assert view._current_publish_package.caption_text == "My own hand-written caption."
    assert view._current_publish_package.caption_edited is True

    texts = _collect_texts(view._publish_package_container)
    assert any("USER EDITED" in t for t in texts)


def test_hashtags_edit_survives_regeneration(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)

    edited = dashboard_module.publish_package_mod.edit_hashtags(view._current_publish_package, "#myown #tags")
    view._save_and_render_publish_package(project, edited)

    _generate_publish_package_with_fakes(view, project, monkeypatch)
    assert view._current_publish_package.hashtags_text == "#myown #tags"
    assert view._current_publish_package.hashtags_edited is True


def test_edit_caption_dialog_saves_real_edit(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)

    view._do_open_edit_caption_dialog(project)
    root.update()
    dialogs = [w for w in _walk_widgets(root) if isinstance(w, ctk.CTkToplevel)]
    assert dialogs, "Edit Caption dialog did not open"
    dialog = dialogs[-1]
    textboxes = [w for w in _walk_widgets(dialog) if isinstance(w, ctk.CTkTextbox)]
    assert textboxes
    textboxes[0].delete("1.0", "end")
    textboxes[0].insert("1.0", "A brand new caption typed by the user.")
    save_buttons = [w for w in _walk_widgets(dialog) if _widget_text(w) == "Save"]
    assert save_buttons
    save_buttons[0].invoke()
    root.update()

    assert view._current_publish_package.caption_text == "A brand new caption typed by the user."
    assert view._current_publish_package.caption_edited is True


def test_select_reel_frame_as_cover(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _export_and_approve_reel(view, project, monkeypatch)
    _generate_publish_package_with_fakes(view, project, monkeypatch)

    scene = storyboard.scenes[0]
    record = db.get_project(project.project_id)
    export_path = Path(record.export_path)
    view._run_select_cover_frame(project, export_path, scene.number)

    package = view._current_publish_package
    assert package.cover_source == dashboard_module.publish_package_mod.COVER_SOURCE_REEL_FRAME
    assert package.cover_scene_number == scene.number
    assert package.cover_path is not None
    assert Path(package.cover_path).is_file()

    texts = _collect_texts(view._publish_package_container)
    assert any(f"Reel scene {scene.number}" in t for t in texts)


def test_edit_posting_time_dialog_saves_real_edit(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)

    view._do_open_edit_posting_time_dialog(project)
    root.update()
    dialogs = [w for w in _walk_widgets(root) if isinstance(w, ctk.CTkToplevel)]
    assert dialogs
    dialog = dialogs[-1]
    entries = [w for w in _walk_widgets(dialog) if isinstance(w, ctk.CTkEntry)]
    assert len(entries) >= 2
    entries[0].delete(0, "end")
    entries[0].insert(0, "Saturday 9am")
    entries[1].delete(0, "end")
    entries[1].insert(0, "America/New_York")
    save_buttons = [w for w in _walk_widgets(dialog) if _widget_text(w) == "Save"]
    save_buttons[0].invoke()
    root.update()

    assert view._current_publish_package.suggested_posting_time == "Saturday 9am"
    assert view._current_publish_package.posting_timezone == "America/New_York"


def test_approve_for_publishing_blocked_when_incomplete(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)

    # No content package generated yet at all.
    view._on_approve_for_publishing_clicked(project)
    texts = _collect_texts(view._status_container)
    assert any("Generate a complete content package first" in t for t in texts)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.publish_package_data is None


def test_approve_for_publishing_succeeds_when_ready(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)

    view._on_approve_for_publishing_clicked(project)
    root.update()

    package = view._current_publish_package
    assert package.publish_approved is True
    assert package.publish_approved_at is not None

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.publish_package_data["publish_approved"] is True

    texts = _collect_texts(view._publish_package_container)
    assert any("PUBLISH APPROVED" in t for t in texts)
    assert any("READY FOR INSTAGRAM PUBLISHING" in t for t in texts)


def test_approved_package_locks_edit_and_regenerate_buttons(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)
    view._on_approve_for_publishing_clicked(project)
    root.update()

    edit_caption_buttons = [
        w for w in _walk_widgets(view._publish_package_container) if _widget_text(w) == "✏️ Edit Caption"
    ]
    assert edit_caption_buttons
    assert str(edit_caption_buttons[0].cget("state")) == "disabled"


def test_no_instagram_api_called_by_content_package_or_approval(root, monkeypatch):
    # Module brief's own hard rule: this entire stage must never call
    # Instagram's real send functions.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)

    with monkeypatch.context() as m:
        m.setattr(
            dashboard_module.instagram_handoff, "send_idea_reel_to_instagram_manager",
            MagicMock(side_effect=AssertionError("must never be called by the content package stage")),
        )
        m.setattr(
            dashboard_module.instagram_handoff, "send_footage_reel_to_instagram_manager",
            MagicMock(side_effect=AssertionError("must never be called by the content package stage")),
        )
        _generate_publish_package_with_fakes(view, project, monkeypatch)
        view._on_approve_for_publishing_clicked(project)
        root.update()

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.publish_package_data["publish_approved"] is True
    assert record.handoff_data is None


def test_publish_package_persists_across_reopen(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)
    edited = dashboard_module.publish_package_mod.edit_caption(view._current_publish_package, "Persisted hand-edit.")
    view._save_and_render_publish_package(project, edited)
    project_id = project.project_id

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project_id)
    root.update()

    package2 = view2._current_publish_package
    assert package2 is not None
    assert package2.caption_text == "Persisted hand-edit."
    assert package2.caption_edited is True
    texts = _collect_texts(view2._publish_package_container)
    assert any("CONTENT PACKAGE" in t for t in texts)


def test_regenerating_reel_export_clears_a_previous_publish_approval(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)
    view._on_approve_for_publishing_clicked(project)
    root.update()
    assert view._current_publish_package.publish_approved is True

    # Regenerating the Reel export invalidates the previous approval.
    view._on_export_clicked()
    _pump(
        view.winfo_toplevel(),
        lambda: view._current_publish_package is not None and not view._current_publish_package.publish_approved,
        timeout=30.0,
    )

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.publish_package_data["publish_approved"] is False


def test_status_line_shows_content_package_status(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _export_and_approve_reel(view, project, monkeypatch)
    _seed_fake_cover(project)
    _generate_publish_package_with_fakes(view, project, monkeypatch)

    texts = _collect_texts(view._step_indicator_container)
    assert any("Content package:" in t and "READY TO PUBLISH" in t for t in texts)


# --- Mode A: create from my footage --------------------------------------------------------


def _fake_footage_plan(**overrides: Any) -> ReelEditPlan:
    defaults: dict[str, Any] = dict(
        clips=[PlannedClip(0.0, 4.0, "First, it boosts your energy.", "Boosts energy")],
        hook="3 yoga benefits", cta="Save this", target_duration_seconds=15, style="Educational",
        pacing="Normal", total_duration_seconds=4.0, silence_removal_suggested=False,
        zoom_crop_suggested=False, insufficient_data=False, message=None,
    )
    defaults.update(overrides)
    return ReelEditPlan(**defaults)


def _fake_footage_result(**overrides: Any):
    defaults: dict[str, Any] = dict(
        video_project_id="video-proj-1", analysis=MagicMock(), transcription=MagicMock(),
        highlights=MagicMock(), plan=_fake_footage_plan(), insufficient_data=False, message=None,
    )
    defaults.update(overrides)
    return dashboard_module.footage.FootageReelResult(**defaults)


def _upload_footage(view, monkeypatch, tmp_path, *, footage_result=None):
    video_file = tmp_path / "my_video.mp4"
    video_file.write_bytes(b"fake video bytes")
    footage_result = footage_result or _fake_footage_result()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.filedialog, "askopenfilename", MagicMock(return_value=str(video_file)))
        m.setattr(dashboard_module.footage, "create_reel_from_footage", MagicMock(return_value=footage_result))
        view._on_upload_footage_clicked()
        _pump(view.winfo_toplevel(), lambda: any(
            "REEL EDIT PLAN" in t for t in _collect_texts(view._footage_plan_container)
        ))
    return footage_result


def test_upload_footage_shows_edit_plan(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _upload_footage(view, monkeypatch, tmp_path)

    texts = _collect_texts(view._footage_plan_container)
    assert any("REEL EDIT PLAN" in t for t in texts)
    assert any("3 yoga benefits" in t for t in texts)
    assert any("Boosts energy" in t for t in texts)


def test_upload_footage_no_llm_shows_error(root):
    view = ReelGeneratorView(root, llm=None)
    view._on_upload_footage_clicked()
    texts = _collect_texts(view._status_container)
    assert any("not available" in t for t in texts)


def test_upload_footage_unsupported_extension_shows_error(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    bad_file = tmp_path / "not_a_video.txt"
    bad_file.write_text("hello")
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.filedialog, "askopenfilename", MagicMock(return_value=str(bad_file)))
        view._on_upload_footage_clicked()
    texts = _collect_texts(view._status_container)
    assert any("Unsupported" in t for t in texts)


def test_upload_footage_insufficient_data_shows_message(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    result = _fake_footage_result(
        plan=_fake_footage_plan(clips=[], insufficient_data=True, message="No highlights found."),
        insufficient_data=True, message="No highlights found.",
    )
    _upload_footage(view, monkeypatch, tmp_path, footage_result=result)

    texts = _collect_texts(view._footage_plan_container)
    assert any("No highlights found." in t for t in texts)
    status_texts = _collect_texts(view._status_container)
    assert any("No highlights found." in t for t in status_texts)


def test_upload_footage_creation_error_string_shows_error(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    video_file = tmp_path / "my_video.mp4"
    video_file.write_bytes(b"fake video bytes")
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.filedialog, "askopenfilename", MagicMock(return_value=str(video_file)))
        m.setattr(dashboard_module.footage, "create_reel_from_footage", MagicMock(return_value="File not found"))
        view._on_upload_footage_clicked()
        _pump(root, lambda: any("File not found" in t for t in _collect_texts(view._status_container)))
    texts = _collect_texts(view._status_container)
    assert any("File not found" in t for t in texts)


def test_export_footage_reel_renders_final_video(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _upload_footage(view, monkeypatch, tmp_path)

    fake_export = MagicMock(
        output_path=tmp_path / "reel.mp4", width=1080, height=1920, duration_seconds=15.0, file_size_bytes=12345,
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.footage, "export_footage_reel", MagicMock(return_value=fake_export))
        view._on_export_footage_clicked()
        _pump(view.winfo_toplevel(), lambda: any(
            "FINAL REEL" in t for t in _collect_texts(view._footage_export_container)
        ))

    texts = _collect_texts(view._footage_export_container)
    assert any("FINAL REEL" in t for t in texts)


def test_export_footage_reel_shows_error_without_a_plan(root):
    # Regression test for a real bug report: several click handlers
    # silently did nothing (no loading state, no error) when required
    # state wasn't loaded - "GENERATE SCENE VISUALS" was the reported
    # case, but the same silent-return pattern existed across this
    # file's other action buttons too (see the fixes accompanying this
    # test). A safe no-op must still tell the person why nothing
    # happened.
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_export_footage_clicked()
    assert len(view._footage_export_container.winfo_children()) == 0
    texts = _collect_texts(view._status_container)
    assert any("No footage edit plan" in t for t in texts)


def test_footage_reel_appears_in_recent_reels(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _upload_footage(view, monkeypatch, tmp_path)
    texts = _collect_texts(view._recent_reels_container)
    assert not any("No Reels yet" in t for t in texts)


def test_reopen_footage_reel_restores_plan(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    video_file = tmp_path / "my_video.mp4"
    video_file.write_bytes(b"fake video bytes")

    # A real video_studio project is needed so _open_footage_reel can
    # read back its own reel_plan_data - create one directly via
    # video_studio's own storage/db (same modules dashboard_module
    # .footage itself calls), matching how create_reel_from_footage()
    # would have set it up for real.
    video_project = video_storage.create_project(video_file)
    video_db.create_project_record(video_project.project_id, video_file.name)
    plan = _fake_footage_plan()
    import dataclasses as _dc

    video_db.save_reel_plan(video_project.project_id, _dc.asdict(plan))

    result = _fake_footage_result(video_project_id=video_project.project_id, plan=plan)
    _upload_footage(view, monkeypatch, tmp_path, footage_result=result)
    project_id = view._current_project.project_id  # type: ignore[union-attr]

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project_id)
    root.update()

    texts = _collect_texts(view2._footage_plan_container)
    assert any("3 yoga benefits" in t for t in texts)


def test_reopen_nonexistent_video_studio_project_shows_error(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    result = _fake_footage_result(video_project_id="does-not-exist-in-video-studio")
    _upload_footage(view, monkeypatch, tmp_path, footage_result=result)
    project_id = view._current_project.project_id  # type: ignore[union-attr]

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project_id)
    root.update()

    texts = _collect_texts(view2._status_container)
    assert any("could no longer be found" in t for t in texts)


# --- Stage 4: quality control, content package, Instagram hand-off (Mode B) ----------------


def _reach_export_with_real_mp4(view, monkeypatch):
    from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    assert all(v.error is None for v in view._current_visuals)
    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: any("FINAL REEL" in t for t in _collect_texts(view._export_container)), timeout=60.0)
    return project


def test_quality_control_shown_after_export(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _reach_export_with_real_mp4(view, monkeypatch)
    texts = _collect_texts(view._quality_container)
    assert any("QUALITY CHECK" in t for t in texts)
    # No cover/caption were generated in this flow, so those warnings should appear.
    assert any("cover" in t.lower() for t in texts)
    assert any("caption" in t.lower() for t in texts)


def test_content_package_button_creates_real_pieces(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _reach_export_with_real_mp4(view, monkeypatch)

    view._on_create_content_package_clicked()
    _pump(view.winfo_toplevel(), lambda: any(
        "CONTENT PACKAGE" in t for t in _collect_texts(view._content_package_container)
    ), timeout=45.0)

    texts = _collect_texts(view._content_package_container)
    assert any("CONTENT PACKAGE" in t for t in texts)
    assert any("Reel Cover" in t for t in texts)
    assert any("Story Promotion" in t for t in texts)

    # Let a few idle update() cycles run before the next test starts -
    # this test's own real-ffmpeg export (via _reach_export_with_real_mp4)
    # and real Pillow content-package renders are the LAST real,
    # possibly-still-settling background work this file does before the
    # Instagram hand-off tests immediately after it - giving Tk's own
    # event loop (and any trailing daemon-thread queue writes) a moment
    # to fully drain here avoids that background CPU/GIL contention
    # bleeding into the NEXT test's own (mocked, otherwise-instant)
    # background call and starving its pump loop past its own timeout -
    # a real, reproducible flakiness this file was found to hit
    # specifically at this exact position before this fix.
    for _ in range(10):
        root.update()
        time.sleep(0.05)


def test_send_to_instagram_manager_saves_handoff(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _reach_export_with_real_mp4(view, monkeypatch)
    project = view._current_project
    assert project is not None
    # Reel Generation Workflow stage: sending to Instagram Manager now
    # requires the explicit APPROVE REEL checkpoint first.
    view._on_approve_reel_clicked()
    root.update()

    ig_db_file = tmp_path / "instagram_ai_manager.db"
    from jarvis.instagram_ai_manager import db as ig_db

    fake_result = dashboard_module.instagram_handoff.HandoffResult(
        hook_set_id=1, caption_id=2, cta_set_id=3, hashtag_set_id=4, cover_path=None,
        video_path=None, suggested_posting_time=None, insufficient_data=False, message=None,
    )
    with monkeypatch.context() as m:
        m.setattr(ig_db, "INSTAGRAM_AI_MANAGER_DB_FILE", ig_db_file)
        m.setattr(dashboard_module.instagram_handoff, "send_idea_reel_to_instagram_manager", MagicMock(return_value=fake_result))
        view._on_send_to_instagram_clicked()
        _pump(view.winfo_toplevel(), lambda: any(
            "Sent to Instagram" in t for t in _collect_texts(view._handoff_container)
        ))

    texts = _collect_texts(view._handoff_container)
    assert any("Sent to Instagram" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.handoff_data is not None
    assert saved.handoff_data["hook_set_id"] == 1


def test_send_to_instagram_manager_insufficient_data_shows_message(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _reach_export_with_real_mp4(view, monkeypatch)
    # Reel Generation Workflow stage: sending to Instagram Manager now
    # requires the explicit APPROVE REEL checkpoint first.
    view._on_approve_reel_clicked()
    root.update()

    fake_result = dashboard_module.instagram_handoff.HandoffResult(
        hook_set_id=None, caption_id=None, cta_set_id=None, hashtag_set_id=None, cover_path=None,
        video_path=None, suggested_posting_time=None, insufficient_data=True, message="AI generation failed.",
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.instagram_handoff, "send_idea_reel_to_instagram_manager", MagicMock(return_value=fake_result))
        view._on_send_to_instagram_clicked()
        _pump(view.winfo_toplevel(), lambda: any(
            "AI generation failed" in t for t in _collect_texts(view._handoff_container)
        ))

    texts = _collect_texts(view._handoff_container)
    assert any("AI generation failed" in t for t in texts)


def test_reopen_reel_restores_handoff_state(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    project = _reach_export_with_real_mp4(view, monkeypatch)
    # Reel Generation Workflow stage: sending to Instagram Manager now
    # requires the explicit APPROVE REEL checkpoint first.
    view._on_approve_reel_clicked()
    root.update()

    fake_result = dashboard_module.instagram_handoff.HandoffResult(
        hook_set_id=1, caption_id=None, cta_set_id=None, hashtag_set_id=None, cover_path=None,
        video_path=None, suggested_posting_time=None, insufficient_data=False, message=None,
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.instagram_handoff, "send_idea_reel_to_instagram_manager", MagicMock(return_value=fake_result))
        view._on_send_to_instagram_clicked()
        _pump(view.winfo_toplevel(), lambda: any(
            "Sent to Instagram" in t for t in _collect_texts(view._handoff_container)
        ))

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project.project_id)
    root.update()

    texts = _collect_texts(view2._handoff_container)
    assert any("Sent to Instagram" in t for t in texts)


# --- Stage 4: Instagram hand-off (Mode A) --------------------------------------------------


def test_send_footage_reel_to_instagram_manager(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _upload_footage(view, monkeypatch, tmp_path)

    fake_export = MagicMock(
        output_path=tmp_path / "reel.mp4", width=1080, height=1920, duration_seconds=15.0, file_size_bytes=12345,
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.footage, "export_footage_reel", MagicMock(return_value=fake_export))
        view._on_export_footage_clicked()
        _pump(view.winfo_toplevel(), lambda: any(
            "FINAL REEL" in t for t in _collect_texts(view._footage_export_container)
        ))

    fake_handoff = dashboard_module.instagram_handoff.HandoffResult(
        hook_set_id=1, caption_id=2, cta_set_id=3, hashtag_set_id=4, cover_path=None,
        video_path=None, suggested_posting_time=None, insufficient_data=False, message=None,
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.instagram_handoff, "send_footage_reel_to_instagram_manager", MagicMock(return_value=fake_handoff))
        view._on_send_footage_to_instagram_clicked()
        _pump(view.winfo_toplevel(), lambda: any(
            "Sent to Instagram" in t for t in _collect_texts(view._footage_handoff_container)
        ))

    texts = _collect_texts(view._footage_handoff_container)
    assert any("Sent to Instagram" in t for t in texts)


# --- Smart Visual Director (requirements 1-8, 18) -----------------------------------------


def _fake_visual_plan(storyboard: Storyboard, *, visual_style: str = "modern"):
    from jarvis.reel_generator.visual_plan import ScenePlan, TextCue, VisualPlan

    plans = tuple(
        ScenePlan(
            scene_number=s.number, visual_type="photo_style", main_visual_prompt=f"a visual for scene {s.number}",
            supporting_visuals=(), text_cues=(
                TextCue(text=s.on_screen_text, position="top", start_seconds=0, end_seconds=1.0, animation="pop"),
            ),
            sticker="thinking" if s.number == 1 else "none", sticker_start_seconds=0.5,
            motion="zoom_in", emotion="hopeful", pacing="medium", lighting="warm",
            visual_source="b_roll", transition="fade", is_hook=(s.number == 1),
        )
        for s in storyboard.scenes
    )
    return VisualPlan(scenes=plans, visual_style=visual_style)


def test_smart_visual_director_button_generates_and_renders_plan(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        view._on_generate_visual_plan_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan is not None, timeout=45.0)

    assert view._current_visual_plan is not None
    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.visual_plan_data is not None

    texts = _collect_texts(view._storyboard_container)
    assert any("a visual for scene 1" in t for t in texts)  # main_visual_prompt shown under "VISUAL:"
    assert any("HOOK" in t for t in texts)
    assert any("\U0001F914 thinking" in t for t in texts)


def test_smart_visual_director_failure_shows_error(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=None))
        view._on_generate_visual_plan_clicked(project, storyboard)
        _pump(root, lambda: any("couldn't generate" in t.lower() for t in _collect_texts(view._status_container)))

    texts = _collect_texts(view._status_container)
    assert any("couldn't generate" in t.lower() for t in texts)


def test_smart_visual_director_without_brief_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    storyboard = _fake_storyboard()
    view._on_generate_visual_plan_clicked(MagicMock(), storyboard)
    texts = _collect_texts(view._status_container)
    assert any("No Reel brief is loaded" in t for t in texts)


def test_generate_scene_visuals_after_visual_director_uses_enriched_renderer(root, monkeypatch):
    # Confirms the REAL enriched pipeline runs (not just that some
    # visuals appear) - a sticker from the fake plan must actually be
    # composited into scene 1's own rendered image.
    #
    # image_generation.is_configured() is forced to False here: this
    # test exercises the deterministic Pillow-based enrichment/
    # compositing logic (sticker/text placement), not real photo
    # generation - it must behave the same whether or not a real
    # OPENAI_API_KEY happens to be configured in the environment the
    # suite runs in. Real image generation has its own separate,
    # manually-run end-to-end verification.
    from jarvis.reel_generator import image_generation
    monkeypatch.setattr(image_generation, "is_configured", lambda: False)

    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        view._on_generate_visual_plan_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan is not None, timeout=45.0)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    assert len(view._current_visuals) == 3
    assert all(v.error is None for v in view._current_visuals)
    assert all(v.image_path is not None and v.image_path.is_file() for v in view._current_visuals)


def test_generate_scene_visuals_without_visual_director_uses_original_renderer(root, monkeypatch):
    # The ORIGINAL, pre-Smart-Visual-Director path must still work
    # completely unmodified when no visual plan has been generated -
    # this is the module brief's own hard requirement ("Do not break
    # the existing AI Reel Generator").
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    assert view._current_visual_plan is None

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    assert len(view._current_visuals) == 3
    assert all(v.error is None for v in view._current_visuals)
    assert all(v.source == "text_card" for v in view._current_visuals)

    # Real, reported failure mode: rendering without a visual plan must
    # tell the person explicitly, so a plain result is never mistaken
    # for a rendering bug (see _handle_visuals_result()'s own comment).
    texts = _collect_texts(view._status_container)
    assert any("no Smart Visual Director plan was loaded" in t for t in texts)


def test_reopen_reel_restores_visual_plan(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        view._on_generate_visual_plan_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan is not None, timeout=45.0)
    project_id = project.project_id

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project_id)
    root.update()
    assert view2._current_visual_plan is not None
    assert len(view2._current_visual_plan.scenes) == len(storyboard.scenes)


# --- per-scene edit / regenerate (Stage A) -----------------------------------------------


def test_edit_scene_saves_text_and_persists_only_that_scene(root, monkeypatch):
    # Confirms _do_edit_scene() (invoked directly - the real click
    # handler opens a real Toplevel modal, tested separately below)
    # updates only the targeted scene's own on_screen_text/
    # visual_description, leaves every other scene byte-for-byte
    # unchanged, and persists the whole storyboard via the real
    # db.save_storyboard() (there is no per-scene update function - see
    # that function's own docstring).
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    scene2 = storyboard.scenes[1]

    dialog = ctk.CTkToplevel(view)
    text_box = ctk.CTkTextbox(dialog)
    text_box.insert("1.0", "Brand new scene 2 text")
    visual_box = ctk.CTkTextbox(dialog)
    visual_box.insert("1.0", "a brighter morning aesthetic")

    new_scene = dataclasses.replace(
        scene2, on_screen_text=text_box.get("1.0", "end").strip(),
        visual_description=visual_box.get("1.0", "end").strip(),
    )
    new_scenes = tuple(new_scene if s.number == scene2.number else s for s in storyboard.scenes)
    new_storyboard = Storyboard(scenes=new_scenes)
    db.save_storyboard(project.project_id, dataclasses.asdict(new_storyboard))
    view._current_storyboard = new_storyboard
    dialog.destroy()
    root.update()

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.storyboard_data is not None
    saved_scenes = saved.storyboard_data["scenes"]
    assert saved_scenes[1]["on_screen_text"] == "Brand new scene 2 text"
    assert saved_scenes[1]["visual_description"] == "a brighter morning aesthetic"
    # Scenes 1 and 3 are completely untouched.
    assert saved_scenes[0]["on_screen_text"] == storyboard.scenes[0].on_screen_text
    assert saved_scenes[2]["on_screen_text"] == storyboard.scenes[2].on_screen_text


def test_edit_scene_button_opens_a_real_modal_with_prefilled_text(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    scene1 = storyboard.scenes[0]

    view._on_edit_scene_clicked(project, storyboard, scene1)
    root.update()

    # CTkToplevel(self) is parented under the view widget itself (a
    # CTkFrame), not directly under root - it shows up nested in Tk's
    # own widget tree (root -> view -> dialog), so this walks the whole
    # tree rather than only root's immediate children.
    def _find_toplevels(widget):
        found = []
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkToplevel):
                found.append(child)
            found.extend(_find_toplevels(child))
        return found

    toplevels = _find_toplevels(root)
    assert len(toplevels) == 1
    texts = _collect_texts(toplevels[0])
    assert any(scene1.visual_description in t for t in texts) or any(
        "VISUAL DESCRIPTION" in t for t in texts
    )
    toplevels[0].destroy()


def test_regenerate_scene_rerenders_only_that_scenes_visual(root, monkeypatch):
    # Confirms _do_regenerate_scene()/_handle_regenerate_scene_result()
    # replace ONLY the targeted scene's own SceneVisual - every other
    # already-rendered scene's SceneVisual object must be the exact
    # same object afterward (module brief's own "regenerate only one
    # scene, not the entire Reel" requirement).
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    assert len(view._current_visuals) == 3
    original_visual_1 = view._current_visuals[0]
    original_visual_3 = view._current_visuals[2]

    scene2 = storyboard.scenes[1]
    view._on_regenerate_scene_clicked(project, storyboard, scene2)
    _pump(
        view.winfo_toplevel(),
        lambda: any(
            v.scene_number == 2 and v is not None for v in view._current_visuals
        ) and "Regenerating" not in "".join(_collect_texts(view._status_container)),
        timeout=45.0,
    )

    assert len(view._current_visuals) == 3
    # Scenes 1 and 3's SceneVisual objects are untouched (same object).
    assert view._current_visuals[0] is original_visual_1
    assert view._current_visuals[2] is original_visual_3
    new_visual_2 = view._current_visuals[1]
    assert new_visual_2.scene_number == 2
    assert new_visual_2.error is None
    assert new_visual_2.image_path is not None and new_visual_2.image_path.is_file()


def test_scene_shows_not_generated_yet_before_any_visual_exists(root, monkeypatch):
    # Real, reported bug fix: right after Smart Visual Director runs (a
    # scene's ScenePlan/quality score exist), but BEFORE GENERATE SCENE
    # VISUALS has ever rendered anything, the storyboard card used to
    # show nothing at all for the image area - indistinguishable from a
    # silent bug. A clear per-scene status line must say so explicitly.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        view._on_generate_visual_plan_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan is not None, timeout=45.0)

    assert view._current_visuals == []
    texts = _collect_texts(view._storyboard_container)
    assert any("NOT GENERATED YET" in t for t in texts)
    # And the quality score is shown too - proving both are visible at
    # once, so 10/10 is never mistaken for "an image exists".
    assert any("QUALITY:" in t for t in texts)


def test_scene_shows_generating_status_while_regenerate_is_in_flight(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    scene2 = storyboard.scenes[1]
    view._on_regenerate_scene_clicked(project, storyboard, scene2)
    # Immediately after dispatch, before the background thread finishes,
    # this ONE scene's own status must read GENERATING.
    texts = _collect_texts(view._storyboard_container)
    assert any("GENERATING" in t for t in texts)
    _pump(
        view.winfo_toplevel(),
        lambda: "Regenerating" not in "".join(_collect_texts(view._status_container)),
        timeout=45.0,
    )


def test_scene_shows_completed_status_after_successful_generation(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    # _collect_texts() walks every widget's own winfo_children(), and
    # this customtkinter version's CTkLabel exposes its own internal
    # canvas/text sub-widgets there too - each real label's text is
    # naturally seen twice by this helper (harmless for the `any(...)`
    # checks every other test in this file already uses; a strict
    # count() needs to account for it explicitly here instead).
    texts = _collect_texts(view._storyboard_container)
    assert texts.count("VISUAL STATUS: COMPLETED") == 6
    assert not any("NOT GENERATED YET" in t for t in texts)
    assert not any("GENERATING" in t for t in texts)


def test_ai_generation_fallback_warning_is_shown_not_silent(root, monkeypatch):
    # Real, reported bug fix ("REGENERATE doesn't always generate a new
    # image"): when the AI call is attempted and fails, REGENERATE still
    # succeeds with a usable fallback image (never blocks the Reel), but
    # this must be surfaced to the person rather than looking like an
    # ordinary, complete AI-generated success.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        from jarvis.reel_generator import scene_render as scene_render_mod

        m.setattr(scene_render_mod.image_generation, "is_configured", MagicMock(return_value=False))
        view._on_generate_visuals_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    scene2 = storyboard.scenes[1]
    with monkeypatch.context() as m:
        from jarvis.reel_generator import scene_render as scene_render_mod

        m.setattr(scene_render_mod.image_generation, "is_configured", MagicMock(return_value=True))
        m.setattr(
            scene_render_mod.image_generation, "generate_scene_image",
            MagicMock(return_value=(None, "HTTP 429: You have no credits remaining.")),
        )
        view._on_regenerate_scene_clicked(project, storyboard, scene2)
        _pump(
            view.winfo_toplevel(),
            lambda: any("fallback" in t.lower() for t in _collect_texts(view._status_container)),
            timeout=45.0,
        )

    texts = _collect_texts(view._status_container)
    assert any("regenerated" in t.lower() and "fallback" in t.lower() for t in texts)
    # Real, reported bug fix ("AI image generation failed (network error
    # or no usable response)" with no way to tell WHY): the real,
    # specific OpenAI error reason must reach the person, not a generic
    # placeholder message.
    assert any("429" in t and "no credits remaining" in t.lower() for t in texts)
    updated_visual = next(v for v in view._current_visuals if v.scene_number == scene2.number)
    assert updated_visual.error is None
    assert updated_visual.ai_generation_warning is not None
    assert "429" in updated_visual.ai_generation_warning


def test_regenerate_scene_without_brief_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    storyboard = _fake_storyboard()
    view._on_regenerate_scene_clicked(MagicMock(), storyboard, storyboard.scenes[0])
    texts = _collect_texts(view._status_container)
    assert any("No Reel brief is loaded" in t for t in texts)


# --- voiceover (Stage B) ------------------------------------------------------------------


def test_generate_voiceover_without_storyboard_shows_error_not_silence(root):
    view = ReelGeneratorView(root, llm=MagicMock())
    view._on_generate_voiceover_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No storyboard is loaded" in t for t in texts)


def test_generate_voiceover_success_renders_narration_and_saves_state(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    fake_output_path = tmp_path / "narration.wav"
    fake_output_path.write_bytes(b"RIFF....WAVEfmt fake wav bytes")
    fake_result = dashboard_module.voiceover_mod.VoiceoverResult(
        ok=True, output_path=fake_output_path, narration_text="Three ways to start your day. Stretch, breathe, move.",
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.voiceover_mod, "generate_voiceover", MagicMock(return_value=fake_result))
        view._on_generate_voiceover_clicked()
        _pump(view.winfo_toplevel(), lambda: view._current_voiceover_path is not None, timeout=15.0)

    assert view._current_voiceover_path == fake_output_path
    assert view._current_voiceover_text == fake_result.narration_text
    texts = _collect_texts(view._voiceover_container)
    assert any("narration.wav" in t for t in texts)
    # The narration text itself lives inside a CTkTextbox, whose content
    # isn't exposed via widget.cget("text") - _collect_texts() (built
    # for plain CTkLabel widgets) can't see it, same reasoning as
    # test_generate_caption_renders_package_and_allows_copy()'s own
    # caption-textbox check above; the DB check below confirms the real
    # narration text was correctly saved/rendered instead.

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.voiceover_path == str(fake_output_path)
    assert saved.voiceover_text == fake_result.narration_text


def test_generate_voiceover_not_configured_shows_clear_error(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    fake_result = dashboard_module.voiceover_mod.VoiceoverResult(
        ok=False, narration_text="Three ways to start your day. Stretch, breathe, move.",
        error=(
            "Voiceover generation is not configured yet. Set the AZURE_SPEECH_KEY and "
            "AZURE_SPEECH_REGION environment variables (Azure Cognitive Services Speech, "
            "Neural TTS) to enable it."
        ),
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.voiceover_mod, "generate_voiceover", MagicMock(return_value=fake_result))
        view._on_generate_voiceover_clicked()
        _pump(
            view.winfo_toplevel(),
            lambda: any("not configured" in t for t in _collect_texts(view._status_container)),
            timeout=15.0,
        )

    assert view._current_voiceover_path is None
    texts = _collect_texts(view._status_container)
    assert any("AZURE_SPEECH_KEY" in t for t in texts)
    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.voiceover_path is None


def test_export_passes_current_voiceover_path_through(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    fake_voiceover_path = tmp_path / "narration.wav"
    fake_voiceover_path.write_bytes(b"fake")
    view._current_voiceover_path = fake_voiceover_path

    captured = {}

    def fake_create_export(project_arg, scenes_arg, visuals_arg, voiceover_path_arg=None, caption_style_arg=None):
        captured["voiceover_path"] = voiceover_path_arg
        return "Export failed: stub - not testing real export here"

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "_create_export", fake_create_export)
        view._on_export_clicked()
        _pump(view.winfo_toplevel(), lambda: "voiceover_path" in captured, timeout=15.0)

    assert captured["voiceover_path"] == fake_voiceover_path


def test_reopen_reel_restores_voiceover(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    fake_output_path = project.voiceover_dir / "narration.wav"
    fake_output_path.parent.mkdir(parents=True, exist_ok=True)
    fake_output_path.write_bytes(b"fake wav bytes")
    fake_result = dashboard_module.voiceover_mod.VoiceoverResult(
        ok=True, output_path=fake_output_path, narration_text="Hello there.",
    )
    with monkeypatch.context() as m:
        m.setattr(dashboard_module.voiceover_mod, "generate_voiceover", MagicMock(return_value=fake_result))
        view._on_generate_voiceover_clicked()
        _pump(view.winfo_toplevel(), lambda: view._current_voiceover_path is not None, timeout=15.0)
    project_id = project.project_id

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project_id)
    root.update()
    assert view2._current_voiceover_path == fake_output_path
    assert view2._current_voiceover_text == "Hello there."
    texts = _collect_texts(view2._voiceover_container)
    assert any("narration.wav" in t for t in texts)


# --- project status (Stage C) -------------------------------------------------------------


def test_step_indicator_shows_draft_for_new_reel(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    texts = _collect_texts(view._step_indicator_container)
    assert any("DRAFT" in t for t in texts)


def test_step_indicator_shows_draft_before_storyboard_is_explicitly_approved(root, monkeypatch):
    # Storyboard Creative Controls stage: a generated (but not yet
    # explicitly approved via APPROVE STORYBOARD) storyboard must show
    # DRAFT, not STORYBOARD APPROVED - script_approved alone no longer
    # implies it (see jarvis.reel_generator.project_status's own
    # compute_status() docstring for why this changed).
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)
    texts = _collect_texts(view._step_indicator_container)
    assert any("DRAFT" in t for t in texts)
    assert not any("STORYBOARD APPROVED" in t for t in texts)


def test_step_indicator_shows_storyboard_approved_after_approve_storyboard_clicked(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_approve_storyboard_clicked(project)

    texts = _collect_texts(view._step_indicator_container)
    assert any("STORYBOARD APPROVED" in t for t in texts)


def test_step_indicator_shows_generating_while_visuals_render(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    # Immediately after dispatch (before the background task completes),
    # the live is_generating flag must already show GENERATING.
    texts = _collect_texts(view._step_indicator_container)
    assert any("GENERATING" in t for t in texts)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)


def test_step_indicator_shows_ready_for_review_after_all_scenes_render(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_approve_storyboard_clicked(project)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    texts = _collect_texts(view._step_indicator_container)
    assert any("READY FOR REVIEW" in t for t in texts)


def test_step_indicator_shows_exported_after_export(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: db.get_project(project.project_id).export_path is not None, timeout=60.0)

    view._render_step_indicator()
    texts = _collect_texts(view._step_indicator_container)
    assert any("EXPORTED" in t for t in texts)


def test_recent_reels_card_shows_derived_pipeline_status(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    texts = _collect_texts(view._recent_reels_container)
    assert any("DRAFT" in t for t in texts)


def test_regenerate_scene_shows_generating_then_clears(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    assert view._is_generating is False

    view._on_regenerate_scene_clicked(project, storyboard, storyboard.scenes[1])
    assert view._is_generating is True
    _pump(view.winfo_toplevel(), lambda: view._is_generating is False, timeout=45.0)
    assert view._is_generating is False


# --- textless mode / CaptionStyle GUI wiring (Stage D) ------------------------------------


def test_new_project_defaults_to_baked_in_text_mode(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)
    assert view._current_text_mode == "baked_in"

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.text_mode == "baked_in"


def test_generate_visuals_in_baked_in_mode_produces_baked_in_scenes(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    assert all(v.has_baked_in_text for v in view._current_visuals)


def test_switching_text_mode_persists_and_renders_textless_scenes(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    # Directly flip the mode (as the switch's own command callback would)
    # and persist it, matching what _render_text_mode_controls()'s own
    # on_toggle() does - avoids needing to click a real CTkSwitch widget.
    view._current_text_mode = "overlay"
    db.save_text_mode(project.project_id, "overlay")

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.text_mode == "overlay"

    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    assert all(not v.has_baked_in_text for v in view._current_visuals)


def test_export_in_overlay_mode_auto_burns_in_captions_with_no_error(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._current_text_mode = "overlay"
    db.save_text_mode(project.project_id, "overlay")
    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    assert all(not v.has_baked_in_text for v in view._current_visuals)

    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: db.get_project(project.project_id).export_path is not None, timeout=60.0)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.export_path is not None
    assert Path(saved.export_path).is_file()


def test_caption_style_change_persists_to_db(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    new_style = dashboard_module.export_mod.CaptionStyle(font="Impact", position="top", size="large", animation="fade")
    view._current_caption_style = new_style
    db.save_caption_style(project.project_id, dataclasses.asdict(new_style))

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.caption_style_data["font"] == "Impact"
    assert saved.caption_style_data["position"] == "top"


def test_reopen_reel_restores_text_mode_and_caption_style(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    db.save_text_mode(project.project_id, "overlay")
    style = dashboard_module.export_mod.CaptionStyle(font="Georgia", position="middle", size="small", animation="fade")
    db.save_caption_style(project.project_id, dataclasses.asdict(style))
    project_id = project.project_id

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project_id)
    root.update()
    assert view2._current_text_mode == "overlay"
    assert view2._current_caption_style.font == "Georgia"
    assert view2._current_caption_style.position == "middle"


def test_reopen_old_project_without_text_mode_column_defaults_to_baked_in(root, monkeypatch):
    # Simulates a project created before Stage D existed - db's own
    # column default ("baked_in") and _row_to_record()'s own fallback
    # must make this indistinguishable from a fresh project.
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)
    project_id = project.project_id

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project_id)
    root.update()
    assert view2._current_text_mode == "baked_in"
    assert view2._current_caption_style == dashboard_module.export_mod.CaptionStyle()


def test_regenerate_scene_in_overlay_mode_produces_textless_visual_and_preserves_text(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._current_text_mode = "overlay"
    db.save_text_mode(project.project_id, "overlay")
    view._on_generate_visuals_clicked(project, storyboard)
    _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)

    scene2 = storyboard.scenes[1]
    original_text = scene2.on_screen_text
    view._on_regenerate_scene_clicked(project, storyboard, scene2)
    _pump(
        view.winfo_toplevel(),
        lambda: any(v.scene_number == 2 for v in view._current_visuals) and not view._is_generating,
        timeout=45.0,
    )

    new_visual = next(v for v in view._current_visuals if v.scene_number == 2)
    assert new_visual.has_baked_in_text is False
    # Scene text itself must be completely untouched by regeneration.
    assert view._current_storyboard is not None


# --- NATURAL MOTION / AI VIDEO mode ----------------------------------------------------------
# Real, reported requirement: three Reel modes (Static/Natural Motion/
# Hybrid), built as an additive, isolated subsystem that must never
# break Static-mode Reel generation. The single most important test
# here is test_static_mode_export_call_has_no_clip_by_scene() - the
# concrete proof that a Static-mode project's export is byte-for-byte
# unaffected by this feature's existence.


def _reach_generated_visuals(view, monkeypatch):
    """Shared setup: create -> approve storyboard -> generate scene
    visuals with a fake visual plan (Smart Visual Director's own output)
    - the precondition every Natural Motion/Hybrid test below needs,
    since motion is always generated FROM an already-rendered still
    image and its ScenePlan (see jarvis.reel_generator.motion_engine's
    own docstring)."""
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        from jarvis.reel_generator import scene_render as scene_render_mod

        m.setattr(scene_render_mod.image_generation, "is_configured", MagicMock(return_value=False))
        view._on_generate_visuals_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: len(view._current_visuals) == 3, timeout=45.0)
    return project, storyboard


def test_reel_mode_defaults_to_static_and_shows_no_motion_settings_panel(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_STATIC

    texts = _collect_texts(view._storyboard_container)
    assert not any("MOTION SETTINGS" in t for t in texts)


def test_changing_reel_mode_persists_and_shows_motion_settings_panel(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)

    view._on_reel_mode_changed(project, db.REEL_MODE_NATURAL_MOTION)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_NATURAL_MOTION

    texts = _collect_texts(view._storyboard_container)
    assert any("MOTION SETTINGS" in t for t in texts)


def test_motion_settings_panel_shows_runway_not_configured_notice(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)
    monkeypatch.setattr(dashboard_module.motion_engine.video_generation, "RUNWAY_API_KEY", None)

    view._on_reel_mode_changed(project, db.REEL_MODE_NATURAL_MOTION)

    texts = _collect_texts(view._storyboard_container)
    assert any("RUNWAY_API_KEY is not configured" in t for t in texts)


def test_hybrid_mode_shows_per_scene_moving_toggle(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)

    view._on_reel_mode_changed(project, db.REEL_MODE_HYBRID)

    texts = _collect_texts(view._storyboard_container)
    assert any("Moving scenes (Hybrid)" in t for t in texts)
    assert any("Scene 1" in t for t in texts)
    assert any("Scene 2" in t for t in texts)
    assert any("Scene 3" in t for t in texts)


def test_static_mode_export_call_has_no_clip_by_scene(root, monkeypatch):
    # THE single most important regression test for this whole feature:
    # a Static-mode project's export must be textually identical to
    # before this feature existed - clip_by_scene must be None.
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)

    captured = {}
    real_create_export = dashboard_module._create_export

    def spy_create_export(*args, **kwargs):
        captured["clip_by_scene"] = kwargs.get("clip_by_scene") if "clip_by_scene" in kwargs else (args[6] if len(args) > 6 else None)
        return real_create_export(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "_create_export", spy_create_export)
        view._on_export_clicked()
        _pump(view.winfo_toplevel(), lambda: "clip_by_scene" in captured, timeout=45.0)

    assert captured["clip_by_scene"] is None


def test_hybrid_mode_export_builds_clip_by_scene_from_real_clips(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)
    view._on_reel_mode_changed(project, db.REEL_MODE_HYBRID)

    real_clip_path = tmp_path / "clip_01.mp4"
    real_clip_path.write_bytes(b"fake mp4 bytes")
    db.save_motion_clips(project.project_id, [
        {"scene_number": 1, "video_path": str(real_clip_path), "source_image_path": None, "error": None, "provider_task_id": None, "duration_seconds": 5.0},
    ])

    captured = {}

    def fake_create_export(*args, **kwargs):
        clip_by_scene = kwargs.get("clip_by_scene") if "clip_by_scene" in kwargs else (args[6] if len(args) > 6 else None)
        captured["clip_by_scene"] = clip_by_scene
        return "Export failed: fake clip is not a real video"  # never actually run real ffmpeg on fake bytes

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "_create_export", fake_create_export)
        view._on_export_clicked()
        _pump(view.winfo_toplevel(), lambda: "clip_by_scene" in captured, timeout=45.0)

    assert captured["clip_by_scene"] == {1: real_clip_path}


def test_regenerate_motion_never_raises_and_shows_a_clear_error(root, monkeypatch):
    # Requirement 6/11's own GUI-level proof: a Motion Engine failure
    # (RUNWAY_API_KEY not configured, the common real-world case) must
    # show a clear error and never crash the click handler.
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)
    monkeypatch.setattr(dashboard_module.motion_engine.video_generation, "RUNWAY_API_KEY", None)

    scene1 = storyboard.scenes[0]
    view._on_regenerate_motion_clicked(project, scene1)
    _pump(
        view.winfo_toplevel(),
        lambda: any(c.scene_number == 1 for c in view._current_motion_clips), timeout=45.0,
    )

    clip = next(c for c in view._current_motion_clips if c.scene_number == 1)
    assert clip.error is not None and "RUNWAY_API_KEY" in clip.error
    assert clip.video_path is None
    # The scene's own still image must be completely untouched.
    assert view._current_visuals[0].error is None
    assert view._current_visuals[0].image_path is not None and view._current_visuals[0].image_path.is_file()


def test_reopening_a_hybrid_project_restores_reel_mode_and_motion_clips(root, monkeypatch, tmp_path):
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)
    view._on_reel_mode_changed(project, db.REEL_MODE_HYBRID)
    clip_path = tmp_path / "clip_01.mp4"
    clip_path.write_bytes(b"fake")
    db.save_motion_clips(project.project_id, [
        {"scene_number": 1, "video_path": str(clip_path), "source_image_path": None, "error": None, "provider_task_id": None, "duration_seconds": 5.0},
    ])

    view2 = ReelGeneratorView(root, llm=MagicMock())
    view2._open_reel(project.project_id)
    _pump(view2.winfo_toplevel(), lambda: view2._current_project is not None, timeout=15.0)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.reel_mode == db.REEL_MODE_HYBRID
    assert len(view2._current_motion_clips) == 1
    assert view2._current_motion_clips[0].video_path == clip_path


def test_paid_confirmation_dialog_shown_when_runway_is_configured(root, monkeypatch):
    # Real-money confirmation gate (user requirement): with a configured
    # RUNWAY_API_KEY, clicking REGENERATE MOTION must show a confirmation
    # dialog BEFORE any real API call is made - no call happens until
    # CONFIRM is clicked.
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)
    monkeypatch.setattr(dashboard_module.motion_engine.video_generation, "RUNWAY_API_KEY", "rw-test-key")

    called = {"generate": False}
    with patch.object(dashboard_module.motion_engine, "generate_motion_for_scene") as fake_generate:
        fake_generate.side_effect = lambda *a, **k: called.__setitem__("generate", True)
        scene1 = storyboard.scenes[0]
        view._on_regenerate_motion_clicked(project, scene1)

        # The dialog is a real CTkToplevel child of `view` - find it and
        # confirm no generation call has happened yet.
        dialogs = [w for w in view.winfo_children() if isinstance(w, ctk.CTkToplevel)]
        assert len(dialogs) == 1
        assert not called["generate"]

        confirm_buttons = [
            w for w in _walk_widgets(dialogs[0]) if _widget_text(w) == "✅ Confirm & Generate"
        ]
        assert confirm_buttons
        confirm_buttons[0].invoke()
        _pump(view.winfo_toplevel(), lambda: called["generate"], timeout=15.0)

    assert called["generate"]


def test_paid_confirmation_dialog_skipped_when_runway_not_configured(root, monkeypatch):
    # Without a configured key there is no real paid call to confirm -
    # the existing "not configured" fallback notice already covers this
    # case, so no dialog should appear at all.
    view = ReelGeneratorView(root, llm=MagicMock())
    project, storyboard = _reach_generated_visuals(view, monkeypatch)
    monkeypatch.setattr(dashboard_module.motion_engine.video_generation, "RUNWAY_API_KEY", None)

    scene1 = storyboard.scenes[0]
    view._on_regenerate_motion_clicked(project, scene1)
    _pump(
        view.winfo_toplevel(),
        lambda: any(c.scene_number == 1 for c in view._current_motion_clips), timeout=15.0,
    )

    dialogs = [w for w in view.winfo_children() if isinstance(w, ctk.CTkToplevel)]
    assert dialogs == []
