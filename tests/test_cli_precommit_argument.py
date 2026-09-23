"""Tests for the 'jarvis precommit' CLI argument: main() dispatches to
run_cli_precommit() when sys.argv[1] == 'precommit', which reuses
scan_project() / build_plan() / build_review() without any API key, REPL,
or test execution. Mirrors test_cli_review_argument.py's approach. Also
verifies 'jarvis scan' / 'jarvis plan' / 'jarvis work' / 'jarvis review'
continue to work correctly alongside the new 'precommit' argument, and
that the pre-existing REPL 'precommit' keyword (format_precommit, which
runs the real test suite) is untouched by this addition."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import (
    main,
    run_cli_plan,
    run_cli_precommit,
    run_cli_review,
    run_cli_scan,
    run_cli_work,
)


def test_run_cli_precommit_reuses_scan_plan_and_review(capsys):
    with patch("jarvis.cli.main.scan_project") as mock_scan:
        with patch("jarvis.cli.main.build_plan") as mock_build_plan:
            with patch("jarvis.cli.main.build_review") as mock_build_review:
                with patch("jarvis.cli.main.build_precommit_check") as mock_build_check:
                    with patch("jarvis.cli.main.format_precommit_check") as mock_format:
                        mock_scan.return_value = "a scan result"
                        mock_build_plan.return_value = "a plan object"
                        mock_build_review.return_value = "a review object"
                        mock_build_check.return_value = "a precommit check"
                        mock_format.return_value = "formatted precommit output"
                        run_cli_precommit()

    mock_scan.assert_called_once()
    mock_build_plan.assert_called_once_with("a scan result")
    mock_build_review.assert_called_once_with("a scan result", "a plan object")
    mock_build_check.assert_called_once_with("a review object")
    mock_format.assert_called_once_with("a precommit check")
    captured = capsys.readouterr()
    assert "formatted precommit output" in captured.out


def test_run_cli_precommit_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("jarvis.cli.main.scan_project"), patch("jarvis.cli.main.build_plan"), patch(
        "jarvis.cli.main.build_review"
    ), patch("jarvis.cli.main.build_precommit_check"), patch(
        "jarvis.cli.main.format_precommit_check", return_value="x"
    ):
        run_cli_precommit()  # must not raise


def test_run_cli_precommit_never_runs_subprocess(monkeypatch):
    # The whole point of the CLI 'precommit' argument (vs. the REPL
    # keyword) is that it never actually runs the test suite or any other
    # subprocess beyond the read-only git status check already covered by
    # project_review/project_precommit's own tests - guard against a
    # regression that wires it to precommit_view's real test runner.
    import subprocess as sp

    def _boom(*a, **k):
        raise AssertionError("run_cli_precommit must never call subprocess directly")

    monkeypatch.setattr(sp, "run", _boom)
    with patch("jarvis.cli.main.scan_project"), patch("jarvis.cli.main.build_plan"), patch(
        "jarvis.cli.main.build_review"
    ), patch("jarvis.cli.main.build_precommit_check"), patch(
        "jarvis.cli.main.format_precommit_check", return_value="x"
    ):
        run_cli_precommit()


def test_main_dispatches_to_precommit_when_argv_is_precommit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "precommit"])
    with patch("jarvis.cli.main.run_cli_precommit") as mock_run:
        main()
    mock_run.assert_called_once()


def test_main_does_not_start_repl_when_running_precommit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "precommit"])
    with patch("jarvis.cli.main.run_cli_precommit"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_precommit_does_not_call_other_cli_entry_points(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "precommit"])
    with patch("jarvis.cli.main.run_cli_precommit"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_scan:
            with patch("jarvis.cli.main.run_cli_plan") as mock_plan:
                with patch("jarvis.cli.main.run_cli_work") as mock_work:
                    with patch("jarvis.cli.main.run_cli_review") as mock_review:
                        main()
    mock_scan.assert_not_called()
    mock_plan.assert_not_called()
    mock_work.assert_not_called()
    mock_review.assert_not_called()


def test_main_review_does_not_call_run_cli_precommit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "review"])
    with patch("jarvis.cli.main.run_cli_review"):
        with patch("jarvis.cli.main.run_cli_precommit") as mock_precommit:
            main()
    mock_precommit.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_precommit(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_precommit") as mock_precommit:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_precommit.assert_not_called()


def test_precommit_reuses_the_exact_same_scan_project_function_as_others():
    import jarvis.cli.main as main_module

    assert run_cli_scan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_plan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_work.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_review.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_precommit.__globals__["scan_project"] is main_module.scan_project


def test_precommit_reuses_the_exact_same_build_review_function_as_review():
    import jarvis.cli.main as main_module

    assert run_cli_review.__globals__["build_review"] is main_module.build_review
    assert run_cli_precommit.__globals__["build_review"] is main_module.build_review


def test_repl_precommit_keyword_still_uses_original_format_precommit():
    # The pre-existing REPL 'precommit' keyword (real test-suite run via
    # jarvis.core.precommit_view.format_precommit) must remain completely
    # untouched by the new 'jarvis precommit' CLI argument - both exist
    # side by side under the same word, distinguished by whether it's
    # argv[1] (CLI) or typed inside the REPL loop (keyword).
    import jarvis.cli.main as main_module

    assert main_module.format_precommit.__module__ == "jarvis.core.precommit_view"
    assert main_module.format_precommit_check.__module__ == "jarvis.core.project_precommit_view"
