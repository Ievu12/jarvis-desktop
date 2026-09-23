"""Tests for the 'jarvis commit' CLI argument and the REPL 'commit'
command: the one CLI/REPL entry point that can actually run 'git commit'.
Covers: building the commit plan pipeline, stopping before any prompt
when not ready, never running git-add, requiring an explicit y/N
confirmation via confirm_side_effect() before running git commit, message
handling ('-m' argument vs. interactive stdin prompt vs. REPL prompt),
denial/interrupt handling, and non-interference with the other CLI
arguments and the REPL loop."""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from jarvis.cli.main import (
    _prompt_for_commit_message,
    _run_commit_flow,
    _try_suggest_commit_message,
    main,
    run_cli_commit,
    run_cli_precommit,
)
from jarvis.core.project_commit import CommitPlan


def _plan(**overrides) -> CommitPlan:
    defaults = dict(
        root_summary="/x",
        is_git_repo=True,
        staged_files=["a.py"],
        unstaged_files=[],
        findings=[],
        concerns=[],
        can_commit=True,
        block_reason="Staged changes are present and the pre-commit check passed.",
    )
    defaults.update(overrides)
    return CommitPlan(**defaults)


def _with_plan(plan: CommitPlan):
    return patch("jarvis.cli.main._build_current_commit_plan", return_value=plan)


# --- pipeline reuse ----------------------------------------------------------------


def test_build_current_commit_plan_reuses_scan_plan_review_precommit():
    with patch("jarvis.cli.main.scan_project") as mock_scan:
        with patch("jarvis.cli.main.build_plan") as mock_build_plan:
            with patch("jarvis.cli.main.build_review") as mock_build_review:
                with patch("jarvis.cli.main.build_precommit_check") as mock_build_check:
                    with patch("jarvis.cli.main.build_commit_plan") as mock_build_commit:
                        mock_scan.return_value = "scan"
                        mock_build_plan.return_value = "plan"
                        mock_build_review.return_value = "review"
                        mock_build_check.return_value = "check"
                        mock_build_commit.return_value = "commit plan"

                        import jarvis.cli.main as main_module

                        result = main_module._build_current_commit_plan()

    mock_scan.assert_called_once()
    mock_build_plan.assert_called_once_with("scan")
    mock_build_review.assert_called_once_with("scan", "plan")
    mock_build_check.assert_called_once_with("review")
    mock_build_commit.assert_called_once_with("check")
    assert result == "commit plan"


# --- stops before any prompt when not ready -----------------------------------------


def test_not_a_repo_never_prompts_or_runs_git(capsys):
    with _with_plan(_plan(is_git_repo=False, staged_files=[], can_commit=False, block_reason="Not a git repository yet.")):
        with patch("builtins.input") as mock_input:
            with patch("subprocess.run") as mock_run:
                _run_commit_flow(None)
    mock_input.assert_not_called()
    mock_run.assert_not_called()
    assert "not a git repository" in capsys.readouterr().out.lower()


def test_nothing_staged_never_prompts_or_runs_git(capsys):
    with _with_plan(_plan(staged_files=[], can_commit=False, block_reason="Nothing is staged for commit.")):
        with patch("builtins.input") as mock_input:
            with patch("subprocess.run") as mock_run:
                _run_commit_flow(None)
    mock_input.assert_not_called()
    mock_run.assert_not_called()


def test_precommit_not_ready_never_prompts_or_runs_git(capsys):
    with _with_plan(_plan(can_commit=False, block_reason="Working tree is already clean.")):
        with patch("builtins.input") as mock_input:
            with patch("subprocess.run") as mock_run:
                _run_commit_flow(None)
    mock_input.assert_not_called()
    mock_run.assert_not_called()


# --- message handling ----------------------------------------------------------------


def test_explicit_message_skips_message_prompt_but_still_confirms():
    with _with_plan(_plan()):
        with patch("jarvis.cli.main.confirm_side_effect", return_value=False) as mock_confirm:
            with patch("builtins.input") as mock_input:
                with patch("subprocess.run") as mock_run:
                    _run_commit_flow("my message")
    mock_input.assert_not_called()  # no message prompt - message was given
    mock_confirm.assert_called_once()
    assert "my message" in mock_confirm.call_args[0][0]
    mock_run.assert_not_called()  # denied


