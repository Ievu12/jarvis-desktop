"""End-to-end check that a corrupted session.json produces a visible
[warning] in the CLI's startup output, rather than being silently
discarded. Runs the real jarvis.cli.main entry point as a subprocess
against an isolated JARVIS_ROOT copy, so it never touches the actual
project's session.json. A fake API key is supplied since main() builds
LLMClient() before load_history() - no real API call happens, since the
scripted input is 'exit' and the loop never reaches agent.step()."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from jarvis.config import JARVIS_ROOT


@pytest.fixture
def isolated_project_copy(tmp_path):
    """A minimal isolated copy of JARVIS_ROOT's jarvis/ package, so the
    subprocess can import it with its own JARVIS_ROOT and .jarvis/ dir,
    without touching the real project's session.json."""
    dest = tmp_path / "jarvis_copy"
    shutil.copytree(JARVIS_ROOT / "jarvis", dest / "jarvis")
    (dest / ".jarvis").mkdir()
    (dest / ".jarvis" / "session.json").write_text("{not valid json at all", encoding="utf-8")
    return dest


def test_corrupted_session_produces_visible_warning_in_cli_startup(isolated_project_copy):
    env = dict(os.environ)
    env["JARVIS_ROOT"] = str(isolated_project_copy)
    env["ANTHROPIC_API_KEY"] = "sk-ant-fake-key-for-this-test-only"

    result = subprocess.run(
        [sys.executable, "-m", "jarvis.cli.main"],
        cwd=isolated_project_copy,
        env=env,
        input="exit\n",
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert "[warning]" in result.stdout
    assert "corrupted" in result.stdout.lower()
