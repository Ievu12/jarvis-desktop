from pathlib import Path

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.core.sandbox import SandboxViolation, check, request_outside_access, resolve_in_sandbox


def test_relative_path_inside_root_is_allowed():
    decision = check("jarvis/config.py")
    assert decision.inside_sandbox
    assert decision.path == (JARVIS_ROOT / "jarvis" / "config.py").resolve()


def test_absolute_path_inside_root_is_allowed():
    target = JARVIS_ROOT / "jarvis" / "config.py"
    decision = check(str(target))
    assert decision.inside_sandbox


def test_path_outside_root_is_blocked():
    decision = check(JARVIS_ROOT.parent / "some-other-project" / "file.txt")
    assert not decision.inside_sandbox


def test_dotdot_traversal_is_blocked():
    decision = check("../some-other-project/file.txt")
    assert not decision.inside_sandbox


def test_resolve_in_sandbox_raises_outside_root():
    with pytest.raises(SandboxViolation):
        resolve_in_sandbox("../yoga-website/secrets.env")


def test_resolve_in_sandbox_allows_inside_root():
    result = resolve_in_sandbox("jarvis/config.py")
    assert result.is_relative_to(JARVIS_ROOT)


def test_request_outside_access_denied_when_callback_declines():
    with pytest.raises(SandboxViolation):
        request_outside_access("../yoga-website/file.txt", approval_callback=lambda p: False)


def test_request_outside_access_allowed_when_callback_approves():
    result = request_outside_access("../yoga-website/file.txt", approval_callback=lambda p: True)
    assert not result.is_relative_to(JARVIS_ROOT)


def test_request_outside_access_skips_callback_when_inside_root():
    called = []
    result = request_outside_access(
        "jarvis/config.py", approval_callback=lambda p: called.append(p) or True
    )
    assert called == []
    assert result.is_relative_to(JARVIS_ROOT)
