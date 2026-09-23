"""Tests for jarvis.integrations.manager.IntegrationsManager: the single
place to see every integration's status (NOT_CONFIGURED/CONNECTED/
DISCONNECTED/ERROR) and connect/disconnect a service, built on top of
IntegrationRegistry. No real connector is ever actually connected here -
connect()/disconnect() never perform a network call or an OAuth
exchange, and no test in this file touches a real external service. The
real EmailConnector is exercised as the first concrete example, with its
TokenStore mocked (never the real OS keychain) - the pure four-state
model itself is tested via minimal fake connectors."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.integrations.base import Connector, ExternalActionResult
from jarvis.integrations.connectors.email import EmailConnector
from jarvis.integrations.connectors.gmail import GmailConnector
from jarvis.integrations.connectors.google_calendar import GoogleCalendarConnector
from jarvis.integrations.connectors.instagram import InstagramConnector
from jarvis.integrations.connectors.stripe import StripeConnector
from jarvis.integrations.manager import IntegrationStatus, IntegrationsManager
from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER, OAuthTokens
from jarvis.integrations.registry import IntegrationRegistry


class _FakeConnector(Connector):
    def __init__(self, service_name: str, configured: bool = False, disconnect_result: bool = False):
        self.service_name = service_name
        self.actions = []
        self._configured = configured
        self._disconnect_result = disconnect_result
        self.disconnect_called = False

    def is_configured(self) -> bool:
        return self._configured

    def execute(self, action_name: str, **kwargs) -> ExternalActionResult:
        return ExternalActionResult(ok=True, output="", service=self.service_name, action=action_name)

    def disconnect(self) -> bool:
        self.disconnect_called = True
        return self._disconnect_result


class _RaisingConnector(Connector):
    service_name = "raising_service"
    actions = []

    def is_configured(self) -> bool:
        raise RuntimeError("boom")

    def execute(self, action_name: str, **kwargs) -> ExternalActionResult:
        return ExternalActionResult(ok=False, output="", service=self.service_name, action=action_name)


@pytest.fixture
def registry():
    return IntegrationRegistry()


# --- get_status: unregistered / basic states -----------------------------------------


def test_get_status_unregistered_service_is_error(registry):
    manager = IntegrationsManager(registry)
    info = manager.get_status("nonexistent")
    assert info.status == IntegrationStatus.ERROR
    assert "not a registered integration" in info.detail


def test_get_status_unconfigured_connector_is_not_configured(registry):
    registry.register(_FakeConnector("fake_a", configured=False))
    manager = IntegrationsManager(registry)
    info = manager.get_status("fake_a")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_get_status_configured_connector_is_connected(registry):
    registry.register(_FakeConnector("fake_a", configured=True))
    manager = IntegrationsManager(registry)
    info = manager.get_status("fake_a")
    assert info.status == IntegrationStatus.CONNECTED


def test_get_status_raises_internally_becomes_error(registry):
    registry.register(_RaisingConnector())
    manager = IntegrationsManager(registry)
    info = manager.get_status("raising_service")
    assert info.status == IntegrationStatus.ERROR
    assert "boom" in info.detail


# --- list_statuses ---------------------------------------------------------------------


def test_list_statuses_returns_all_registered(registry):
    registry.register(_FakeConnector("fake_a", configured=True))
    registry.register(_FakeConnector("fake_b", configured=False))
    manager = IntegrationsManager(registry)

    statuses = manager.list_statuses()
    by_name = {s.service_name: s.status for s in statuses}
    assert by_name == {
        "fake_a": IntegrationStatus.CONNECTED,
        "fake_b": IntegrationStatus.NOT_CONFIGURED,
    }


def test_list_statuses_empty_registry_returns_empty_list(registry):
    manager = IntegrationsManager(registry)
    assert manager.list_statuses() == []


# --- is_configured convenience method --------------------------------------------------


def test_is_configured_true(registry):
    registry.register(_FakeConnector("fake_a", configured=True))
    manager = IntegrationsManager(registry)
    assert manager.is_configured("fake_a") is True


def test_is_configured_false_for_unregistered(registry):
    manager = IntegrationsManager(registry)
    assert manager.is_configured("nonexistent") is False


# --- disconnect: DISCONNECTED state ----------------------------------------------------


def test_disconnect_unconfigured_connector_marks_disconnected(registry):
    connector = _FakeConnector("fake_a", configured=False, disconnect_result=False)
    registry.register(connector)
    manager = IntegrationsManager(registry)

    info = manager.disconnect("fake_a")
    assert info.status == IntegrationStatus.DISCONNECTED
    assert connector.disconnect_called is True


def test_disconnect_then_get_status_stays_disconnected(registry):
    registry.register(_FakeConnector("fake_a", configured=False))
    manager = IntegrationsManager(registry)

    manager.disconnect("fake_a")
    info = manager.get_status("fake_a")
    assert info.status == IntegrationStatus.DISCONNECTED


def test_disconnect_unregistered_service_is_error(registry):
    manager = IntegrationsManager(registry)
    info = manager.disconnect("nonexistent")
    assert info.status == IntegrationStatus.ERROR


def test_disconnect_raising_connector_returns_error_not_exception(registry):
    class _RaisingDisconnect(Connector):
        service_name = "raising_disconnect"
        actions = []

        def is_configured(self) -> bool:
            return True

        def execute(self, action_name: str, **kwargs) -> ExternalActionResult:
            return ExternalActionResult(ok=True, output="", service=self.service_name, action=action_name)

        def disconnect(self) -> bool:
            raise RuntimeError("disconnect boom")

    registry.register(_RaisingDisconnect())
    manager = IntegrationsManager(registry)
    info = manager.disconnect("raising_disconnect")
    assert info.status == IntegrationStatus.ERROR
    assert "disconnect boom" in info.detail


def test_disconnect_not_supported_by_connector_notes_it_in_detail(registry):
    # disconnect() defaults to False (not supported) on the base
    # Connector class - the manager must still mark the service as
    # explicitly disconnected for status purposes, but say nothing was
    # actually cleared.
    registry.register(_FakeConnector("fake_a", configured=False, disconnect_result=False))
    manager = IntegrationsManager(registry)
    info = manager.disconnect("fake_a")
    assert "no locally-stored credential" in info.detail.lower()


def test_disconnecting_a_currently_connected_service_still_reports_connected_if_still_configured(registry):
    # disconnect() clears local state, but if is_configured() is still
    # True afterward (e.g. a password-only connector whose env vars are
    # still set - disconnect() didn't touch them), the manager reports
    # the actual current state (CONNECTED), not a fictional DISCONNECTED.
    connector = _FakeConnector("fake_a", configured=True, disconnect_result=True)
    registry.register(connector)
    manager = IntegrationsManager(registry)

    info = manager.disconnect("fake_a")
    assert info.status == IntegrationStatus.CONNECTED


# --- connect: clears the disconnected marker, never performs a real OAuth exchange -------


def test_connect_unregistered_service_is_error(registry):
    manager = IntegrationsManager(registry)
    info = manager.connect("nonexistent")
    assert info.status == IntegrationStatus.ERROR


def test_connect_unconfigured_connector_reports_not_configured(registry):
    registry.register(_FakeConnector("fake_a", configured=False))
    manager = IntegrationsManager(registry)
    info = manager.connect("fake_a")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_connect_configured_connector_reports_connected(registry):
    registry.register(_FakeConnector("fake_a", configured=True))
    manager = IntegrationsManager(registry)
    info = manager.connect("fake_a")
    assert info.status == IntegrationStatus.CONNECTED


def test_connect_after_disconnect_clears_disconnected_marker(registry):
    connector = _FakeConnector("fake_a", configured=False)
    registry.register(connector)
    manager = IntegrationsManager(registry)

    manager.disconnect("fake_a")
    assert manager.get_status("fake_a").status == IntegrationStatus.DISCONNECTED

    # Simulate the connector becoming configured (e.g. credentials were
    # provided) and reconnecting.
    connector._configured = True
    info = manager.connect("fake_a")
    assert info.status == IntegrationStatus.CONNECTED


def test_connected_status_supersedes_stale_disconnected_marker_without_explicit_connect(registry):
    connector = _FakeConnector("fake_a", configured=False)
    registry.register(connector)
    manager = IntegrationsManager(registry)

    manager.disconnect("fake_a")
    connector._configured = True
    # Even without calling connect() again, a subsequent get_status()
    # must reflect the connector actually being configured now, not the
    # stale disconnected marker.
    info = manager.get_status("fake_a")
    assert info.status == IntegrationStatus.CONNECTED


def test_connect_never_calls_oauth_token_exchange(registry, monkeypatch):
    from jarvis.integrations import oauth

    def _boom(*a, **k):
        raise AssertionError("connect() must never perform a real OAuth token exchange")

    monkeypatch.setattr(oauth, "exchange_code_for_tokens", _boom)
    registry.register(_FakeConnector("fake_a", configured=False))
    manager = IntegrationsManager(registry)
    manager.connect("fake_a")  # must not raise the assertion above


# --- credentials are never leaked in any status detail ----------------------------------


def test_status_detail_never_contains_raw_env_credential_value(registry, monkeypatch):
    from jarvis.integrations import credentials as creds_module

    monkeypatch.setenv("FAKE_SECRET_VAR", "super-secret-raw-value")
    creds_module.register_connector_env_vars("fake_a", ["FAKE_SECRET_VAR"])
    registry.register(_FakeConnector("fake_a", configured=True))
    manager = IntegrationsManager(registry)

    info = manager.get_status("fake_a")
    assert "super-secret-raw-value" not in info.detail


def test_status_info_is_safe_to_str_without_leaking(registry, monkeypatch):
    from jarvis.integrations import credentials as creds_module

    monkeypatch.setenv("ANOTHER_SECRET", "another-raw-secret-value")
    creds_module.register_connector_env_vars("fake_b", ["ANOTHER_SECRET"])
    registry.register(_FakeConnector("fake_b", configured=True))
    manager = IntegrationsManager(registry)

    info = manager.get_status("fake_b")
    assert "another-raw-secret-value" not in str(info)


# --- real EmailConnector as the first concrete example (TokenStore mocked) --------------


def test_email_connector_not_configured_reports_not_configured(registry, monkeypatch):
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        registry.register(EmailConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("email")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_email_connector_password_configured_reports_connected(registry, monkeypatch):
    monkeypatch.setenv("EMAIL_IMAP_HOST", "imap.example.com")
    monkeypatch.setenv("EMAIL_ADDRESS", "user@example.com")
    monkeypatch.setenv("EMAIL_PASSWORD", "app-password")
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        registry.register(EmailConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("email")
    assert info.status == IntegrationStatus.CONNECTED


def test_email_connector_oauth_configured_reports_connected_without_env_vars(registry, monkeypatch):
    import time

    from jarvis.integrations.oauth import OAuthTokens

    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    tokens = OAuthTokens(
        access_token="superSecretAccessTokenValue12345",
        refresh_token="superSecretRefreshTokenValue12345",
        expires_at=time.time() + 3600,
        scope="gmail.readonly",
    )
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=tokens):
        registry.register(EmailConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("email")
    assert info.status == IntegrationStatus.CONNECTED
    assert "superSecretAccessTokenValue12345" not in info.detail
    assert "superSecretRefreshTokenValue12345" not in info.detail


def test_email_connector_disconnect_clears_oauth_tokens(registry, monkeypatch):
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    with patch("jarvis.integrations.connectors.email.TokenStore.clear", return_value=True) as mock_clear:
        with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
            registry.register(EmailConnector())
            manager = IntegrationsManager(registry)
            info = manager.disconnect("email")
    mock_clear.assert_called_once()
    assert info.status == IntegrationStatus.DISCONNECTED


def test_email_connector_registered_in_a_registry_appears_in_list_statuses(registry, monkeypatch):
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        registry.register(EmailConnector())
        manager = IntegrationsManager(registry)
        statuses = manager.list_statuses()
    assert len(statuses) == 1
    assert statuses[0].service_name == "email"


# --- real StripeConnector as the second concrete example (urllib mocked) ----------------


def test_stripe_connector_not_configured_reports_not_configured(registry, monkeypatch):
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    registry.register(StripeConnector())
    manager = IntegrationsManager(registry)
    info = manager.get_status("stripe")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_stripe_connector_configured_reports_connected(registry, monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_someKeyValue12345")
    registry.register(StripeConnector())
    manager = IntegrationsManager(registry)
    info = manager.get_status("stripe")
    assert info.status == IntegrationStatus.CONNECTED


def test_stripe_connector_status_never_leaks_api_key(registry, monkeypatch):
    monkeypatch.setenv("STRIPE_API_KEY", "sk_test_superSecretStripeKeyValue12345")
    registry.register(StripeConnector())
    manager = IntegrationsManager(registry)
    info = manager.get_status("stripe")
    assert "sk_test_superSecretStripeKeyValue12345" not in info.detail
    assert "sk_test_superSecretStripeKeyValue12345" not in str(info)


def test_stripe_connector_disconnect_not_supported_marks_disconnected_anyway(registry, monkeypatch):
    # StripeConnector has no locally-managed secret to clear (its only
    # credential is a plain env var) - disconnect() falls back to the
    # base Connector's default (False, not supported), and the manager
    # still marks it as explicitly disconnected for status purposes.
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    registry.register(StripeConnector())
    manager = IntegrationsManager(registry)
    info = manager.disconnect("stripe")
    assert info.status == IntegrationStatus.DISCONNECTED
    assert "no locally-stored credential" in info.detail.lower()


def test_email_and_stripe_both_registered_appear_together_in_list_statuses(registry, monkeypatch):
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        registry.register(EmailConnector())
        registry.register(StripeConnector())
        manager = IntegrationsManager(registry)
        statuses = manager.list_statuses()

    names = {s.service_name for s in statuses}
    assert names == {"email", "stripe"}
    assert all(s.status == IntegrationStatus.NOT_CONFIGURED for s in statuses)


# --- real GoogleCalendarConnector as the third concrete example (OAuth-only, TokenStore mocked) --


def _calendar_tokens(**overrides) -> OAuthTokens:
    import time

    defaults = dict(
        access_token="superSecretCalendarAccessToken12345",
        refresh_token="superSecretCalendarRefreshToken12345",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/calendar.readonly",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


def test_google_calendar_connector_not_configured_reports_not_configured(registry):
    with patch("jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=None):
        registry.register(GoogleCalendarConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("google_calendar")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_google_calendar_connector_oauth_configured_reports_connected(registry):
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.load",
        return_value=_calendar_tokens(),
    ):
        registry.register(GoogleCalendarConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("google_calendar")
    assert info.status == IntegrationStatus.CONNECTED


def test_google_calendar_connector_connected_detail_does_not_say_no_credentials_registered(registry):
    # Regression test: an OAuth-only connector (no env vars registered at
    # all, so CredentialStatus.required_vars is empty) must not have its
    # CONNECTED detail overridden by credentials.format_credential_status()'s
    # "no credentials registered." placeholder text, which is meant for a
    # genuinely unregistered/unused credential shape, not a connected
    # OAuth-only one - see jarvis.integrations.manager
    # ._status_for_connector()'s status_for_env_vars.required_vars check.
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.load",
        return_value=_calendar_tokens(),
    ):
        registry.register(GoogleCalendarConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("google_calendar")
    assert info.status == IntegrationStatus.CONNECTED
    assert "no credentials registered" not in info.detail.lower()
    assert "oauth" in info.detail.lower()


def test_google_calendar_connector_status_never_leaks_tokens(registry):
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.load",
        return_value=_calendar_tokens(),
    ):
        registry.register(GoogleCalendarConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("google_calendar")
    assert "superSecretCalendarAccessToken12345" not in info.detail
    assert "superSecretCalendarRefreshToken12345" not in info.detail
    assert "superSecretCalendarAccessToken12345" not in str(info)


def test_google_calendar_connector_disconnect_clears_oauth_tokens(registry):
    with patch(
        "jarvis.integrations.connectors.google_calendar.TokenStore.clear", return_value=True
    ) as mock_clear:
        with patch(
            "jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=None
        ):
            registry.register(GoogleCalendarConnector())
            manager = IntegrationsManager(registry)
            info = manager.disconnect("google_calendar")
    mock_clear.assert_called_once()
    assert info.status == IntegrationStatus.DISCONNECTED


def test_google_calendar_connector_has_no_env_var_credential_path(registry, monkeypatch):
    # Confirms the manager-level status also reflects the OAuth-only
    # design - setting unrelated env vars must not flip the status.
    monkeypatch.setenv("GOOGLE_CALENDAR_SOME_VAR", "irrelevant")
    with patch("jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=None):
        registry.register(GoogleCalendarConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("google_calendar")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_all_three_connectors_registered_appear_together_in_list_statuses(registry, monkeypatch):
    monkeypatch.delenv("EMAIL_IMAP_HOST", raising=False)
    monkeypatch.delenv("EMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    monkeypatch.delenv("STRIPE_API_KEY", raising=False)
    with patch("jarvis.integrations.connectors.email.TokenStore.load", return_value=None):
        with patch(
            "jarvis.integrations.connectors.google_calendar.TokenStore.load", return_value=None
        ):
            registry.register(EmailConnector())
            registry.register(StripeConnector())
            registry.register(GoogleCalendarConnector())
            manager = IntegrationsManager(registry)
            statuses = manager.list_statuses()

    names = {s.service_name for s in statuses}
    assert names == {"email", "stripe", "google_calendar"}
    assert all(s.status == IntegrationStatus.NOT_CONFIGURED for s in statuses)


# --- real GmailConnector as the fourth concrete example (OAuth-only, TokenStore mocked) --


def _gmail_tokens(**overrides) -> OAuthTokens:
    import time

    defaults = dict(
        access_token="superSecretGmailAccessToken12345",
        refresh_token="superSecretGmailRefreshToken12345",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


def test_gmail_connector_not_configured_reports_not_configured(registry):
    with patch("jarvis.integrations.connectors.gmail.TokenStore.load", return_value=None):
        registry.register(GmailConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("gmail")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_gmail_connector_oauth_configured_reports_connected(registry):
    with patch(
        "jarvis.integrations.connectors.gmail.TokenStore.load", return_value=_gmail_tokens()
    ):
        registry.register(GmailConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("gmail")
    assert info.status == IntegrationStatus.CONNECTED


def test_gmail_connector_connected_detail_does_not_say_no_credentials_registered(registry):
    # The exact bug report this test guards against: real OAuth tokens
    # stored in the OS keychain for Gmail previously still produced the
    # "gmail: no credentials registered." detail text (meant for a
    # service with nothing configured at all) even while status correctly
    # read CONNECTED - see jarvis.integrations.manager
    # ._status_for_connector()'s status_for_env_vars.required_vars fix.
    with patch(
        "jarvis.integrations.connectors.gmail.TokenStore.load", return_value=_gmail_tokens()
    ):
        registry.register(GmailConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("gmail")
    assert info.status == IntegrationStatus.CONNECTED
    assert "no credentials registered" not in info.detail.lower()
    assert "oauth" in info.detail.lower()


def test_gmail_connector_status_never_leaks_tokens(registry):
    with patch(
        "jarvis.integrations.connectors.gmail.TokenStore.load", return_value=_gmail_tokens()
    ):
        registry.register(GmailConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("gmail")
    assert "superSecretGmailAccessToken12345" not in info.detail
    assert "superSecretGmailRefreshToken12345" not in info.detail
    assert "superSecretGmailAccessToken12345" not in str(info)


def test_gmail_connector_disconnect_clears_oauth_tokens(registry):
    with patch("jarvis.integrations.connectors.gmail.TokenStore.clear", return_value=True) as mock_clear:
        with patch("jarvis.integrations.connectors.gmail.TokenStore.load", return_value=None):
            registry.register(GmailConnector())
            manager = IntegrationsManager(registry)
            manager.disconnect("gmail")
    mock_clear.assert_called_once()


# --- real InstagramConnector as the fifth concrete example (OAuth-only, TokenStore mocked) --


def _instagram_tokens(**overrides) -> OAuthTokens:
    import time

    defaults = dict(
        access_token="superSecretInstagramAccessToken12345",
        refresh_token="",
        expires_at=time.time() + 3600,
        scope=f"{META_INSTAGRAM_OAUTH_PROVIDER.scope}|17841400000000000",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


def test_instagram_connector_not_configured_reports_not_configured(registry):
    with patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=None):
        registry.register(InstagramConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("instagram")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_instagram_connector_oauth_configured_reports_connected(registry):
    with patch(
        "jarvis.integrations.connectors.instagram.TokenStore.load",
        return_value=_instagram_tokens(),
    ):
        registry.register(InstagramConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("instagram")
    assert info.status == IntegrationStatus.CONNECTED


def test_instagram_connector_connected_detail_does_not_say_no_credentials_registered(registry):
    with patch(
        "jarvis.integrations.connectors.instagram.TokenStore.load",
        return_value=_instagram_tokens(),
    ):
        registry.register(InstagramConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("instagram")
    assert info.status == IntegrationStatus.CONNECTED
    assert "no credentials registered" not in info.detail.lower()
    assert "oauth" in info.detail.lower()


def test_instagram_connector_status_never_leaks_tokens(registry):
    with patch(
        "jarvis.integrations.connectors.instagram.TokenStore.load",
        return_value=_instagram_tokens(),
    ):
        registry.register(InstagramConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("instagram")
    assert "superSecretInstagramAccessToken12345" not in info.detail
    assert "superSecretInstagramAccessToken12345" not in str(info)


def test_instagram_connector_disconnect_clears_oauth_tokens(registry):
    with patch(
        "jarvis.integrations.connectors.instagram.TokenStore.clear", return_value=True
    ) as mock_clear:
        with patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=None):
            registry.register(InstagramConnector())
            manager = IntegrationsManager(registry)
            manager.disconnect("instagram")
    mock_clear.assert_called_once()


def test_instagram_connector_has_no_env_var_credential_path(registry, monkeypatch):
    monkeypatch.setenv("INSTAGRAM_APP_SECRET", "some-value")
    with patch("jarvis.integrations.connectors.instagram.TokenStore.load", return_value=None):
        registry.register(InstagramConnector())
        manager = IntegrationsManager(registry)
        info = manager.get_status("instagram")
    assert info.status == IntegrationStatus.NOT_CONFIGURED


def test_all_four_oauth_connectors_registered_appear_together_independently(registry):
    # Gmail/Calendar/Instagram each report their own status independently
    # - registering all together must never cross-contaminate status.
    # TokenStore.load is the SAME underlying method object regardless of
    # which connector module imports it (all three "from
    # jarvis.integrations.oauth import TokenStore"), so a single
    # side_effect keyed by keyring service name is used here instead of
    # three separate return_value patches, which would otherwise just
    # overwrite each other on the one shared TokenStore.load.
    def _load_side_effect(token_store_self):
        if token_store_self._keyring_service == "jarvis-oauth-instagram":
            return _instagram_tokens()
        return None

    with patch("jarvis.integrations.oauth.TokenStore.load", autospec=True, side_effect=_load_side_effect):
        registry.register(GmailConnector())
        registry.register(GoogleCalendarConnector())
        registry.register(InstagramConnector())
        manager = IntegrationsManager(registry)
        statuses = {s.service_name: s.status for s in manager.list_statuses()}

    assert statuses["gmail"] == IntegrationStatus.NOT_CONFIGURED
    assert statuses["google_calendar"] == IntegrationStatus.NOT_CONFIGURED
    assert statuses["instagram"] == IntegrationStatus.CONNECTED
