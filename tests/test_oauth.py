"""Tests for jarvis.integrations.oauth: the connector-agnostic OAuth
layer (token dataclass, expiry check, authorization URL construction,
OS-keychain-backed TokenStore, and the real authorization-code token
exchange). build_authorization_url() only constructs a string - no real
provider is contacted for that. exchange_code_for_tokens() DOES make a
real HTTP POST when actually called (this is the one function in this
module that talks to a real OAuth provider) - every test here mocks
urllib.request.urlopen, so no test in this suite ever makes a real
network call or requires real Google credentials.
refresh_access_token() is confirmed to remain deliberately unimplemented.
TokenStore is tested against the real `keyring` package but always
targeting a distinctive, disposable service name unique to this test
run, with an explicit teardown - never against any service name a real
connector would use, and any leftover entry is cleaned up even if a test
fails."""

from __future__ import annotations

import json
import time
import urllib.error
import uuid
from unittest.mock import MagicMock, patch

import keyring
import pytest

from jarvis.integrations.base import CredentialError
from jarvis.integrations.oauth import (
    GOOGLE_OAUTH_PROVIDER,
    MICROSOFT_OAUTH_PROVIDER,
    OAuthTokens,
    TokenStore,
    build_authorization_url,
    exchange_code_for_tokens,
    is_token_expired,
    refresh_access_token,
)


def _tokens(**overrides) -> OAuthTokens:
    defaults = dict(
        access_token="access-abc",
        refresh_token="refresh-xyz",
        expires_at=time.time() + 3600,
        scope="https://www.googleapis.com/auth/gmail.readonly",
    )
    defaults.update(overrides)
    return OAuthTokens(**defaults)


# --- OAuthTokens / is_token_expired --------------------------------------------------


def test_tokens_not_expired_when_far_in_the_future():
    tokens = _tokens(expires_at=time.time() + 3600)
    assert is_token_expired(tokens) is False


def test_tokens_expired_when_in_the_past():
    tokens = _tokens(expires_at=time.time() - 10)
    assert is_token_expired(tokens) is True


def test_tokens_expired_within_safety_margin():
    # Expires in 30s - inside the safety margin, must count as expired
    # already so a refresh happens before a request could fail mid-flight.
    tokens = _tokens(expires_at=time.time() + 30)
    assert is_token_expired(tokens) is True


def test_tokens_is_frozen():
    tokens = _tokens()
    with pytest.raises(Exception):
        tokens.access_token = "other"  # type: ignore[misc]


# --- build_authorization_url ------------------------------------------------------------


def test_authorization_url_uses_provider_endpoint():
    url = build_authorization_url(
        GOOGLE_OAUTH_PROVIDER, client_id="abc", redirect_uri="http://localhost/cb", state="s1"
    )
    assert url.startswith(GOOGLE_OAUTH_PROVIDER.authorization_endpoint)


def test_authorization_url_includes_client_id_and_state():
    url = build_authorization_url(
        GOOGLE_OAUTH_PROVIDER, client_id="my-client-id", redirect_uri="http://localhost/cb", state="xyz123"
    )
    assert "client_id=my-client-id" in url
    assert "state=xyz123" in url


def test_authorization_url_requests_offline_access_for_a_refresh_token():
    url = build_authorization_url(
        GOOGLE_OAUTH_PROVIDER, client_id="abc", redirect_uri="http://localhost/cb", state="s1"
    )
    assert "access_type=offline" in url


def test_authorization_url_requests_only_readonly_scope():
    url = build_authorization_url(
        GOOGLE_OAUTH_PROVIDER, client_id="abc", redirect_uri="http://localhost/cb", state="s1"
    )
    assert "readonly" in url
    assert "modify" not in url
    assert "send" not in url


def test_authorization_url_never_makes_a_network_call(monkeypatch):
    # Guard against a future accidental import of a network library here -
    # this function must remain pure string construction.
    import urllib.request

    def _boom(*a, **k):
        raise AssertionError("build_authorization_url must never touch the network")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    build_authorization_url(
        GOOGLE_OAUTH_PROVIDER, client_id="abc", redirect_uri="http://localhost/cb", state="s1"
    )


def test_microsoft_provider_also_works():
    url = build_authorization_url(
        MICROSOFT_OAUTH_PROVIDER, client_id="abc", redirect_uri="http://localhost/cb", state="s1"
    )
    assert url.startswith(MICROSOFT_OAUTH_PROVIDER.authorization_endpoint)


