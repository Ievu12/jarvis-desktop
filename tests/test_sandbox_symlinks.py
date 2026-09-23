"""Symlink-escape tests for the sandbox gate.

These tests create a symlink INSIDE JARVIS_ROOT that points to a target
OUTSIDE it, to prove that resolving through the symlink is still caught by
the boundary check (the resolver must follow symlinks before comparing
against JARVIS_ROOT, per jarvis.core.sandbox._resolve).

The only path ever created outside JARVIS_ROOT is a throwaway file inside
the OS temp directory (via tempfile), never a real project directory. That
outside file is read-only data used as a symlink target - it is never
written to, and everything created by these tests (symlink + temp file) is
cleaned up in a fixture teardown.

Symlink creation on Windows can require Developer Mode or admin privileges.
Tests that can't create a symlink in this environment are skipped rather
than failed, since that's an environment limitation, not a sandbox bug.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.core.sandbox import SandboxViolation, check, request_outside_access, resolve_in_sandbox


def _make_dir_link(link_path: Path, target_dir: Path) -> None:
    """Create a directory reparse point at link_path -> target_dir.

    Tries a true symlink first (requires Developer Mode / admin on
    Windows); falls back to a Windows junction, which needs no special
    privilege and is sufficient to test path-resolution escape since
    pathlib.resolve() follows junctions the same way it follows symlinks.
    """
    try:
        link_path.symlink_to(target_dir, target_is_directory=True)
        return
    except (OSError, NotImplementedError):
        pass

    if os.name == "nt":
        import subprocess

        result = subprocess.run(
            [
                "cmd",
                "/c",
                "mklink",
                "/J",
                str(link_path),
                str(target_dir),
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and link_path.exists():
            return
        pytest.skip(f"Cannot create symlink or junction in this environment: {result.stderr}")
    else:
        pytest.skip("Cannot create symlinks in this environment")


@pytest.fixture
def outside_target():
    """A real file outside JARVIS_ROOT, living in the OS temp dir."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="jarvis_symlink_test_"))
    target_file = tmp_dir / "outside_secret.txt"
    target_file.write_text("this lives outside the sandbox", encoding="utf-8")
    yield target_file
    target_file.unlink(missing_ok=True)
    tmp_dir.rmdir()


@pytest.fixture
def symlink_in_sandbox(outside_target):
    """A directory link created INSIDE JARVIS_ROOT whose target dir is
    OUTSIDE it; the test paths reach the file through that link, which is
    equivalent (for path-resolution purposes) to symlinking the file
    itself and works even without symlink privilege via a junction."""
    link_dir = JARVIS_ROOT / ".jarvis" / "symlink_test_link_dir"
    link_dir.parent.mkdir(parents=True, exist_ok=True)
    _make_dir_link(link_dir, outside_target.parent)
    yield link_dir / outside_target.name
    _remove_dir_link(link_dir)


@pytest.fixture
def symlinked_dir_in_sandbox(outside_target):
    """A directory symlink/junction INSIDE JARVIS_ROOT pointing to an
    OUTSIDE dir, used to prove a file reached *through* a symlinked
    directory is also caught, not just a direct file symlink."""
    outside_dir = outside_target.parent
    link_dir = JARVIS_ROOT / ".jarvis" / "symlink_test_dir"
    link_dir.parent.mkdir(parents=True, exist_ok=True)
    _make_dir_link(link_dir, outside_dir)
    yield link_dir
    _remove_dir_link(link_dir)


def _remove_dir_link(link_dir: Path) -> None:
    # A junction/symlink-to-dir must be removed as a directory link, not
    # recursively, so the target's real contents are never touched.
    if link_dir.is_dir():
        os.rmdir(link_dir)
    elif link_dir.exists():
        link_dir.unlink()


def test_symlink_inside_sandbox_pointing_outside_is_detected(symlink_in_sandbox):
    # The link itself lives inside JARVIS_ROOT, but check() must resolve
    # through it to the real outside target before deciding.
    decision = check(symlink_in_sandbox)
    assert not decision.inside_sandbox


def test_resolve_in_sandbox_blocks_symlink_escape(symlink_in_sandbox):
    with pytest.raises(SandboxViolation):
        resolve_in_sandbox(symlink_in_sandbox)


def test_request_outside_access_prompts_for_symlink_escape(symlink_in_sandbox):
    # Must be treated as an outside-sandbox request (approval callback
    # invoked), not silently allowed because the link's own path is inside.
    called_with = []

    def callback(p: Path) -> bool:
        called_with.append(p)
        return False

    with pytest.raises(SandboxViolation):
        request_outside_access(symlink_in_sandbox, approval_callback=callback)

    assert len(called_with) == 1
    # The path handed to the approval callback must be the REAL resolved
    # target, not the symlink's in-sandbox path - otherwise the user would
    # be approving a lie.
    assert not called_with[0].is_relative_to(JARVIS_ROOT)


def test_request_outside_access_approval_reveals_real_target(symlink_in_sandbox, outside_target):
    called_with = []

    def callback(p: Path) -> bool:
        called_with.append(p)
        return True

    result = request_outside_access(symlink_in_sandbox, approval_callback=callback)
    assert result == outside_target.resolve()


def test_symlinked_directory_escape_is_detected(symlinked_dir_in_sandbox, outside_target):
    # Access a file *through* the symlinked directory - e.g.
    # .jarvis/symlink_test_dir/outside_secret.txt - must resolve to the real
    # outside file and be blocked just like a direct symlink.
    path_through_link = symlinked_dir_in_sandbox / outside_target.name
    decision = check(path_through_link)
    assert not decision.inside_sandbox

    with pytest.raises(SandboxViolation):
        resolve_in_sandbox(path_through_link)


def test_relative_path_through_symlink_escape_is_detected(symlink_in_sandbox):
    # Same as the direct-symlink test but expressed as a relative path, the
    # way a tool call from the LLM would typically supply it.
    rel = symlink_in_sandbox.relative_to(JARVIS_ROOT)
    decision = check(str(rel))
    assert not decision.inside_sandbox
