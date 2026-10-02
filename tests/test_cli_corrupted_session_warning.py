"""End-to-end check that a corrupted session.json produces a visible
[warning] in the CLI's startup output, rather than being silently
discarded. Runs the real jarvis.cli.main entry point as a subprocess
against an isolated JARVIS_ROOT copy, so it never touches the actual
project's session.json. A fake API key is supplied since main() builds
LLMClient() before load_history() - no real API call happens, since the
scripted input is 'exit' and the loop never reaches agent.step().

Requires a genuinely importable `anthropic` package in the subprocess
(this test runs a real, separate Python process, not something this
suite's own test-time mocks can reach into) - skipped, not failed, on a
machine where it can't be imported (e.g. Windows Smart App Control
blocking anthropic's own `jiter` dependency - see jarvis.core.llm's own
"JARVIS won't open at all" bug fix docstring for the full story): on
such a machine, main() now correctly exits early with a clear AI-
unavailable message BEFORE ever reaching load_history()/this test's own
session.json check, which is the CORRECT, intended behavior (see
jarvis.cli.main._build_llm_or_exit()) - it just means this specific
test's own target behavior isn't reachable there, not that anything is
broken."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from jarvis.config import JARVIS_ROOT


def _anthropic_really_imports() -> bool:
    """A real, live import attempt (not just find_spec(), which only
    confirms the package is INSTALLED, not that it can actually be
    imported - the exact gap this whole bug fix is about) in a
    throwaway subprocess, so this test's own import failure never
    pollutes this test process's own sys.modules cache."""
    result = subprocess.run(
        [sys.executable, "-c", "import anthropic"], capture_output=True, timeout=30,
    )
    return result.returncode == 0


pytestmark = pytest.mark.skipif(
    not _anthropic_really_imports(),
    reason="anthropic package can't actually be imported in this environment (e.g. Windows Smart App Control block) - see this file's own module docstring",
)


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
