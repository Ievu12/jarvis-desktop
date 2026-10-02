"""Tests for jarvis.gui.views.reel_generator.dashboard.ReelGeneratorView's
Storyboard Creative Controls stage: APPROVE STORYBOARD (lock), CHANGE
CAMERA/STYLE/VISUAL (single-scene, single-aspect LLM regeneration),
RESET SCENE (restore the original generated version), CHANGE DURATION
(folded into the existing EDIT SCENE dialog), and the "re-run quality
scoring/variety detection after any regeneration" requirement.

Uses the SAME real-(withdrawn)-CTk-root/redirected-storage pattern as
tests/test_gui_reel_generator_dashboard.py (see that file's own
docstring for the full rationale) - LLM calls mocked throughout, no
real network call."""

from __future__ import annotations

import dataclasses
import time
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.reel_generator import db, storage
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript, ScriptSegment
from jarvis.reel_generator.storyboard import Scene, Storyboard
from jarvis.reel_generator.visual_plan import ScenePlan, TextCue, VisualPlan
from jarvis.gui.views.reel_generator import dashboard as dashboard_module
from jarvis.gui.views.reel_generator.dashboard import ReelGeneratorView


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "reel_generator.db"
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(dashboard_module.db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(dashboard_module.storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_child_views(root):
    yield
    for _ in range(5):
        root.update()
    for child in list(root.winfo_children()):
        try:
            child.destroy()
        except Exception:
            pass


def _pump(root, predicate, *, timeout=15.0):
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


def _fake_brief(**overrides) -> ReelBrief:
    defaults = dict(
        topic="morning yoga", audience="wellness beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=20, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _fake_script() -> ReelScript:
    return ReelScript(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=5, text="Three ways to start your day."),
        ScriptSegment(kind="value", start_seconds=5, end_seconds=15, text="Stretch. Breathe. Move."),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="Save this Reel."),
    ))


def _fake_storyboard() -> Storyboard:
    return Storyboard(scenes=(
        Scene(number=1, start_seconds=0, end_seconds=5, segment_kind="hook", voice_text="Three ways to start your day.", on_screen_text="3 ways to start", visual_description="morning light"),
        Scene(number=2, start_seconds=5, end_seconds=15, segment_kind="value", voice_text="Stretch. Breathe. Move.", on_screen_text="Stretch, breathe, move", visual_description="stretching"),
        Scene(number=3, start_seconds=15, end_seconds=20, segment_kind="cta", voice_text="Save this Reel.", on_screen_text="Save this!", visual_description="calm ending"),
    ))


def _create_with_fakes(view, monkeypatch, *, brief=None, script=None):
    view._prompt_entry.insert("1.0", "Create a 20 second Reel about morning yoga.")
    brief = brief or _fake_brief()
    script = script or _fake_script()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_reel_brief", MagicMock(return_value=brief))
        m.setattr(dashboard_module, "generate_reel_script", MagicMock(return_value=script))
        view._on_create_clicked()
        _pump(view.winfo_toplevel(), lambda: view._current_project is not None, timeout=45.0)
    return brief, script


def _approve_with_fake_storyboard(view, project, monkeypatch):
    storyboard = _fake_storyboard()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_storyboard", MagicMock(return_value=storyboard))
        view._on_approve_clicked(project)
        _pump(view.winfo_toplevel(), lambda: view._current_storyboard is not None, timeout=45.0)
    return storyboard


def _fake_visual_plan(storyboard: Storyboard, *, visual_style: str = "modern") -> VisualPlan:
    plans = tuple(
        ScenePlan(
            scene_number=s.number, visual_type="photo_style",
            main_visual_prompt=f"A detailed camera-ready description for scene {s.number}, cinematic lighting",
            supporting_visuals=(), text_cues=(
                TextCue(text=s.on_screen_text, position="top", start_seconds=0, end_seconds=1.0, animation="pop"),
            ),
            sticker="thinking" if s.number == 1 else "none", sticker_start_seconds=0.5,
            motion="zoom_in", emotion="hopeful", pacing="medium", lighting="warm",
            environment=f"environment for scene {s.number}", subject_action=f"action for scene {s.number}",
            camera_shot="medium_shot", camera_movement="static",
            visual_source="b_roll", transition="fade", is_hook=(s.number == 1),
        )
        for s in storyboard.scenes
    )
    return VisualPlan(scenes=plans, visual_style=visual_style)


def _generate_fake_visual_plan(view, project, storyboard, monkeypatch):
    visual_plan = _fake_visual_plan(storyboard)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_visual_plan", MagicMock(return_value=visual_plan))
        view._on_generate_visual_plan_clicked(project, storyboard)
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan is not None, timeout=45.0)
    return visual_plan


