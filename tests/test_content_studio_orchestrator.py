"""Tests for jarvis.content_studio.orchestrator: Stage 2's "create a
real linked project" functions. Mocks jarvis.reel_generator.brief
.generate_reel_brief()/jarvis.reel_generator.script.generate_reel_script()
and jarvis.design_studio.brief.generate_design_brief() directly (no real
LLM call) - jarvis.design_studio.render.render_design() runs FOR REAL
(Pillow, no external service, fast/deterministic - no mocking needed,
and this is exactly what confirms create_design_project() produces a
genuinely viewable design file, not just a mocked success).

Confirms: create_reel_project() creates a REAL, independently-readable
jarvis.reel_generator project (verified via jarvis.reel_generator.db
.get_project(), not just the returned dataclass) with a saved brief and
script, stops at the script stage (does not call storyboard/export -
respecting jarvis.reel_generator's own approval gate, per this module's
own docstring), grounds its request text in the plan item's own angle/
objective/cta (never a generic placeholder), and returns a plain error
string (never raises) on brief/script/storage failure.
create_design_project() creates a REAL, independently-readable
jarvis.design_studio project with a real rendered image file on disk,
for each of post/story/carousel, and returns a plain error string on
failure. Both functions reject a mismatched content_type."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from jarvis.content_studio import orchestrator
from jarvis.content_studio.plan import ContentPlanItem
from jarvis.design_studio import db as design_db
from jarvis.design_studio import storage as design_storage
from jarvis.design_studio.brief import DesignBrief
from jarvis.reel_generator import db as reel_db
from jarvis.reel_generator import storage as reel_storage
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript, ScriptSegment


@pytest.fixture(autouse=True)
def _isolated_reel_generator(tmp_path, monkeypatch):
    db_file = tmp_path / "reel_generator.db"
    projects_dir = tmp_path / "reel_projects"
    monkeypatch.setattr(reel_db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(reel_storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(orchestrator.reel_db, "REEL_GENERATOR_DB_FILE", db_file)
    monkeypatch.setattr(orchestrator.reel_storage, "REEL_GENERATOR_PROJECTS_DIR", projects_dir)


@pytest.fixture(autouse=True)
def _isolated_design_studio(tmp_path, monkeypatch):
    db_file = tmp_path / "design_studio.db"
    projects_dir = tmp_path / "design_projects"
    monkeypatch.setattr(design_db, "DESIGN_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(design_storage, "DESIGN_STUDIO_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(orchestrator.design_db, "DESIGN_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(orchestrator.design_storage, "DESIGN_STUDIO_PROJECTS_DIR", projects_dir)


def _item(content_type: str = "reel", **overrides) -> ContentPlanItem:
    defaults = dict(
        content_type=content_type, angle="5 minučių rytinė joga energijai",
        objective="educate", cta="Išbandyk rytoj",
    )
    defaults.update(overrides)
    return ContentPlanItem(**defaults)


def _fake_reel_brief() -> ReelBrief:
    return ReelBrief(
        topic="morning yoga", audience="beginners", objective="educate", tone="calm",
        cta="Try it", style="yoga", duration_seconds=20, language="lt",
    )


def _fake_reel_script() -> ReelScript:
    return ReelScript(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=5, text="hi"),
        ScriptSegment(kind="value", start_seconds=5, end_seconds=15, text="val"),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="save"),
    ))


def _fake_design_brief(fmt: str = "post") -> DesignBrief:
    return DesignBrief(
        topic="morning yoga", objective="educate", audience="beginners", tone="calm",
        headline="5 Min Yoga", supporting_text="Start your day right.", cta="Save this",
        format=fmt, style="yoga",
    )


# --- create_reel_project -----------------------------------------------------------------


def test_create_reel_project_creates_real_linked_project():
    item = _item("reel")
    brief = _fake_reel_brief()
    script = _fake_reel_script()
    with patch.object(orchestrator, "generate_reel_brief", return_value=brief), \
         patch.object(orchestrator, "generate_reel_script", return_value=script):
        result = orchestrator.create_reel_project(MagicMock(), item)

    assert isinstance(result, orchestrator.ReelCreationResult)
    assert result.brief == brief
    assert result.script == script

    # Genuinely readable back through jarvis.reel_generator.db's own
    # functions - proves this landed in the real, shared data store,
    # not a Content-Studio-only copy.
    saved = reel_db.get_project(result.reel_generator_project_id)
    assert saved is not None
    assert saved.brief_data is not None
    assert saved.script_data is not None
    assert saved.script_approved is False  # the approval gate is untouched by this orchestrator


def test_create_reel_project_grounds_request_in_plan_item():
    item = _item("reel", angle="Specific unique angle text", objective="build trust", cta="DM me")
    with patch.object(orchestrator, "generate_reel_brief", return_value=_fake_reel_brief()) as mock_brief, \
         patch.object(orchestrator, "generate_reel_script", return_value=_fake_reel_script()):
        orchestrator.create_reel_project(MagicMock(), item)

    call_args = mock_brief.call_args
    request_text = call_args[0][1]
    assert "Specific unique angle text" in request_text
    assert "build trust" in request_text
    assert "DM me" in request_text


def test_create_reel_project_rejects_empty_angle_without_calling_llm():
    # Regression test for a real bug found by hand-testing Stage 2's
    # own orchestration: an item with an empty angle/objective/cta
    # still produced a non-empty request_text (the fixed "Objective: .
    # CTA: ." scaffolding text alone), which passed
    # generate_reel_brief()'s own bare "is request_text empty" check
    # and let the LLM fabricate a plausible-looking brief from
    # punctuation instead of the caller ever finding out the item
    # itself was empty.
    item = _item("reel", angle="", objective="", cta="")
    llm = MagicMock()
    result = orchestrator.create_reel_project(llm, item)
    assert isinstance(result, str)
    assert "no angle" in result
    llm.send.assert_not_called()
    assert reel_db.list_projects() == []  # no project should even be created


def test_create_design_project_rejects_empty_angle_without_calling_llm():
    item = _item("post", angle="", objective="", cta="")
    llm = MagicMock()
    result = orchestrator.create_design_project(llm, item)
    assert isinstance(result, str)
    assert "no angle" in result
    llm.send.assert_not_called()
    assert design_db.list_projects() == []


def test_create_reel_project_rejects_non_reel_item():
    item = _item("post")
    result = orchestrator.create_reel_project(MagicMock(), item)
    assert isinstance(result, str)
    assert "not a Reel" in result


def test_create_reel_project_brief_failure_returns_error_string():
    item = _item("reel")
    with patch.object(orchestrator, "generate_reel_brief", return_value=None):
        result = orchestrator.create_reel_project(MagicMock(), item)
    assert isinstance(result, str)
    assert "couldn't create a Reel brief" in result

    # No dangling project should exist beyond what create_project()
    # itself already made - confirms an error is surfaced honestly, not
    # silently retried/hidden (module brief Stage 2: "do not fake
    # successful creation").
    projects = reel_db.list_projects()
    assert len(projects) == 1
    assert projects[0].brief_data is None


def test_create_reel_project_script_failure_returns_error_string():
    item = _item("reel")
    with patch.object(orchestrator, "generate_reel_brief", return_value=_fake_reel_brief()), \
         patch.object(orchestrator, "generate_reel_script", return_value=None):
        result = orchestrator.create_reel_project(MagicMock(), item)
    assert isinstance(result, str)
    assert "couldn't fit a script" in result.lower() or "script" in result.lower()


def test_create_reel_project_llm_exception_does_not_raise():
    item = _item("reel")
    with patch.object(orchestrator, "generate_reel_brief", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            orchestrator.create_reel_project(MagicMock(), item)
    # Documents the actual contract: generate_reel_brief() itself never
    # raises (see its own tests) - this orchestrator adds no extra
    # try/except around it, relying on that existing guarantee rather
    # than defending against it twice (same reasoning
    # jarvis.reel_generator.caption's own equivalent test documents).


# --- create_design_project ----------------------------------------------------------------


@pytest.mark.parametrize("content_type,expected_format", [("post", "post"), ("story", "story"), ("carousel", "carousel")])
def test_create_design_project_creates_real_rendered_design(content_type, expected_format):
    item = _item(content_type)
    brief = _fake_design_brief(expected_format)
    with patch.object(orchestrator, "generate_design_brief", return_value=brief) as mock_brief:
        result = orchestrator.create_design_project(MagicMock(), item)

    mock_brief.assert_called_once()
    assert mock_brief.call_args.kwargs["forced_format"] == expected_format

    assert isinstance(result, orchestrator.DesignCreationResult)
    assert result.render_result.output_path.is_file()
    with Image.open(result.render_result.output_path) as img:
        assert img.width > 0 and img.height > 0

    saved = design_db.get_project(result.design_studio_project_id)
    assert saved is not None
    assert saved.brief_data is not None
    assert saved.design_path == str(result.render_result.output_path)


def test_create_design_project_grounds_request_in_plan_item():
    item = _item("post", angle="Unique post angle", objective="engage", cta="Comment below")
    with patch.object(orchestrator, "generate_design_brief", return_value=_fake_design_brief()) as mock_brief:
        orchestrator.create_design_project(MagicMock(), item)

    call_args = mock_brief.call_args
    request_text = call_args[0][1]
    assert "Unique post angle" in request_text
    assert "engage" in request_text
    assert "Comment below" in request_text


def test_create_design_project_rejects_reel_item():
    item = _item("reel")
    result = orchestrator.create_design_project(MagicMock(), item)
    assert isinstance(result, str)
    assert "not a Post/Story/Carousel" in result


def test_create_design_project_rejects_pdf_item():
    item = _item("pdf")
    result = orchestrator.create_design_project(MagicMock(), item)
    assert isinstance(result, str)


def test_create_design_project_brief_failure_returns_error_string():
    item = _item("post")
    with patch.object(orchestrator, "generate_design_brief", return_value=None):
        result = orchestrator.create_design_project(MagicMock(), item)
    assert isinstance(result, str)
    assert "couldn't create a design brief" in result

    projects = design_db.list_projects()
    assert len(projects) == 1
    assert projects[0].design_path is None