def _no_suggestion(*a, **k):
    # Every message-prompt test below exercises the plain stdin-prompt
    # path, not the LLM-suggestion feature (covered separately in
    # test_commit_message_suggestion.py) - forcing no suggestion keeps
    # these tests independent of whether a real ANTHROPIC_API_KEY happens
    # to be set in the environment they run in.
    return None


def test_missing_message_prompts_on_stdin():
    with _with_plan(_plan()):
        with patch("jarvis.cli.main._try_suggest_commit_message", side_effect=_no_suggestion):
            with patch("builtins.input", return_value="typed message") as mock_input:
                with patch("jarvis.cli.main.confirm_side_effect", return_value=False):
                    with patch("subprocess.run") as mock_run:
                        _run_commit_flow(None)
    mock_input.assert_called_once()
    mock_run.assert_not_called()


def test_empty_message_from_prompt_aborts_without_confirmation():
    with _with_plan(_plan()):
        with patch("jarvis.cli.main._try_suggest_commit_message", side_effect=_no_suggestion):
            with patch("builtins.input", return_value="   "):
                with patch("jarvis.cli.main.confirm_side_effect") as mock_confirm:
                    with patch("subprocess.run") as mock_run:
                        _run_commit_flow(None)
    mock_confirm.assert_not_called()
    mock_run.assert_not_called()


def test_eof_on_message_prompt_aborts_gracefully():
    with _with_plan(_plan()):
        with patch("jarvis.cli.main._try_suggest_commit_message", side_effect=_no_suggestion):
            with patch("builtins.input", side_effect=EOFError):
                with patch("jarvis.cli.main.confirm_side_effect") as mock_confirm:
                    with patch("subprocess.run") as mock_run:
                        _run_commit_flow(None)  # must not raise
    mock_confirm.assert_not_called()
    mock_run.assert_not_called()


# --- LLM commit-message suggestion (advisory only, never auto-applied) ----------------


def test_try_suggest_no_api_key_returns_none_without_calling_llm(monkeypatch):
    monkeypatch.setattr("jarvis.cli.main.ANTHROPIC_API_KEY", None)
    with patch("jarvis.cli.main.LLMClient") as mock_llm_cls:
        result = _try_suggest_commit_message(["a.py"])
    assert result is None
    mock_llm_cls.assert_not_called()


def test_try_suggest_llm_client_init_failure_returns_none(monkeypatch):
    monkeypatch.setattr("jarvis.cli.main.ANTHROPIC_API_KEY", "sk-ant-fake")
    with patch("jarvis.cli.main.LLMClient", side_effect=RuntimeError("no key")):
        result = _try_suggest_commit_message(["a.py"])
    assert result is None


def test_try_suggest_delegates_to_suggest_commit_message(monkeypatch):
    monkeypatch.setattr("jarvis.cli.main.ANTHROPIC_API_KEY", "sk-ant-fake")
    with patch("jarvis.cli.main.LLMClient") as mock_llm_cls:
        with patch("jarvis.cli.main.get_staged_diff", return_value="some diff"):
            with patch("jarvis.cli.main.suggest_commit_message", return_value="Add feature X") as mock_suggest:
                result = _try_suggest_commit_message(["a.py", "b.py"])
    assert result == "Add feature X"
    mock_suggest.assert_called_once_with(mock_llm_cls.return_value, ["a.py", "b.py"], "some diff")


def test_prompt_with_suggestion_shown_accepts_blank_input():
    with patch("jarvis.cli.main._try_suggest_commit_message", return_value="Suggested message"):
        with patch("builtins.input", return_value="") as mock_input:
            result = _prompt_for_commit_message(["a.py"])
    assert result == "Suggested message"
    mock_input.assert_called_once()


def test_prompt_with_suggestion_shown_prefers_typed_message():
    with patch("jarvis.cli.main._try_suggest_commit_message", return_value="Suggested message"):
        with patch("builtins.input", return_value="My own message"):
            result = _prompt_for_commit_message(["a.py"])
    assert result == "My own message"


def test_prompt_displays_the_suggestion_to_the_user(capsys):
    with patch("jarvis.cli.main._try_suggest_commit_message", return_value="Fix the bug"):
        with patch("builtins.input", return_value=""):
            _prompt_for_commit_message(["a.py"])
    assert "Fix the bug" in capsys.readouterr().out