# --- APPROVE STORYBOARD -------------------------------------------------------------------


def test_storyboard_starts_unlocked(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.storyboard_approved is False
    texts = _collect_texts(view._storyboard_container)
    assert any("APPROVE STORYBOARD" in t for t in texts)
    assert not any("APPROVED - locked" in t for t in texts)


def test_approve_storyboard_locks_it(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_approve_storyboard_clicked(project)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.storyboard_approved is True
    texts = _collect_texts(view._storyboard_container)
    assert any("APPROVED" in t for t in texts)


def test_approved_storyboard_disables_edit_scene_button(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_approve_storyboard_clicked(project)

    def _find_button(widget, text):
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkButton) and child.cget("text") == text:
                return child
            found = _find_button(child, text)
            if found is not None:
                return found
        return None

    edit_button = _find_button(view._storyboard_container, "✏️ EDIT SCENE")
    assert edit_button is not None
    assert edit_button.cget("state") == "disabled"


def test_regenerate_all_unlocks_a_previously_approved_storyboard(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)
    view._on_approve_storyboard_clicked(project)
    assert db.get_project(project.project_id).storyboard_approved is True

    # A DIFFERENT scene count/text than the already-approved storyboard,
    # so the _pump() predicate below can distinguish "the new background
    # task actually completed" from "the OLD storyboard is still sitting
    # in self._current_storyboard from the approve step above".
    new_storyboard = Storyboard(scenes=(
        Scene(number=1, start_seconds=0, end_seconds=5, segment_kind="hook", voice_text="new", on_screen_text="REGENERATED", visual_description="x"),
    ))
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_storyboard", MagicMock(return_value=new_storyboard))
        view._on_generate_storyboard_clicked()
        _pump(view.winfo_toplevel(), lambda: len(view._current_storyboard.scenes) == 1, timeout=45.0)

    assert len(view._current_storyboard.scenes) == 1
    record = db.get_project(project.project_id)
    assert record is not None
    assert record.storyboard_approved is False  # re-locked storyboard must be re-approved


def test_regenerate_all_saves_a_new_original_snapshot(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_storyboard(view, project, monkeypatch)

    record = db.get_project(project.project_id)
    assert record is not None
    assert record.original_storyboard_data is not None
    first_snapshot = record.original_storyboard_data

    new_storyboard = Storyboard(scenes=(
        Scene(number=1, start_seconds=0, end_seconds=5, segment_kind="hook", voice_text="new", on_screen_text="NEW TEXT", visual_description="x"),
    ))
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_storyboard", MagicMock(return_value=new_storyboard))
        view._on_generate_storyboard_clicked()
        _pump(view.winfo_toplevel(), lambda: len(view._current_storyboard.scenes) == 1, timeout=45.0)

    record2 = db.get_project(project.project_id)
    assert record2 is not None
    assert record2.original_storyboard_data != first_snapshot
    assert record2.original_storyboard_data["scenes"][0]["on_screen_text"] == "NEW TEXT"


# --- CHANGE DURATION (folded into EDIT SCENE) ----------------------------------------------


def test_change_duration_shifts_later_scenes_and_keeps_their_own_durations(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    scene1 = storyboard.scenes[0]

    view._on_edit_scene_clicked(project, storyboard, scene1)
    root.update()

    def _find_toplevels(widget):
        found = []
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkToplevel):
                found.append(child)
            found.extend(_find_toplevels(child))
        return found

    dialog = _find_toplevels(root)[0]

    def _find(widget, cls):
        for child in widget.winfo_children():
            if isinstance(child, cls):
                return child
            found = _find(child, cls)
            if found is not None:
                return found
        return None

    duration_entry = _find(dialog, ctk.CTkEntry)
    assert duration_entry is not None
    duration_entry.delete(0, "end")
    duration_entry.insert(0, "8")  # was 5 seconds - +3s delta

    def _find_save_button(widget):
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkButton) and child.cget("text") == "💾 SAVE":
                return child
            found = _find_save_button(child)
            if found is not None:
                return found
        return None

    save_button = _find_save_button(dialog)
    assert save_button is not None
    save_button.invoke()
    root.update()

    new_storyboard = view._current_storyboard
    assert new_storyboard is not None
    assert new_storyboard.scenes[0].duration_seconds == 8.0
    # Scene 2 was 5-15 (10s) - now shifted by +3 to 8-18, same own duration.
    assert new_storyboard.scenes[1].start_seconds == 8.0
    assert new_storyboard.scenes[1].end_seconds == 18.0
    assert new_storyboard.scenes[1].duration_seconds == 10.0
    # Scene 3 was 15-20 (5s) - now shifted to 18-23, same own duration.
    assert new_storyboard.scenes[2].start_seconds == 18.0
    assert new_storyboard.scenes[2].end_seconds == 23.0
    assert new_storyboard.scenes[2].duration_seconds == 5.0


# --- CHANGE CAMERA --------------------------------------------------------------------


def test_change_camera_without_visual_plan_shows_clear_error(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)

    view._on_change_camera_clicked(project, storyboard, storyboard.scenes[0])
    texts = _collect_texts(view._status_container)
    assert any("SMART VISUAL DIRECTOR" in t for t in texts)


def test_run_change_camera_updates_only_that_scenes_plan(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene2 = storyboard.scenes[1]
    original_plan_scene2 = view._current_visual_plan.for_scene(2)
    original_plan_scene3 = view._current_visual_plan.for_scene(3)

    new_plan = dataclasses.replace(original_plan_scene2, camera_shot="macro", camera_movement="handheld")
    with monkeypatch.context() as m:
        m.setattr(
            dashboard_module, "_change_scene_camera",
            MagicMock(return_value=(project, new_plan)),
        )
        view._run_change_camera(project, scene2, original_plan_scene2, camera_shot="macro", camera_movement_override=None)
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan.for_scene(2).camera_shot == "macro", timeout=15.0)

    assert view._current_visual_plan.for_scene(2).camera_shot == "macro"
    assert view._current_visual_plan.for_scene(2).camera_movement == "handheld"
    # Scene 3's own plan is byte-for-byte untouched.
    assert view._current_visual_plan.for_scene(3) == original_plan_scene3


def test_change_camera_with_movement_override_applies_it(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene1 = storyboard.scenes[0]
    plan1 = view._current_visual_plan.for_scene(1)
    llm_returned_plan = dataclasses.replace(plan1, camera_shot="wide_shot", camera_movement="static")

    with monkeypatch.context() as m:
        m.setattr(
            dashboard_module, "regenerate_scene_camera",
            MagicMock(return_value=llm_returned_plan),
        )
        view._run_change_camera(project, scene1, plan1, camera_shot=None, camera_movement_override="tracking")
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan.for_scene(1).camera_movement == "tracking", timeout=15.0)

    assert view._current_visual_plan.for_scene(1).camera_movement == "tracking"


def test_change_camera_failure_shows_error_and_preserves_plan(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene1 = storyboard.scenes[0]
    plan1 = view._current_visual_plan.for_scene(1)
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "regenerate_scene_camera", MagicMock(return_value=None))
        view._run_change_camera(project, scene1, plan1, camera_shot="macro", camera_movement_override=None)
        _pump(
            view.winfo_toplevel(),
            lambda: any("couldn't change" in t.lower() for t in _collect_texts(view._status_container)),
            timeout=15.0,
        )

    assert view._current_visual_plan.for_scene(1) == plan1  # unchanged on failure


# --- CHANGE STYLE ---------------------------------------------------------------------


def test_run_change_style_updates_only_that_scenes_plan(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene1 = storyboard.scenes[0]
    plan1 = view._current_visual_plan.for_scene(1)
    original_plan_scene2 = view._current_visual_plan.for_scene(2)
    new_plan = dataclasses.replace(plan1, main_visual_prompt="cinematic dramatic version", lighting="dramatic")

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "regenerate_scene_style", MagicMock(return_value=new_plan))
        view._run_change_style(project, scene1, plan1, "cinematic")
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan.for_scene(1).lighting == "dramatic", timeout=15.0)

    assert view._current_visual_plan.for_scene(1).lighting == "dramatic"
    assert view._current_visual_plan.for_scene(2) == original_plan_scene2


# --- CHANGE VISUAL --------------------------------------------------------------------


def test_run_change_visual_produces_a_different_interpretation(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene1 = storyboard.scenes[0]
    plan1 = view._current_visual_plan.for_scene(1)
    new_plan = dataclasses.replace(
        plan1, environment="a sunny kitchen", subject_action="pouring coffee",
        main_visual_prompt="completely different interpretation",
    )

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "regenerate_scene_visual_plan", MagicMock(return_value=new_plan))
        view._on_change_visual_clicked(project, storyboard, scene1)
        _pump(
            view.winfo_toplevel(),
            lambda: view._current_visual_plan.for_scene(1).environment == "a sunny kitchen",
            timeout=15.0,
        )

    assert view._current_visual_plan.for_scene(1).environment == "a sunny kitchen"
    assert scene1.on_screen_text == "3 ways to start"  # Scene text never touched


# --- quality score / variety re-run after regeneration --------------------------------


def test_quality_score_updates_after_change_style(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene1 = storyboard.scenes[0]
    plan1 = view._current_visual_plan.for_scene(1)
    # A low-quality replacement (vague/short prompt, no environment).
    from jarvis.reel_generator.visual_quality import score_scene_plan
    low_quality_plan = dataclasses.replace(
        plan1, main_visual_prompt="a room", environment="", subject_action="", visual_quality_score=score_scene_plan(
            dataclasses.replace(plan1, main_visual_prompt="a room", environment="", subject_action=""),
        ).score,
    )

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "regenerate_scene_style", MagicMock(return_value=low_quality_plan))
        view._run_change_style(project, scene1, plan1, "minimal")
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan.for_scene(1).main_visual_prompt == "a room", timeout=15.0)

    texts = _collect_texts(view._storyboard_container)
    new_score = view._current_visual_plan.for_scene(1).visual_quality_score
    assert any(f"{new_score}/10" in t for t in texts)


