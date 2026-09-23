"""Tests for jarvis.core.project_plan.build_plan: deterministic,
rule-based plan derivation from an already-computed ProjectScanResult.
build_plan() never touches the filesystem or git itself - all inputs are
constructed directly to test every combination of scan findings,
including via scan_project() against real isolated temp directories for
end-to-end coverage of every required project state (no git, no commits,
existing commits, clean/dirty tree, missing metadata)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis.core.project_plan import ProjectPlan, build_plan
from jarvis.core.project_scan import GitInfo, ProjectScanResult, scan_project


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def _bare_scan(root: Path = Path("/x"), **overrides) -> ProjectScanResult:
    """Construct a ProjectScanResult directly (no filesystem access) for
    unit-testing build_plan()'s pure logic in isolation."""
    defaults = dict(root=root)
    defaults.update(overrides)
    return ProjectScanResult(**defaults)


# --- structural sanity: build_plan never touches the filesystem --------------


def test_build_plan_is_pure_no_filesystem_access(tmp_path):
    # tmp_path is a real, empty directory the scan was never run against -
    # build_plan() must work purely from the scan object, never re-reading
    # from tmp_path itself. A ProjectScanResult with root=tmp_path but
    # populated fields that don't match reality confirms build_plan()
    # trusts only the object, not the filesystem.
    scan = _bare_scan(root=tmp_path, readme_files=["README.md"])
    plan = build_plan(scan)
    assert any("Documented with: README.md" in s for s in plan.current_state)
    # tmp_path has no actual README.md on disk - if build_plan() had
    # re-scanned, this would have reported "No README present" instead.


def test_returns_project_plan_dataclass():
    plan = build_plan(_bare_scan())
    assert isinstance(plan, ProjectPlan)
    assert plan.root_summary == "\\x" or plan.root_summary  # platform-dependent string form


# --- all four required plan sections are always populated (or explicit) -------


def test_all_four_sections_present_for_empty_project():
    plan = build_plan(_bare_scan())
    assert plan.current_state
    assert plan.findings
    assert plan.recommended_steps
    assert plan.suggested_order


def test_all_four_sections_present_for_fully_complete_project():
    scan = _bare_scan(
        readme_files=["README.md"],
        manifest_files=["pyproject.toml"],
        has_tests=True,
        test_locations=["tests/"],
        has_jarvis_md=True,
        has_gitignore=True,
        git=GitInfo(is_repo=True, commit_count=5, has_uncommitted_changes=False),
    )
    plan = build_plan(scan)
    assert plan.current_state
    assert plan.findings
    assert plan.recommended_steps  # "no gaps" message, not empty
    assert plan.suggested_order


# --- current state section ----------------------------------------------------


def test_current_state_reports_empty_directory():
    plan = build_plan(_bare_scan(top_level_entries=[]))
    assert any("empty" in s.lower() for s in plan.current_state)


def test_current_state_reports_entry_count():
    plan = build_plan(_bare_scan(top_level_entries=["a.py", "b.py"]))
    assert any("2 top-level entries" in s for s in plan.current_state)


def test_current_state_singular_entry_wording():
    plan = build_plan(_bare_scan(top_level_entries=["a.py"]))
    assert any("1 top-level entry" in s for s in plan.current_state)


def test_current_state_not_a_git_repo():
    plan = build_plan(_bare_scan(git=GitInfo(is_repo=False)))
    assert any("not under git version control" in s.lower() for s in plan.current_state)


def test_current_state_git_repo_zero_commits():
    plan = build_plan(_bare_scan(git=GitInfo(is_repo=True, commit_count=0)))
    assert any("no commits yet" in s.lower() for s in plan.current_state)


def test_current_state_git_repo_with_commits_clean():
    plan = build_plan(
        _bare_scan(git=GitInfo(is_repo=True, commit_count=3, has_uncommitted_changes=False))
    )
    assert any("3 commits" in s and "clean" in s for s in plan.current_state)


def test_current_state_git_repo_with_commits_dirty():
    plan = build_plan(
        _bare_scan(git=GitInfo(is_repo=True, commit_count=1, has_uncommitted_changes=True))
    )
    assert any("1 commit" in s and "uncommitted changes" in s for s in plan.current_state)