def test_prompt_without_suggestion_behaves_exactly_as_before():
    with patch("jarvis.cli.main._try_suggest_commit_message", return_value=None):
        with patch("builtins.input", return_value="") as mock_input:
            result = _prompt_for_commit_message(["a.py"])
    assert result is None  # blank input with no suggestion still aborts
    prompt_arg = mock_input.call_args[0][0]
    assert prompt_arg == "Commit message: "


def test_prompt_without_suggestion_typed_message_still_works():
    with patch("jarvis.cli.main._try_suggest_commit_message", return_value=None):
        with patch("builtins.input", return_value="typed"):
            result = _prompt_for_commit_message(["a.py"])
    assert result == "typed"


def test_prompt_eof_returns_none_even_with_suggestion():
    with patch("jarvis.cli.main._try_suggest_commit_message", return_value="Suggested"):
        with patch("builtins.input", side_effect=EOFError):
            result = _prompt_for_commit_message(["a.py"])
    assert result is None


def test_suggestion_never_bypasses_the_final_approval_prompt():
    # Even when a suggestion is silently accepted (blank Enter), the
    # normal confirm_side_effect() y/N gate must still run before any
    # subprocess call - accepting a suggested message is not the same as
    # approving the commit itself.
    with _with_plan(_plan()):
        with patch("jarvis.cli.main._try_suggest_commit_message", return_value="Suggested message"):
            with patch("builtins.input", return_value=""):
                with patch("jarvis.cli.main.confirm_side_effect", return_value=False) as mock_confirm:
                    with patch("subprocess.run") as mock_run:
                        _run_commit_flow(None)
    mock_confirm.assert_called_once()
    assert "Suggested message" in mock_confirm.call_args[0][0]
    mock_run.assert_not_called()


def test_explicit_message_via_dash_m_never_triggers_suggestion():
    # -m already skips the whole prompt/suggestion path entirely.
    with _with_plan(_plan()):
        with patch("jarvis.cli.main._try_suggest_commit_message") as mock_try_suggest:
            with patch("jarvis.cli.main.confirm_side_effect", return_value=False):
                with patch("subprocess.run"):
                    _run_commit_flow("explicit message")
    mock_try_suggest.assert_not_called()


# --- confirmation gate: the core safety property --------------------------------------


def test_denied_confirmation_never_runs_git_commit():
    with _with_plan(_plan()):
        with patch("jarvis.cli.main.confirm_side_effect", return_value=False):
            with patch("subprocess.run") as mock_run:
                _run_commit_flow("msg")
    mock_run.assert_not_called()


def test_approved_confirmation_runs_git_commit_with_exact_argv():
    with _with_plan(_plan(staged_files=["a.py", "b.py"])):
        with patch("jarvis.cli.main.confirm_side_effect", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = "committed"
                mock_run.return_value.stderr = ""
                _run_commit_flow("my commit message")
    mock_run.assert_called_once()
    argv = mock_run.call_args[0][0]
    assert argv == ["git", "commit", "-m", "my commit message"]


def test_confirmation_prompt_mentions_staged_count_and_message():
    with _with_plan(_plan(staged_files=["a.py", "b.py"])):
        with patch("jarvis.cli.main.confirm_side_effect", return_value=False) as mock_confirm:
            with patch("subprocess.run"):
                _run_commit_flow("fix things")
    description = mock_confirm.call_args[0][0]
    assert "2" in description
    assert "fix things" in description


def test_keyboard_interrupt_during_confirmation_propagates():
    with _with_plan(_plan()):
        with patch("jarvis.cli.main.confirm_side_effect", side_effect=KeyboardInterrupt):
            with patch("subprocess.run") as mock_run:
                with pytest.raises(KeyboardInterrupt):
                    _run_commit_flow("msg")
    mock_run.assert_not_called()


# --- never runs git add -----------------------------------------------------------------


def test_run_commit_flow_never_invokes_git_add():
    with _with_plan(_plan()):
        with patch("jarvis.cli.main.confirm_side_effect", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 0
                mock_run.return_value.stdout = ""
                mock_run.return_value.stderr = ""
                _run_commit_flow("msg")
    for call in mock_run.call_args_list:
        argv = call[0][0]
        assert "add" not in argv


# --- invalid message rejected before any prompt -----------------------------------------


def test_message_rejected_by_git_commit_builder_never_prompts_for_confirmation():
    # An empty message somehow reaching _run_commit_flow (bypassing the
    # stdin-prompt's own blank check) must still be caught by _git_commit's
    # own validation before any approval prompt appears.
    with _with_plan(_plan()):
        with patch("jarvis.cli.main.confirm_side_effect") as mock_confirm:
            with patch("subprocess.run") as mock_run:
                _run_commit_flow("   ")
    mock_confirm.assert_not_called()
    mock_run.assert_not_called()


# --- git commit failure/timeout handled gracefully ---------------------------------------


def test_git_commit_nonzero_exit_reported_not_raised(capsys):
    with _with_plan(_plan()):
        with patch("jarvis.cli.main.confirm_side_effect", return_value=True):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value.returncode = 1
                mock_run.return_value.stdout = ""
                mock_run.return_value.stderr = "nothing to commit"
                _run_commit_flow("msg")  # must not raise
    assert "failed" in capsys.readouterr().out.lower()


def test_git_commit_timeout_reported_not_raised(capsys):
    with _with_plan(_plan()):
        with patch("jarvis.cli.main.confirm_side_effect", return_value=True):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="git", timeout=30)):
                _run_commit_flow("msg")  # must not raise
    assert "timed out" in capsys.readouterr().out.lower()


