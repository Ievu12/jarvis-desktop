"""Tests for jarvis.core.secrets: masking, redaction, and the .env
gitignore startup check. No real API key is used or connected anywhere
here - these tests only exercise string handling and file/gitignore
checks inside JARVIS_ROOT."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.core.secrets import check_env_gitignored, mask_secret, redact_secret


# --- mask_secret ---------------------------------------------------------


def test_mask_secret_none_returns_placeholder():
    assert mask_secret(None) == "<not set>"


def test_mask_secret_empty_string_returns_placeholder():
    assert mask_secret("") == "<not set>"


def test_mask_secret_never_returns_original_value():
    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    masked = mask_secret(key)
    assert masked != key
    assert key not in masked


def test_mask_secret_shows_only_last_n_chars():
    key = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    masked = mask_secret(key, visible=4)
    assert masked.endswith(key[-4:])
    assert masked.count("*") == len(key) - 4


def test_mask_secret_short_value_fully_masked():
    masked = mask_secret("abc", visible=4)
    assert masked == "***"
    assert "abc" not in masked


# --- redact_secret ---------------------------------------------------------


def test_redact_secret_removes_known_value_from_text():
    key = "sk-ant-api03-super-secret-value"
    text = f"Request failed: invalid key {key} was rejected"
    redacted = redact_secret(text, secret=key)
    assert key not in redacted
    assert "Request failed" in redacted


def test_redact_secret_no_secret_configured_returns_text_unchanged():
    text = "some error message"
    assert redact_secret(text, secret=None) == text


def test_redact_secret_secret_not_present_in_text_is_noop():
    text = "unrelated error message"
    redacted = redact_secret(text, secret="sk-ant-not-in-this-string")
    assert redacted == text


def test_redact_secret_defaults_to_configured_api_key(monkeypatch):
    monkeypatch.setattr("jarvis.config.ANTHROPIC_API_KEY", "sk-ant-configured-value")
    text = "error: sk-ant-configured-value is invalid"
    redacted = redact_secret(text)
    assert "sk-ant-configured-value" not in redacted


# --- check_env_gitignored ---------------------------------------------------


@pytest.fixture
def scratch_env_file():
    """A throwaway .env file inside JARVIS_ROOT, cleaned up after the test.
    Content is a fake placeholder, never a real key."""
    env_path = JARVIS_ROOT / ".env"
    pre_existing = env_path.exists()
    original_content = env_path.read_text(encoding="utf-8") if pre_existing else None

    env_path.write_text("ANTHROPIC_API_KEY=placeholder-not-a-real-key\n", encoding="utf-8")
    yield env_path

    if pre_existing:
        env_path.write_text(original_content, encoding="utf-8")
    else:
        env_path.unlink(missing_ok=True)


def test_no_env_file_produces_no_warnings():
    env_path = JARVIS_ROOT / ".env"
    if env_path.exists():
        pytest.skip(".env already exists in this project; covered by other tests instead")
    warnings = check_env_gitignored(JARVIS_ROOT)
    assert warnings == []


def test_env_file_present_without_git_repo_warns(scratch_env_file):
    git_dir = JARVIS_ROOT / ".git"
    if git_dir.exists():
        pytest.skip("JARVIS_ROOT is a git repo in this environment; covered by git-aware test")

    warnings = check_env_gitignored(JARVIS_ROOT)
    assert len(warnings) == 1
    assert "not a git repository" in warnings[0]


def test_env_file_ignored_by_git_produces_no_warning(tmp_path):
    # Uses an isolated temp directory (not JARVIS_ROOT) as its own throwaway
    # git repo, so this test never touches the real project's git state.
    _init_git_repo(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tmp_path / ".env").write_text("PLACEHOLDER=not-a-real-key\n", encoding="utf-8")

    warnings = check_env_gitignored(tmp_path)
    assert warnings == []


def test_env_file_not_ignored_by_git_warns(tmp_path):
    _init_git_repo(tmp_path)
    # Deliberately no .gitignore entry for .env.
    (tmp_path / ".env").write_text("PLACEHOLDER=not-a-real-key\n", encoding="utf-8")

    warnings = check_env_gitignored(tmp_path)
    assert len(warnings) == 1
    assert "NOT covered by .gitignore" in warnings[0]


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


def test_gitignore_actually_covers_env_pattern():
    """Direct check that the .gitignore file itself lists .env, independent
    of whether a .git repo exists yet in this environment."""
    gitignore_path = JARVIS_ROOT / ".gitignore"
    assert gitignore_path.exists()
    patterns = gitignore_path.read_text(encoding="utf-8").splitlines()
    assert ".env" in [p.strip() for p in patterns]