def test_meta_instagram_provider_also_works():
    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER

    url = build_authorization_url(
        META_INSTAGRAM_OAUTH_PROVIDER, client_id="abc", redirect_uri="http://localhost/cb", state="s1"
    )
    assert url.startswith(META_INSTAGRAM_OAUTH_PROVIDER.authorization_endpoint)


def test_meta_instagram_provider_scope_includes_content_publish_but_connector_has_no_publish_action():
    # The scope now includes instagram_business_content_publish (per the
    # current Instagram API with Instagram Login permissions model), but
    # granting a scope is independent from InstagramConnector actually
    # having a code path that uses it - confirm the connector's action
    # list still has no publish/create-media action, so requesting this
    # scope grants no real capability by itself.
    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER
    from jarvis.integrations.connectors.instagram import InstagramConnector

    assert "instagram_business_content_publish" in META_INSTAGRAM_OAUTH_PROVIDER.scope
    action_names = {a.name for a in InstagramConnector().actions}
    assert "publish_media" not in action_names
    assert "create_media" not in action_names


def test_meta_instagram_provider_scope_uses_current_instagram_business_permission_names():
    # The current Instagram API with Instagram Login flow uses
    # "instagram_business_*" permission names, distinct from the older
    # Facebook Login for Business flow's "instagram_basic"/
    # "instagram_manage_*"/"pages_*" names, which this provider no longer
    # requests.
    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER

    assert "instagram_business_basic" in META_INSTAGRAM_OAUTH_PROVIDER.scope
    assert "instagram_business_manage_insights" in META_INSTAGRAM_OAUTH_PROVIDER.scope
    assert "instagram_business_manage_comments" in META_INSTAGRAM_OAUTH_PROVIDER.scope
    assert "instagram_business_manage_messages" in META_INSTAGRAM_OAUTH_PROVIDER.scope


def test_meta_instagram_provider_scope_no_longer_uses_old_facebook_login_permission_names():
    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER

    scope = META_INSTAGRAM_OAUTH_PROVIDER.scope
    for old_name in ("pages_show_list", "pages_read_engagement"):
        assert old_name not in scope
    # "instagram_basic" (old) must not appear as its own token - but it IS
    # a substring of "instagram_business_basic" (new), so check word
    # boundaries via comma-splitting rather than plain substring search.
    scope_tokens = scope.split(",")
    assert "instagram_basic" not in scope_tokens
    assert "instagram_manage_insights" not in scope_tokens
    assert "instagram_manage_comments" not in scope_tokens
    assert "instagram_manage_messages" not in scope_tokens


def test_meta_instagram_provider_uses_instagram_com_authorization_endpoint():
    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER

    assert META_INSTAGRAM_OAUTH_PROVIDER.authorization_endpoint.startswith(
        "https://www.instagram.com/"
    )


def test_meta_instagram_provider_uses_api_instagram_com_token_endpoint():
    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER

    assert META_INSTAGRAM_OAUTH_PROVIDER.token_endpoint.startswith("https://api.instagram.com/")


def test_meta_instagram_provider_no_longer_uses_facebook_com_endpoints():
    from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER

    assert "facebook.com" not in META_INSTAGRAM_OAUTH_PROVIDER.authorization_endpoint
    assert "facebook.com" not in META_INSTAGRAM_OAUTH_PROVIDER.token_endpoint


def test_meta_instagram_provider_is_a_distinct_config_from_google_providers():
    from jarvis.integrations.oauth import GOOGLE_CALENDAR_OAUTH_PROVIDER, META_INSTAGRAM_OAUTH_PROVIDER

    assert META_INSTAGRAM_OAUTH_PROVIDER.token_endpoint != GOOGLE_OAUTH_PROVIDER.token_endpoint
    assert META_INSTAGRAM_OAUTH_PROVIDER.token_endpoint != GOOGLE_CALENDAR_OAUTH_PROVIDER.token_endpoint


# --- exchange_code_for_tokens: real POST to the token endpoint, always mocked ------------


def _mock_token_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def _success_payload(**overrides) -> dict:
    defaults = dict(
        access_token="new-access-token-value",
        refresh_token="new-refresh-token-value",
        expires_in=3600,
        scope=GOOGLE_OAUTH_PROVIDER.scope,
        token_type="Bearer",
    )
    defaults.update(overrides)
    return defaults


def test_exchange_code_for_tokens_success_returns_oauthtokens():
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload())):
        tokens = exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    assert isinstance(tokens, OAuthTokens)
    assert tokens.access_token == "new-access-token-value"
    assert tokens.refresh_token == "new-refresh-token-value"
    assert tokens.scope == GOOGLE_OAUTH_PROVIDER.scope