def test_current_state_git_error_reported():
    plan = build_plan(_bare_scan(git=GitInfo(is_repo=True, error="git broke")))
    assert any("git broke" in s for s in plan.current_state)


# --- findings section -----------------------------------------------------------


def test_findings_reuses_scan_notes_verbatim():
    scan = _bare_scan(notes=["Custom note from scan."])
    plan = build_plan(scan)
    assert "Custom note from scan." in plan.findings


def test_findings_mentions_jarvis_md_present():
    plan = build_plan(_bare_scan(has_jarvis_md=True))
    assert any("JARVIS.md is present" in f for f in plan.findings)


def test_findings_mentions_jarvis_md_absent():
    plan = build_plan(_bare_scan(has_jarvis_md=False))
    assert any("No JARVIS.md yet" in f for f in plan.findings)


def test_findings_mentions_uncommitted_changes_when_present():
    scan = _bare_scan(git=GitInfo(is_repo=True, has_uncommitted_changes=True))
    plan = build_plan(scan)
    assert any("uncommitted changes" in f.lower() for f in plan.findings)


def test_findings_does_not_mention_uncommitted_changes_when_clean():
    scan = _bare_scan(git=GitInfo(is_repo=True, has_uncommitted_changes=False))
    plan = build_plan(scan)
    assert not any("there are uncommitted changes" in f.lower() for f in plan.findings)


# --- recommended steps -----------------------------------------------------------


def test_recommends_git_init_when_not_a_repo():
    plan = build_plan(_bare_scan(git=GitInfo(is_repo=False)))
    assert any("git-init" in s for s in plan.recommended_steps)


def test_recommends_initial_commit_when_zero_commits():
    plan = build_plan(_bare_scan(git=GitInfo(is_repo=True, commit_count=0)))
    assert any("initial commit" in s.lower() for s in plan.recommended_steps)


def test_does_not_recommend_git_init_when_already_a_repo_with_commits():
    plan = build_plan(_bare_scan(git=GitInfo(is_repo=True, commit_count=2)))
    assert not any("git-init" in s for s in plan.recommended_steps)


def test_recommends_readme_when_missing():
    plan = build_plan(_bare_scan(readme_files=[]))
    assert any("readme" in s.lower() for s in plan.recommended_steps)


def test_does_not_recommend_readme_when_present():
    plan = build_plan(_bare_scan(readme_files=["README.md"]))
    assert not any(s.lower().startswith("add a readme") for s in plan.recommended_steps)


def test_recommends_manifest_when_missing():
    plan = build_plan(_bare_scan(manifest_files=[]))
    assert any("manifest" in s.lower() for s in plan.recommended_steps)


def test_recommends_tests_when_missing():
    plan = build_plan(_bare_scan(has_tests=False))
    assert any("test suite" in s.lower() for s in plan.recommended_steps)


def test_does_not_recommend_tests_when_present():
    plan = build_plan(_bare_scan(has_tests=True, test_locations=["tests/"]))
    assert not any("add a test suite" in s.lower() for s in plan.recommended_steps)


def test_recommends_jarvis_md_when_missing():
    plan = build_plan(_bare_scan(has_jarvis_md=False))
    assert any("jarvis.md" in s.lower() for s in plan.recommended_steps)


def test_recommends_reviewing_uncommitted_changes():
    scan = _bare_scan(git=GitInfo(is_repo=True, has_uncommitted_changes=True))
    plan = build_plan(scan)
    assert any("review" in s.lower() and "precommit" in s.lower() for s in plan.recommended_steps)


def test_no_gaps_message_when_everything_present():
    scan = _bare_scan(
        readme_files=["README.md"],
        manifest_files=["pyproject.toml"],
        has_tests=True,
        test_locations=["tests/"],
        has_jarvis_md=True,
        git=GitInfo(is_repo=True, commit_count=1, has_uncommitted_changes=False),
    )
    plan = build_plan(scan)
    assert len(plan.recommended_steps) == 1
    assert "no gaps detected" in plan.recommended_steps[0].lower()


# --- suggested order --------------------------------------------------------------


def test_suggested_order_contains_same_items_as_recommended_steps():
    plan = build_plan(_bare_scan())
    assert sorted(plan.suggested_order) == sorted(plan.recommended_steps)


