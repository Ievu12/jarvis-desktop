"""Tests for jarvis.gui.views.story_generator.dashboard.StoryGeneratorView:
widget wiring and rendering, using a real (withdrawn) CTk root with
jarvis.story_generator.db/.storage redirected to a per-test tmp_path.
jarvis.story_generator.structure.generate_story_structure and
.story_scenes.generate_story_scenes are mocked for these tests (no real
LLM call) - same pattern as tests/test_gui_reel_generator_dashboard.py
(see that file's own docstring for the full rationale).

Confirms: button/status states, routing GenerationTaskResults by
(kind, self), the tuple-vs-error-string unpacking safety, structure is
rendered after creation, APPROVE STORY persists approval to the DB and
structurally triggers scene generation, scene visuals are REAL rendered
images (via AI Reel Generator's own render_all_scenes(), called
unmodified), the final export is a REAL MP4 (via AI Reel Generator's
own export_reel_video(), called unmodified), and reopening a saved
Story from Recent Stories restores its structure/approval/scenes/export
state without calling the LLM again."""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from jarvis.gui.views.story_generator import dashboard as dashboard_module
from jarvis.gui.views.story_generator.dashboard import StoryGeneratorView
from jarvis.reel_generator import image_generation
from jarvis.reel_generator.storyboard import Scene, Storyboard
from jarvis.story_generator import db, storage
from jarvis.story_generator.structure import BEAT_NAMES, StoryBeat, StoryStructure
from jarvis.story_generator.visual_plan import ScenePlan, TextCue, VisualPlan


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "story_generator.db"
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(db, "STORY_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(storage, "STORY_GENERATOR_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(dashboard_module.db, "STORY_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(dashboard_module.storage, "STORY_GENERATOR_PROJECTS_DIR", projects_dir)


@pytest.fixture(autouse=True)
def _no_real_image_api_calls(monkeypatch):
    # _fake_visual_plan()'s ScenePlan entries leave visual_source unset,
    # defaulting to ScenePlan's own DEFAULT_VISUAL_SOURCE
    # ("ai_generated") - one of scene_render's own
    # _REAL_IMAGE_VISUAL_SOURCES. render_all_scenes() runs unmodified in
    # these tests (see module docstring), so if a real OPENAI_API_KEY
    # happens to be configured in the environment this suite runs in,
    # every test that renders scenes would otherwise make real, slow,
    # costly OpenAI Images API calls. Same fix as
    # tests/test_reel_generator_scene_render.py's own identical fixture.
    monkeypatch.setattr(image_generation, "is_configured", lambda: False)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_child_views(root):
    """Same reasoning as
    tests/test_gui_reel_generator_dashboard.py's own identical fixture:
    keeps live Tcl widget count roughly constant across this file's
    many constructed views, avoiding Windows's own finite cap on live
    Tcl menu handles per process."""
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
        if isinstance(w, ctk.CTkTextbox):
            try:
                texts.append(w.get("1.0", "end").strip())
            except Exception:
                pass
        for c in w.winfo_children():
            _walk(c)

    _walk(widget)
    return texts


def _fake_structure(**overrides: Any) -> StoryStructure:
    beats = overrides.pop("beats", None) or tuple(
        StoryBeat(kind=kind, text=f"This is the {kind} of a founder's failed launch.") for kind in BEAT_NAMES
    )
    defaults: dict[str, Any] = dict(story_type="personal", language="en", beats=beats)
    defaults.update(overrides)
    return StoryStructure(**defaults)


def _fake_storyboard() -> Storyboard:
    scenes = []
    cursor = 0.0
    for i, kind in enumerate(BEAT_NAMES, start=1):
        scenes.append(Scene(
            number=i, start_seconds=cursor, end_seconds=cursor + 3.0, segment_kind=kind,
            voice_text=f"Voice for {kind}", on_screen_text=kind.upper(),
            visual_description="a quiet room", mood="reflective",
            transition="fade" if i < len(BEAT_NAMES) else "end",
        ))
        cursor += 3.0
    return Storyboard(scenes=tuple(scenes))


def _fake_visual_plan() -> VisualPlan:
    plans = tuple(
        ScenePlan(
            scene_number=i, visual_type="photo_style", main_visual_prompt=f"a scene for {kind}",
            supporting_visuals=(), text_cues=(
                TextCue(text=kind.upper(), position="top", start_seconds=0, end_seconds=1.5, animation="fade_in"),
            ),
            sticker="thinking" if i == 1 else "none", sticker_start_seconds=0.5,
            motion="zoom_in", emotion="reflective", pacing="medium", lighting="warm",
        )
        for i, kind in enumerate(BEAT_NAMES, start=1)
    )
    return VisualPlan(scenes=plans)


def _fake_scenes_result() -> tuple[Storyboard, VisualPlan]:
    return _fake_storyboard(), _fake_visual_plan()


# --- construction ------------------------------------------------------------------------


def test_builds_with_llm(root):
    view = StoryGeneratorView(root, llm=MagicMock())
    assert isinstance(view, ctk.CTkFrame)


def test_builds_without_llm(root):
    view = StoryGeneratorView(root, llm=None)
    assert isinstance(view, ctk.CTkFrame)


def test_refresh_does_not_raise(root):
    view = StoryGeneratorView(root, llm=MagicMock())
    view.refresh()


def test_shows_no_stories_message_when_empty(root):
    view = StoryGeneratorView(root, llm=MagicMock())
    texts = _collect_texts(view._recent_stories_container)
    assert any("No stories yet" in t for t in texts)


# --- generate story structure flow (mocked) ------------------------------------------------


def test_empty_prompt_shows_error_without_calling_llm(root):
    llm = MagicMock()
    view = StoryGeneratorView(root, llm=llm)
    view._on_generate_structure_clicked()
    texts = _collect_texts(view._status_container)
    assert any("Describe the story" in t for t in texts)
    llm.send.assert_not_called()


def test_no_llm_shows_clear_error(root):
    view = StoryGeneratorView(root, llm=None)
    view._prompt_entry.insert("1.0", "A founder's failed launch.")
    view._on_generate_structure_clicked()
    texts = _collect_texts(view._status_container)
    assert any("not available" in t for t in texts)


def _create_with_fakes(view, monkeypatch, *, structure=None):
    view._prompt_entry.insert("1.0", "A founder's first failed product launch, and how they recovered.")
    structure = structure or _fake_structure()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_story_structure", MagicMock(return_value=structure))
        view._on_generate_structure_clicked()
        _pump(view.winfo_toplevel(), lambda: view._current_project is not None, timeout=45.0)
    return structure


def test_create_click_renders_structure(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)

    structure_texts = _collect_texts(view._structure_container)
    assert any("STORY STRUCTURE" in t for t in structure_texts)
    assert not any("Approved" in t for t in structure_texts)  # not yet approved

    assert view._current_project is not None
    saved = db.get_project(view._current_project.project_id)
    assert saved is not None
    assert saved.structure_data is not None
    assert saved.structure_approved is False


def test_create_structure_failure_shows_error(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some idea")
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_story_structure", MagicMock(return_value=None))
        view._on_generate_structure_clicked()
        _pump(root, lambda: any("couldn't create" in t.lower() for t in _collect_texts(view._status_container)))
    texts = _collect_texts(view._status_container)
    assert any("couldn't create" in t.lower() for t in texts)


def test_create_generation_exception_shows_error_not_crash(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    view._prompt_entry.insert("1.0", "some idea")
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_story_structure", MagicMock(side_effect=RuntimeError("network down")))
        view._on_generate_structure_clicked()
        _pump(root, lambda: any("network down" in t for t in _collect_texts(view._status_container)))
    texts = _collect_texts(view._status_container)
    assert any("network down" in t for t in texts)


# --- approval gate -----------------------------------------------------------------------


def _approve_with_fake_scenes(view, project, structure, monkeypatch):
    storyboard, visual_plan = _fake_scenes_result()
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_story_scenes", MagicMock(return_value=(storyboard, visual_plan)))
        view._on_approve_clicked(project, structure)
        _pump(view.winfo_toplevel(), lambda: view._current_storyboard is not None, timeout=45.0)
    return storyboard, visual_plan


def test_approve_button_persists_approval_and_triggers_scene_generation(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None

    _approve_with_fake_scenes(view, project, structure, monkeypatch)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.structure_approved is True

    texts = _collect_texts(view._structure_container)
    assert any("Approved" in t for t in texts)

    storyboard_texts = _collect_texts(view._storyboard_container)
    assert any("STORYBOARD PREVIEW" in t for t in storyboard_texts)
    assert any("SCENE 1" in t for t in storyboard_texts)


def test_video_is_never_generated_before_approval(root):
    # Scene generation (a prerequisite for the final export) is only
    # ever reachable through _on_generate_scenes_clicked(), itself only
    # ever called from _on_approve_clicked() - there is no path in this
    # view that reaches scene/export generation without going through
    # db.approve_structure() first (module brief requirement 6's gate,
    # enforced structurally, not just by a UI hint).
    view = StoryGeneratorView(root, llm=MagicMock())
    assert view._current_storyboard is None
    view._on_export_clicked()  # must be a safe no-op with nothing approved/generated yet
    assert view._current_storyboard is None


def test_regenerate_structure_produces_new_unapproved_structure(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    structure = view._current_structure
    assert structure is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)
    assert db.get_project(project.project_id).structure_approved is True  # type: ignore[union-attr]

    new_structure = _fake_structure(beats=tuple(
        StoryBeat(kind=kind, text=f"A brand new {kind} beat.") for kind in BEAT_NAMES
    ))
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_story_structure", MagicMock(return_value=new_structure))
        view._on_regenerate_structure_clicked()
        _pump(root, lambda: any("brand new hook" in t for t in _collect_texts(view._structure_container)))

    texts = _collect_texts(view._structure_container)
    assert any("brand new hook" in t for t in texts)
    assert not any("Approved" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.structure_approved is False


# --- reopening from Recent Stories --------------------------------------------------------


def test_open_story_restores_structure_and_approval(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)
    project_id = project.project_id

    view2 = StoryGeneratorView(root, llm=MagicMock())
    generate_mock = MagicMock(side_effect=AssertionError("should not regenerate"))
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "generate_story_structure", generate_mock)
        m.setattr(dashboard_module, "generate_story_scenes", generate_mock)
        view2._open_story(project_id)
        root.update()

    structure_texts = _collect_texts(view2._structure_container)
    assert any("Approved" in t for t in structure_texts)
    storyboard_texts = _collect_texts(view2._storyboard_container)
    assert any("STORYBOARD PREVIEW" in t for t in storyboard_texts)


def test_open_nonexistent_story_shows_error(root):
    view = StoryGeneratorView(root, llm=MagicMock())
    view._open_story("does-not-exist")
    root.update()
    texts = _collect_texts(view._status_container)
    assert any("could no longer be found" in t for t in texts)


def test_open_project_public_wrapper_delegates_to_open_story(root):
    # Same "public entry point for external callers, tested via mock
    # rather than a second full view construction" reasoning as
    # tests/test_gui_reel_generator_dashboard.py
    # ::test_open_project_public_wrapper_delegates_to_open_reel.
    view = StoryGeneratorView(root, llm=MagicMock())
    with patch.object(view, "_open_story") as mock_open_story:
        view.open_project("some-project-id")
    mock_open_story.assert_called_once_with("some-project-id")


def test_created_story_appears_in_recent_stories(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    _create_with_fakes(view, monkeypatch)
    texts = _collect_texts(view._recent_stories_container)
    assert not any("No stories yet" in t for t in texts)


# --- scene visuals / export (real Pillow/ffmpeg execution) --------------------------------


def test_generate_scenes_renders_real_images(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)

    assert len(view._current_visuals) == len(BEAT_NAMES)
    assert all(v.error is None for v in view._current_visuals)
    assert all(v.image_path is not None and v.image_path.is_file() for v in view._current_visuals)


def test_generate_scenes_without_llm_shows_error_not_silence(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    view._llm = None
    view._on_generate_scenes_clicked(project, structure)
    texts = _collect_texts(view._status_container)
    assert any("not available" in t for t in texts)
    assert view._current_storyboard is None


def test_export_without_storyboard_shows_error_not_silence(root):
    view = StoryGeneratorView(root, llm=MagicMock())
    view._on_export_clicked()
    texts = _collect_texts(view._status_container)
    assert any("No scenes are loaded" in t for t in texts)


def test_export_requires_all_scenes_rendered_first(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)
    view._current_visuals = view._current_visuals[:-1]  # simulate one missing scene visual

    view._on_export_clicked()
    texts = _collect_texts(view._status_container)
    assert any("Every scene needs a successfully rendered visual" in t for t in texts)


def test_export_passes_motion_by_scene_from_visual_plan(root, monkeypatch):
    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)

    captured = {}

    def fake_create_export(project, scenes, visuals, motion_by_scene):
        captured["motion_by_scene"] = motion_by_scene
        return "stopped before a real export - only checking the call args"

    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "_create_export", fake_create_export)
        view._on_export_clicked()
        _pump(view.winfo_toplevel(), lambda: "motion_by_scene" in captured, timeout=45.0)

    assert captured["motion_by_scene"] == {i: "zoom_in" for i in range(1, len(BEAT_NAMES) + 1)}


def test_export_produces_real_mp4(root, monkeypatch):
    from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)

    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: len(_collect_texts(view._export_container)) > 0, timeout=60.0)

    texts = _collect_texts(view._export_container)
    assert any("COMPLETED" in t for t in texts)

    saved = db.get_project(project.project_id)
    assert saved is not None
    assert saved.export_path is not None
    assert saved.status == "exported"


def test_storyboard_shows_visual_plan_fields(root, monkeypatch):
    # This feature's own visual-richness requirement 8: the storyboard
    # UI shows visual type, stickers, animation/motion, duration -
    # confirms these actually render as text in the storyboard card,
    # not just stored as unused data.
    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)

    texts = _collect_texts(view._storyboard_container)
    assert any("photo style" in t for t in texts)
    assert any("zoom in" in t for t in texts)
    assert any("\U0001F914 thinking" in t for t in texts)  # scene 1's sticker


def test_open_story_restores_export(root, monkeypatch):
    from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    view = StoryGeneratorView(root, llm=MagicMock())
    structure = _create_with_fakes(view, monkeypatch)
    project = view._current_project
    assert project is not None
    _approve_with_fake_scenes(view, project, structure, monkeypatch)
    view._on_export_clicked()
    _pump(view.winfo_toplevel(), lambda: len(_collect_texts(view._export_container)) > 0, timeout=60.0)
    project_id = project.project_id

    view2 = StoryGeneratorView(root, llm=MagicMock())
    view2._open_story(project_id)
    root.update()
    texts = _collect_texts(view2._export_container)
    assert any("COMPLETED" in t for t in texts)