def test_exchange_code_for_tokens_sets_expires_at_from_expires_in():
    before = time.time()
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload(expires_in=1800))):
        tokens = exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    assert tokens.expires_at == pytest.approx(before + 1800, abs=5)


def test_exchange_code_for_tokens_posts_to_provider_token_endpoint():
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload())) as mock_urlopen:
        exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    request = mock_urlopen.call_args[0][0]
    assert request.full_url == GOOGLE_OAUTH_PROVIDER.token_endpoint
    assert request.get_method() == "POST"


def test_exchange_code_for_tokens_sends_grant_type_authorization_code():
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload())) as mock_urlopen:
        exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    request = mock_urlopen.call_args[0][0]
    body = request.data.decode("utf-8")
    assert "grant_type=authorization_code" in body
    assert "code=fake-code" in body
    assert "client_id=abc" in body
    assert "redirect_uri=" in body


def test_exchange_code_for_tokens_sends_client_secret_in_body():
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload())) as mock_urlopen:
        exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="my-super-secret-value",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    request = mock_urlopen.call_args[0][0]
    body = request.data.decode("utf-8")
    assert "client_secret=my-super-secret-value" in body


def test_exchange_code_for_tokens_missing_refresh_token_falls_back_to_empty_string():
    # Google omits refresh_token on a re-consent without prior revocation -
    # must not raise, must return a usable OAuthTokens with an empty one.
    payload = _success_payload()
    del payload["refresh_token"]
    with patch("urllib.request.urlopen", return_value=_mock_token_response(payload)):
        tokens = exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    assert tokens.refresh_token == ""


def test_exchange_code_for_tokens_missing_access_token_raises_credential_error():
    with patch("urllib.request.urlopen", return_value=_mock_token_response({"error": "invalid_grant"})):
        with pytest.raises(CredentialError):
            exchange_code_for_tokens(
                GOOGLE_OAUTH_PROVIDER,
                client_id="abc",
                client_secret="secret",
                redirect_uri="http://localhost/cb",
                authorization_code="bad-code",
            )


def test_exchange_code_for_tokens_http_error_raises_credential_error_not_exception():
    http_error = urllib.error.HTTPError(
        url=GOOGLE_OAUTH_PROVIDER.token_endpoint,
        code=400,
        msg="Bad Request",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": "invalid_grant"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(CredentialError) as exc_info:
            exchange_code_for_tokens(
                GOOGLE_OAUTH_PROVIDER,
                client_id="abc",
                client_secret="secret",
                redirect_uri="http://localhost/cb",
                authorization_code="bad-code",
            )
    assert "400" in str(exc_info.value)


def test_exchange_code_for_tokens_http_error_never_leaks_client_secret():
    http_error = urllib.error.HTTPError(
        url=GOOGLE_OAUTH_PROVIDER.token_endpoint,
        code=400,
        msg="Bad Request",
        hdrs=None,  # type: ignore[arg-type]
        fp=MagicMock(read=lambda: b'{"error": "my-super-secret-value leaked somehow"}'),
    )
    with patch("urllib.request.urlopen", side_effect=http_error):
        with pytest.raises(CredentialError) as exc_info:
            exchange_code_for_tokens(
                GOOGLE_OAUTH_PROVIDER,
                client_id="abc",
                client_secret="my-super-secret-value",
                redirect_uri="http://localhost/cb",
                authorization_code="bad-code",
            )
    assert "my-super-secret-value" not in str(exc_info.value)


def test_exchange_code_for_tokens_connection_error_raises_credential_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        with pytest.raises(CredentialError):
            exchange_code_for_tokens(
                GOOGLE_OAUTH_PROVIDER,
                client_id="abc",
                client_secret="secret",
                redirect_uri="http://localhost/cb",
                authorization_code="fake-code",
            )


def test_exchange_code_for_tokens_timeout_raises_credential_error():
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        with pytest.raises(CredentialError) as exc_info:
            exchange_code_for_tokens(
                GOOGLE_OAUTH_PROVIDER,
                client_id="abc",
                client_secret="secret",
                redirect_uri="http://localhost/cb",
                authorization_code="fake-code",
            )
    assert "timed out" in str(exc_info.value).lower()


def test_exchange_code_for_tokens_invalid_json_raises_credential_error():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"not valid json{{{"
    with patch("urllib.request.urlopen", return_value=cm):
        with pytest.raises(CredentialError):
            exchange_code_for_tokens(
                GOOGLE_OAUTH_PROVIDER,
                client_id="abc",
                client_secret="secret",
                redirect_uri="http://localhost/cb",
                authorization_code="fake-code",
            )


def test_exchange_code_for_tokens_never_returns_client_secret_anywhere():
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload())):
        tokens = exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="my-super-secret-value",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    assert "my-super-secret-value" not in tokens.access_token
    assert "my-super-secret-value" not in tokens.refresh_token
    assert "my-super-secret-value" not in tokens.scope