# --- run_cli_commit argv parsing ('-m' handling) ------------------------------------------


def test_run_cli_commit_with_no_args_prompts_for_message(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "commit"])
    with patch("jarvis.cli.main._run_commit_flow") as mock_flow:
        run_cli_commit()
    mock_flow.assert_called_once_with(None)


def test_run_cli_commit_with_dash_m_passes_message(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "commit", "-m", "my message"])
    with patch("jarvis.cli.main._run_commit_flow") as mock_flow:
        run_cli_commit()
    mock_flow.assert_called_once_with("my message")


def test_run_cli_commit_with_dash_m_no_value_falls_back_to_prompt(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "commit", "-m"])
    with patch("jarvis.cli.main._run_commit_flow") as mock_flow:
        run_cli_commit()
    mock_flow.assert_called_once_with(None)


# --- main() dispatch ----------------------------------------------------------------------


def test_main_dispatches_to_commit_when_argv_is_commit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "commit"])
    with patch("jarvis.cli.main.run_cli_commit") as mock_run:
        main()
    mock_run.assert_called_once()


def test_main_does_not_start_repl_when_committing(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "commit"])
    with patch("jarvis.cli.main.run_cli_commit"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_commit_does_not_call_other_cli_entry_points(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "commit"])
    with patch("jarvis.cli.main.run_cli_commit"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_scan:
            with patch("jarvis.cli.main.run_cli_plan") as mock_plan:
                with patch("jarvis.cli.main.run_cli_work") as mock_work:
                    with patch("jarvis.cli.main.run_cli_review") as mock_review:
                        with patch("jarvis.cli.main.run_cli_precommit") as mock_precommit:
                            main()
    mock_scan.assert_not_called()
    mock_plan.assert_not_called()
    mock_work.assert_not_called()
    mock_review.assert_not_called()
    mock_precommit.assert_not_called()


def test_main_precommit_does_not_call_run_cli_commit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "precommit"])
    with patch("jarvis.cli.main.run_cli_precommit"):
        with patch("jarvis.cli.main.run_cli_commit") as mock_commit:
            main()
    mock_commit.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_commit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_commit") as mock_commit:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_commit.assert_not_called()


def test_commit_reuses_the_exact_same_scan_project_function_as_others():
    import jarvis.cli.main as main_module

    assert run_cli_precommit.__globals__["scan_project"] is main_module.scan_project
    assert main_module._build_current_commit_plan.__globals__["scan_project"] is main_module.scan_project


# --- REPL 'commit' keyword ------------------------------------------------------------------


def test_repl_commit_keyword_invokes_run_commit_flow_with_no_message(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    inputs = iter(["commit", "exit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main._run_commit_flow") as mock_flow:
                main()
    mock_flow.assert_called_once_with(None)


def test_repl_commit_keyword_does_not_reach_llm_agent_turn(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    inputs = iter(["commit", "exit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main._run_commit_flow"):
                with patch("jarvis.cli.main._run_agent_turn") as mock_turn:
                    main()
    mock_turn.assert_not_called()