def test_variety_warning_appears_after_change_camera_creates_a_repeat(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene1 = storyboard.scenes[0]
    plan1 = view._current_visual_plan.for_scene(1)
    plan2 = view._current_visual_plan.for_scene(2)
    # Force scene 1 to match scene 2's own camera_shot/camera_movement -
    # a real, detectable repeat that didn't exist before this change.
    new_plan = dataclasses.replace(plan1, camera_shot=plan2.camera_shot, camera_movement=plan2.camera_movement)

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "regenerate_scene_camera", MagicMock(return_value=new_plan))
        view._run_change_camera(project, scene1, plan1, camera_shot=plan2.camera_shot, camera_movement_override=None)
        _pump(
            view.winfo_toplevel(),
            lambda: view._current_visual_plan.for_scene(1).camera_shot == plan2.camera_shot,
            timeout=15.0,
        )

    texts = _collect_texts(view._storyboard_container)
    assert any("Low visual variety" in t for t in texts)


# --- RESET SCENE ------------------------------------------------------------------------


def test_reset_scene_restores_original_text_after_edit(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    scene2 = storyboard.scenes[1]
    original_text = scene2.on_screen_text

    # Edit scene 2's text directly (simulating EDIT SCENE's own save).
    edited_scene = dataclasses.replace(scene2, on_screen_text="EDITED TEXT")
    edited_storyboard = Storyboard(scenes=tuple(
        edited_scene if s.number == 2 else s for s in storyboard.scenes
    ))
    db.save_storyboard(project.project_id, dataclasses.asdict(edited_storyboard))
    view._current_storyboard = edited_storyboard
    assert view._current_storyboard.scenes[1].on_screen_text == "EDITED TEXT"

    view._on_reset_scene_clicked(project, edited_storyboard, edited_scene)

    assert view._current_storyboard is not None
    restored_scene = next(s for s in view._current_storyboard.scenes if s.number == 2)
    assert restored_scene.on_screen_text == original_text
    # Scene 1 and 3 (never edited) are completely untouched.
    assert view._current_storyboard.scenes[0].on_screen_text == storyboard.scenes[0].on_screen_text
    assert view._current_storyboard.scenes[2].on_screen_text == storyboard.scenes[2].on_screen_text


def test_reset_scene_restores_visual_plan_too(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    storyboard = _approve_with_fake_storyboard(view, project, monkeypatch)
    _generate_fake_visual_plan(view, project, storyboard, monkeypatch)

    scene1 = storyboard.scenes[0]
    original_camera_shot = view._current_visual_plan.for_scene(1).camera_shot

    # CHANGE CAMERA edits scene 1's plan.
    changed_plan = dataclasses.replace(view._current_visual_plan.for_scene(1), camera_shot="macro")
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "regenerate_scene_camera", MagicMock(return_value=changed_plan))
        view._run_change_camera(project, scene1, view._current_visual_plan.for_scene(1), camera_shot="macro", camera_movement_override=None)
        _pump(view.winfo_toplevel(), lambda: view._current_visual_plan.for_scene(1).camera_shot == "macro", timeout=15.0)
    assert view._current_visual_plan.for_scene(1).camera_shot == "macro"

    view._on_reset_scene_clicked(project, storyboard, scene1)

    assert view._current_visual_plan is not None
    assert view._current_visual_plan.for_scene(1).camera_shot == original_camera_shot


def test_reset_scene_without_any_snapshot_shows_clear_error(root, monkeypatch):
    view = ReelGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    # No storyboard/snapshot exists yet at all.
    scene = Scene(number=1, start_seconds=0, end_seconds=5, segment_kind="hook", voice_text="x", on_screen_text="x", visual_description="x")
    view._on_reset_scene_clicked(project, Storyboard(scenes=(scene,)), scene)
    texts = _collect_texts(view._status_container)
    assert any("no original version" in t.lower() for t in texts)