def test_exchange_code_for_tokens_sends_correct_content_type_header():
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload())) as mock_urlopen:
        exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )
    request = mock_urlopen.call_args[0][0]
    assert request.get_header("Content-type") == "application/x-www-form-urlencoded"


def test_exchange_code_for_tokens_result_is_not_persisted_by_this_function(monkeypatch):
    # exchange_code_for_tokens() must never touch the keychain itself -
    # persisting is the caller's responsibility (e.g. via TokenStore.save()).
    def _boom(*a, **k):
        raise AssertionError("exchange_code_for_tokens must never touch keyring directly")

    monkeypatch.setattr(keyring, "set_password", _boom)
    with patch("urllib.request.urlopen", return_value=_mock_token_response(_success_payload())):
        exchange_code_for_tokens(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            authorization_code="fake-code",
        )


# --- refresh_access_token remains deliberately unimplemented -----------------------------


def test_refresh_access_token_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        refresh_access_token(
            GOOGLE_OAUTH_PROVIDER,
            client_id="abc",
            client_secret="secret",
            tokens=_tokens(),
        )


# --- TokenStore: real keyring package, disposable/unique service name -------------------


@pytest.fixture
def disposable_service_name():
    # A unique name per test run/test, so this never collides with a real
    # connector's stored tokens (e.g. "email") or with another test.
    return f"test-oauth-{uuid.uuid4().hex}"


@pytest.fixture
def store(disposable_service_name):
    s = TokenStore(disposable_service_name)
    yield s
    s.clear()  # always clean up, even if the test itself fails


def test_load_returns_none_when_nothing_stored(store):
    assert store.load() is None


def test_save_then_load_round_trips(store):
    tokens = _tokens(access_token="tok-1", refresh_token="ref-1")
    assert store.save(tokens) is True

    loaded = store.load()
    assert loaded is not None
    assert loaded.access_token == "tok-1"
    assert loaded.refresh_token == "ref-1"
    assert loaded.scope == tokens.scope
    assert loaded.expires_at == pytest.approx(tokens.expires_at)


def test_save_overwrites_previous_tokens(store):
    store.save(_tokens(access_token="first"))
    store.save(_tokens(access_token="second"))
    loaded = store.load()
    assert loaded is not None
    assert loaded.access_token == "second"


def test_clear_removes_stored_tokens(store):
    store.save(_tokens())
    assert store.clear() is True
    assert store.load() is None


def test_clear_when_nothing_stored_returns_false(store):
    assert store.clear() is False


def test_describe_no_tokens(store):
    output = store.describe()
    assert "no tokens stored" in output.lower()


def test_describe_never_includes_raw_access_token(store):
    store.save(_tokens(access_token="super-secret-access-token-value"))
    output = store.describe()
    assert "super-secret-access-token-value" not in output


def test_describe_never_includes_raw_refresh_token(store):
    store.save(_tokens(refresh_token="super-secret-refresh-token-value"))
    output = store.describe()
    assert "super-secret-refresh-token-value" not in output


def test_describe_shows_expired_status(store):
    store.save(_tokens(expires_at=time.time() - 100))
    output = store.describe()
    assert "expired" in output.lower()


def test_describe_shows_valid_status(store):
    store.save(_tokens(expires_at=time.time() + 3600))
    output = store.describe()
    assert "valid" in output.lower()


def test_is_available_reports_a_bool(store):
    assert isinstance(store.is_available(), bool)


def test_corrupted_stored_value_treated_as_nothing_stored(disposable_service_name):
    keyring.set_password(f"jarvis-oauth-{disposable_service_name}", "tokens", "not-valid-token-data")
    store = TokenStore(disposable_service_name)
    assert store.load() is None
    store.clear()


def test_different_services_do_not_share_storage():
    a = TokenStore(f"test-a-{uuid.uuid4().hex}")
    b = TokenStore(f"test-b-{uuid.uuid4().hex}")
    try:
        a.save(_tokens(access_token="only-for-a"))
        assert b.load() is None
    finally:
        a.clear()
        b.clear()