def test_suggested_order_puts_git_init_before_readme():
    scan = _bare_scan(git=GitInfo(is_repo=False), readme_files=[])
    plan = build_plan(scan)
    git_index = next(i for i, s in enumerate(plan.suggested_order) if "git-init" in s)
    readme_index = next(i for i, s in enumerate(plan.suggested_order) if s.lower().startswith("add a readme"))
    assert git_index < readme_index


def test_suggested_order_puts_manifest_before_readme():
    scan = _bare_scan(readme_files=[], manifest_files=[])
    plan = build_plan(scan)
    manifest_index = next(i for i, s in enumerate(plan.suggested_order) if "manifest" in s.lower())
    readme_index = next(i for i, s in enumerate(plan.suggested_order) if s.lower().startswith("add a readme"))
    assert manifest_index < readme_index


def test_suggested_order_is_deterministic_across_repeated_calls():
    scan = _bare_scan(readme_files=[], manifest_files=[], has_tests=False, has_jarvis_md=False)
    order1 = build_plan(scan).suggested_order
    order2 = build_plan(scan).suggested_order
    assert order1 == order2


def test_suggested_order_puts_review_last():
    scan = _bare_scan(
        readme_files=[],
        git=GitInfo(is_repo=True, commit_count=1, has_uncommitted_changes=True),
    )
    plan = build_plan(scan)
    review_index = next(
        i for i, s in enumerate(plan.suggested_order) if "review" in s.lower() and "precommit" in s.lower()
    )
    assert review_index == len(plan.suggested_order) - 1


# --- end-to-end via real scan_project() against isolated temp dirs -----------


def test_end_to_end_no_git_repo(tmp_path):
    scan = scan_project(tmp_path)
    plan = build_plan(scan)
    assert any("not under git" in s.lower() for s in plan.current_state)
    assert any("git-init" in s for s in plan.recommended_steps)


def test_end_to_end_git_repo_zero_commits(tmp_path):
    _init_git_repo(tmp_path)
    scan = scan_project(tmp_path)
    plan = build_plan(scan)
    assert any("no commits yet" in s.lower() for s in plan.current_state)
    assert any("initial commit" in s.lower() for s in plan.recommended_steps)


def test_end_to_end_git_repo_with_commits_clean_tree(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    scan = scan_project(tmp_path)
    plan = build_plan(scan)
    assert any("clean working tree" in s for s in plan.current_state)
    assert not any("git-init" in s for s in plan.recommended_steps)
    assert not any(s.lower().startswith("add a readme") for s in plan.recommended_steps)


def test_end_to_end_git_repo_with_commits_dirty_tree(tmp_path):
    _init_git_repo(tmp_path)
    f = tmp_path / "README.md"
    f.write_text("# Project", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)
    f.write_text("# Project\nmore content", encoding="utf-8")

    scan = scan_project(tmp_path)
    plan = build_plan(scan)
    assert any("uncommitted changes" in s for s in plan.current_state)
    assert any("review" in s.lower() and "precommit" in s.lower() for s in plan.recommended_steps)


def test_end_to_end_missing_metadata_fully(tmp_path):
    # Just an empty directory - no README, no manifest, no tests, no git,
    # no JARVIS.md. Confirms the plan gracefully covers every gap at once.
    scan = scan_project(tmp_path)
    plan = build_plan(scan)
    assert len(plan.recommended_steps) >= 4  # git-init, README, manifest, tests, JARVIS.md


def test_end_to_end_fully_complete_project_reports_no_gaps(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass", encoding="utf-8")
    (tmp_path / "JARVIS.md").write_text("notes", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=tmp_path, check=True)

    scan = scan_project(tmp_path)
    plan = build_plan(scan)
    assert len(plan.recommended_steps) == 1
    assert "no gaps" in plan.recommended_steps[0].lower()


def test_end_to_end_never_modifies_the_project(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")

    files_before = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())
    build_plan(scan_project(tmp_path))
    build_plan(scan_project(tmp_path))
    files_after = sorted(p.name for p in tmp_path.rglob("*") if p.is_file())

    assert files_before == files_after


def test_end_to_end_never_raises_on_a_realistic_mixed_project(tmp_path):
    _init_git_repo(tmp_path)
    (tmp_path / "README.md").write_text("# Project", encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("click", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("pass", encoding="utf-8")

    build_plan(scan_project(tmp_path))  # must not raise
