"""Tests for the 'jarvis review' CLI argument: main() dispatches to
run_cli_review() when sys.argv[1] == 'review', which reuses
scan_project() and build_plan() without any API key or REPL. Mirrors
test_cli_work_argument.py's approach. Also verifies 'jarvis scan' /
'jarvis plan' / 'jarvis work' continue to work correctly alongside the
new 'review' argument, and that the pre-existing REPL 'review' keyword
(format_review, git status+diff) is untouched by this addition."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import (
    main,
    run_cli_plan,
    run_cli_review,
    run_cli_scan,
    run_cli_work,
)


def test_run_cli_review_reuses_scan_project_and_build_plan(capsys):
    with patch("jarvis.cli.main.scan_project") as mock_scan:
        with patch("jarvis.cli.main.build_plan") as mock_build_plan:
            with patch("jarvis.cli.main.build_review") as mock_build_review:
                with patch("jarvis.cli.main.format_review_result") as mock_format:
                    mock_scan.return_value = "a scan result"
                    mock_build_plan.return_value = "a plan object"
                    mock_build_review.return_value = "a review object"
                    mock_format.return_value = "formatted review output"
                    run_cli_review()

    mock_scan.assert_called_once()
    mock_build_plan.assert_called_once_with("a scan result")
    mock_build_review.assert_called_once_with("a scan result", "a plan object")
    mock_format.assert_called_once_with("a review object")
    captured = capsys.readouterr()
    assert "formatted review output" in captured.out


def test_run_cli_review_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with patch("jarvis.cli.main.scan_project"), patch("jarvis.cli.main.build_plan"), patch(
        "jarvis.cli.main.build_review"
    ), patch("jarvis.cli.main.format_review_result", return_value="x"):
        run_cli_review()  # must not raise


def test_main_dispatches_to_review_when_argv_is_review(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "review"])
    with patch("jarvis.cli.main.run_cli_review") as mock_run_review:
        main()
    mock_run_review.assert_called_once()


def test_main_does_not_start_repl_when_reviewing(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "review"])
    with patch("jarvis.cli.main.run_cli_review"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_review_does_not_call_run_cli_scan_plan_or_work(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "review"])
    with patch("jarvis.cli.main.run_cli_review"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_scan:
            with patch("jarvis.cli.main.run_cli_plan") as mock_plan:
                with patch("jarvis.cli.main.run_cli_work") as mock_work:
                    main()
    mock_scan.assert_not_called()
    mock_plan.assert_not_called()
    mock_work.assert_not_called()


def test_main_scan_does_not_call_run_cli_review(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "scan"])
    with patch("jarvis.cli.main.run_cli_scan"):
        with patch("jarvis.cli.main.run_cli_review") as mock_review:
            main()
    mock_review.assert_not_called()


def test_main_plan_does_not_call_run_cli_review(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "plan"])
    with patch("jarvis.cli.main.run_cli_plan"):
        with patch("jarvis.cli.main.run_cli_review") as mock_review:
            main()
    mock_review.assert_not_called()


def test_main_work_does_not_call_run_cli_review(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "work"])
    with patch("jarvis.cli.main.run_cli_work"):
        with patch("jarvis.cli.main.run_cli_review") as mock_review:
            main()
    mock_review.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_review(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_review") as mock_review:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_review.assert_not_called()


def test_review_reuses_the_exact_same_scan_project_function_as_others():
    import jarvis.cli.main as main_module

    assert run_cli_scan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_plan.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_work.__globals__["scan_project"] is main_module.scan_project
    assert run_cli_review.__globals__["scan_project"] is main_module.scan_project


def test_review_reuses_the_exact_same_build_plan_function_as_plan_and_work():
    import jarvis.cli.main as main_module

    assert run_cli_plan.__globals__["build_plan"] is main_module.build_plan
    assert run_cli_work.__globals__["build_plan"] is main_module.build_plan
    assert run_cli_review.__globals__["build_plan"] is main_module.build_plan


def test_repl_review_keyword_still_uses_original_format_review(monkeypatch):
    # The pre-existing REPL 'review' keyword (git status+diff) must remain
    # completely untouched by the new 'jarvis review' CLI argument - both
    # exist side by side under the same word, distinguished by whether
    # it's argv[1] (CLI) or typed inside the REPL loop (keyword).
    import jarvis.cli.main as main_module

    assert main_module.format_review.__module__ == "jarvis.core.review_view"
    assert main_module.format_review_result.__module__ == "jarvis.core.project_review_view"
