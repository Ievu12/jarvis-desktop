"""Tests for the 'jarvis integrations' CLI argument and the REPL
'integrations' keyword: both list every registered integration's status
via IntegrationsManager.list_statuses(), using build_integration_registry()
to build the IntegrationRegistry. Covers: correct delegation to
IntegrationsManager (never a raw Connector call from CLI code), no API
key/REPL required, non-interference with the other CLI arguments and the
REPL loop, and that no credential value ever appears in the output."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.cli.main import (
    build_integration_registry,
    main,
    run_cli_integrations,
    run_cli_precommit,
    run_cli_scan,
)
from jarvis.integrations.base import Connector, ExternalActionResult
from jarvis.integrations.manager import IntegrationStatus, IntegrationStatusInfo
from jarvis.integrations.registry import IntegrationRegistry


class _FakeConnector(Connector):
    service_name = "fake_service"
    actions = []

    def is_configured(self) -> bool:
        return False

    def execute(self, action_name: str, **kwargs) -> ExternalActionResult:
        return ExternalActionResult(ok=True, output="", service=self.service_name, action=action_name)


# --- build_integration_registry -----------------------------------------------------


def test_build_integration_registry_registers_email():
    registry = build_integration_registry()
    assert registry.get("email") is not None


def test_build_integration_registry_registers_stripe():
    registry = build_integration_registry()
    assert registry.get("stripe") is not None


def test_build_integration_registry_registers_google_calendar():
    registry = build_integration_registry()
    assert registry.get("google_calendar") is not None


def test_build_integration_registry_registers_gmail():
    registry = build_integration_registry()
    assert registry.get("gmail") is not None


def test_build_integration_registry_registers_instagram():
    registry = build_integration_registry()
    assert registry.get("instagram") is not None


def test_build_integration_registry_returns_integration_registry_instance():
    registry = build_integration_registry()
    assert isinstance(registry, IntegrationRegistry)


# --- run_cli_integrations: delegates to IntegrationsManager, never touches a Connector directly --


def test_run_cli_integrations_uses_integrations_manager(capsys):
    fake_statuses = [
        IntegrationStatusInfo(service_name="email", status=IntegrationStatus.NOT_CONFIGURED, detail="d")
    ]
    with patch("jarvis.cli.main.build_integration_registry") as mock_build_registry:
        with patch("jarvis.cli.main.IntegrationsManager") as mock_manager_cls:
            mock_manager_cls.return_value.list_statuses.return_value = fake_statuses
            run_cli_integrations()

    mock_build_registry.assert_called_once()
    mock_manager_cls.assert_called_once_with(mock_build_registry.return_value)
    mock_manager_cls.return_value.list_statuses.assert_called_once()
    assert "email" in capsys.readouterr().out


def test_run_cli_integrations_prints_formatted_output(capsys):
    with patch("jarvis.cli.main.format_integration_statuses", return_value="FORMATTED OUTPUT HERE"):
        with patch("jarvis.cli.main.IntegrationsManager") as mock_manager_cls:
            mock_manager_cls.return_value.list_statuses.return_value = []
            run_cli_integrations()
    assert "FORMATTED OUTPUT HERE" in capsys.readouterr().out


def test_run_cli_integrations_does_not_require_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    run_cli_integrations()  # must not raise, uses the real registry/manager


def test_run_cli_integrations_never_calls_connector_execute_or_disconnect():
    registry = IntegrationRegistry()
    connector = _FakeConnector()
    registry.register(connector)

    with patch("jarvis.cli.main.build_integration_registry", return_value=registry):
        with patch.object(connector, "execute") as mock_execute:
            with patch.object(connector, "disconnect") as mock_disconnect:
                run_cli_integrations()

    mock_execute.assert_not_called()
    mock_disconnect.assert_not_called()


def test_run_cli_integrations_real_registry_lists_email(capsys):
    # End-to-end with the real build_integration_registry()/
    # IntegrationsManager (no mocking) - only the real EmailConnector,
    # never touching the network or a real keychain entry beyond what
    # is_configured()/TokenStore.load() already do read-only.
    run_cli_integrations()
    out = capsys.readouterr().out
    assert "email" in out
    assert "Integrations (" in out


def test_run_cli_integrations_real_registry_lists_stripe(capsys):
    # Same end-to-end run also covers the real StripeConnector - no
    # mocking, no network access beyond is_configured()'s env var check.
    run_cli_integrations()
    out = capsys.readouterr().out
    assert "stripe" in out


def test_run_cli_integrations_shows_stripe_not_configured_status(monkeypatch, capsys):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    run_cli_integrations()
    out = capsys.readouterr().out
    stripe_lines = [line for line in out.splitlines() if line.startswith("stripe:")]
    assert len(stripe_lines) == 1
    assert "NOT CONFIGURED" in stripe_lines[0]


def test_cli_integrations_never_leaks_a_configured_stripe_key(monkeypatch, capsys):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_superSecretStripeKeyValue12345")
    run_cli_integrations()
    out = capsys.readouterr().out
    assert "sk_test_superSecretStripeKeyValue12345" not in out


def test_run_cli_integrations_real_registry_lists_google_calendar(capsys):
    # End-to-end with the real build_integration_registry()/
    # IntegrationsManager - no mocking. GoogleCalendarConnector's
    # is_configured() only checks the real OS keychain (via TokenStore),
    # never the network - safe to run unmocked here exactly like the
    # email/stripe end-to-end checks above.
    run_cli_integrations()
    out = capsys.readouterr().out
    assert "google_calendar" in out


def test_run_cli_integrations_shows_google_calendar_not_configured_status(capsys):
    # Mocked to None so this test is deterministic regardless of whether
    # this machine happens to have real Google Calendar OAuth tokens
    # stored in its OS keychain (e.g. from a completed manual OAuth
    # connect step) - mirrors the same fix already applied to the
    # equivalent Gmail test.
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=None
    ):
        run_cli_integrations()
    out = capsys.readouterr().out
    calendar_lines = [line for line in out.splitlines() if line.startswith("google_calendar:")]
    assert len(calendar_lines) == 1
    assert "NOT CONFIGURED" in calendar_lines[0]


def test_cli_integrations_never_leaks_stored_calendar_tokens(capsys):
    import time

    from jarvis.integrations.oauth import OAuthTokens

    tokens = OAuthTokens(
        access_token="superSecretCalendarAccessToken12345",
        refresh_token="superSecretCalendarRefreshToken12345",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/calendar.readonly",
    )
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=tokens
    ):
        run_cli_integrations()
    out = capsys.readouterr().out
    assert "superSecretCalendarAccessToken12345" not in out
    assert "superSecretCalendarRefreshToken12345" not in out


def test_run_cli_integrations_real_registry_lists_gmail(capsys):
    # End-to-end with the real build_integration_registry()/
    # IntegrationsManager - no mocking. GmailConnector's is_configured()
    # only checks the real OS keychain (via TokenStore), never the
    # network - safe to run unmocked here exactly like the calendar
    # end-to-end check above.
    run_cli_integrations()
    out = capsys.readouterr().out
    assert "gmail" in out


def test_run_cli_integrations_shows_gmail_not_configured_status(capsys):
    # Mocked to None so this test is deterministic regardless of whether
    # this machine happens to have real Gmail OAuth tokens stored in its
    # OS keychain (e.g. from a completed manual OAuth connect step) -
    # otherwise this test's outcome would depend on host machine state
    # rather than on run_cli_integrations()'s own behavior.
    with patch("jarvis.integrations.connectors.gmail.TokenStore.load", return_value=None):
        run_cli_integrations()
    out = capsys.readouterr().out
    gmail_lines = [line for line in out.splitlines() if line.startswith("gmail:")]
    assert len(gmail_lines) == 1
    assert "NOT CONFIGURED" in gmail_lines[0]


def test_run_cli_integrations_shows_gmail_connected_status_with_stored_tokens(capsys):
    # The counterpart to the NOT_CONFIGURED case above: with real-shaped
    # (but fake) tokens stored, status must be CONNECTED and the detail
    # must correctly describe an OAuth-based credential - this is the
    # exact scenario the manager.py fix in this change addresses (detail
    # previously read "no credentials registered." even when CONNECTED).
    import time

    from jarvis.integrations.oauth import OAuthTokens

    tokens = OAuthTokens(
        access_token="fake-access-token-for-test",
        refresh_token="fake-refresh-token-for-test",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    with patch("jarvis.integrations.connectors.gmail.TokenStore.load", return_value=tokens):
        run_cli_integrations()
    out = capsys.readouterr().out
    gmail_lines = [line for line in out.splitlines() if line.startswith("gmail:")]
    assert len(gmail_lines) == 1
    assert "CONNECTED" in gmail_lines[0]
    assert "no credentials registered" not in out.lower()


def test_cli_integrations_never_leaks_stored_gmail_tokens(capsys):
    import time

    from jarvis.integrations.oauth import OAuthTokens

    tokens = OAuthTokens(
        access_token="superSecretGmailAccessToken12345",
        refresh_token="superSecretGmailRefreshToken12345",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    with patch("jarvis.integrations.connectors.gmail.TokenStore.load", return_value=tokens):
        run_cli_integrations()
    out = capsys.readouterr().out
    assert "superSecretGmailAccessToken12345" not in out
    assert "superSecretGmailRefreshToken12345" not in out


def test_run_cli_integrations_real_registry_lists_instagram(capsys):
    # End-to-end with the real build_integration_registry()/
    # IntegrationsManager - no mocking. InstagramConnector's
    # is_configured() only checks the real OS keychain (via TokenStore),
    # never the network - safe to run unmocked here exactly like the
    # gmail/calendar end-to-end checks above.
    run_cli_integrations()
    out = capsys.readouterr().out
    assert "instagram" in out


def test_run_cli_integrations_shows_instagram_not_configured_status(capsys):
    with patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=None):
        run_cli_integrations()
    out = capsys.readouterr().out
    instagram_lines = [line for line in out.splitlines() if line.startswith("instagram:")]
    assert len(instagram_lines) == 1
    assert "NOT CONFIGURED" in instagram_lines[0]


def test_run_cli_integrations_shows_instagram_connected_status_with_stored_tokens(capsys):
    import time

    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER, OAuthTokens

    tokens = OAuthTokens(
        access_token="fake-instagram-access-token-for-test",
        refresh_token="",
        expires_at=time.time() + 3600,
        scope=f"{META_INSTAGRAM_OAUTH_PROVIDER.scope}|17841400000000000",
    )
    with patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=tokens):
        run_cli_integrations()
    out = capsys.readouterr().out
    instagram_lines = [line for line in out.splitlines() if line.startswith("instagram:")]
    assert len(instagram_lines) == 1
    assert "CONNECTED" in instagram_lines[0]
    assert "no credentials registered" not in out.lower()


def test_cli_integrations_never_leaks_stored_instagram_tokens(capsys):
    import time

    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER, OAuthTokens

    tokens = OAuthTokens(
        access_token="superSecretInstagramAccessToken12345",
        refresh_token="",
        expires_at=time.time() + 3600,
        scope=f"{META_INSTAGRAM_OAUTH_PROVIDER.scope}|17841400000000000",
    )
    with patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=tokens):
        run_cli_integrations()
    out = capsys.readouterr().out
    assert "superSecretInstagramAccessToken12345" not in out


def test_run_cli_integrations_instagram_status_does_not_affect_gmail_or_calendar(capsys):
    # Registering/reporting on Instagram must never change what
    # gmail/google_calendar report - each connector's is_configured()
    # is independent.
    with patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=None):
        with patch("jarvis.integrations.connectors.gmail.TokenStore.load", return_value=None):
            with patch(
                "jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=None
            ):
                run_cli_integrations()
    out = capsys.readouterr().out
    assert "gmail: NOT CONFIGURED" in out
    assert "google_calendar: NOT CONFIGURED" in out
    assert "instagram: NOT CONFIGURED" in out


# --- main() dispatch ----------------------------------------------------------------


def test_main_dispatches_to_integrations_when_argv_is_integrations(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "integrations"])
    with patch("jarvis.cli.main.run_cli_integrations") as mock_run:
        main()
    mock_run.assert_called_once()


def test_main_does_not_start_repl_when_listing_integrations(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "integrations"])
    with patch("jarvis.cli.main.run_cli_integrations"):
        with patch("jarvis.cli.main.LLMClient") as mock_llm:
            main()
    mock_llm.assert_not_called()


def test_main_integrations_does_not_call_other_cli_entry_points(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "integrations"])
    with patch("jarvis.cli.main.run_cli_integrations"):
        with patch("jarvis.cli.main.run_cli_scan") as mock_scan:
            with patch("jarvis.cli.main.run_cli_precommit") as mock_precommit:
                main()
    mock_scan.assert_not_called()
    mock_precommit.assert_not_called()


def test_main_scan_does_not_call_run_cli_integrations(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis", "scan"])
    with patch("jarvis.cli.main.run_cli_scan"):
        with patch("jarvis.cli.main.run_cli_integrations") as mock_integrations:
            main()
    mock_integrations.assert_not_called()


def test_main_with_no_args_does_not_call_run_cli_integrations(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    monkeypatch.setattr("builtins.input", MagicMock(side_effect=EOFError))
    with patch("jarvis.cli.main.run_cli_integrations") as mock_integrations:
        with patch("jarvis.cli.main.LLMClient"):
            with patch("jarvis.cli.main.load_history") as mock_load_history:
                mock_load_history.return_value = MagicMock(warning=None, history=[])
                main()
    mock_integrations.assert_not_called()


# --- REPL 'integrations' keyword ------------------------------------------------------


def test_repl_integrations_keyword_invokes_handler(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    inputs = iter(["integrations", "exit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main._handle_integrations_command") as mock_handler:
                main()
    mock_handler.assert_called_once()


def test_repl_integrations_keyword_does_not_reach_llm_agent_turn(monkeypatch):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    inputs = iter(["integrations", "exit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            with patch("jarvis.cli.main._handle_integrations_command"):
                with patch("jarvis.cli.main._run_agent_turn") as mock_turn:
                    main()
    mock_turn.assert_not_called()


def test_repl_integrations_keyword_prints_status(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["jarvis"])
    inputs = iter(["integrations", "exit"])
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(inputs))

    with patch("jarvis.cli.main.LLMClient"):
        with patch("jarvis.cli.main.load_history") as mock_load_history:
            mock_load_history.return_value = MagicMock(warning=None, history=[])
            main()
    out = capsys.readouterr().out
    assert "email" in out


# --- no credential leakage through either surface --------------------------------------


def test_cli_integrations_never_leaks_a_configured_password(monkeypatch, capsys):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_ADDRESS", "user@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "super-secret-password-value")

    run_cli_integrations()
    out = capsys.readouterr().out
    assert "super-secret-password-value" not in out
