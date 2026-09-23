"""OAuth authentication layer for connectors that support it (email,
Gmail, Google Calendar). Deliberately connector-agnostic: OAuthTokens/
TokenStore know nothing about IMAP, Gmail, or any specific provider - a
connector uses this module's pieces, it doesn't extend them.

exchange_code_for_tokens() performs the real HTTP POST to a provider's
token endpoint - the one call in this module that actually talks to a
real OAuth provider. It is invoked only by a deliberate, one-time,
user-initiated "connect my account" step (never automatically, never
from within the normal agent/tool-call loop) - see this function's own
docstring for the exact flow. refresh_access_token() remains
deliberately NOT implemented (see its own docstring) - an expired token
is refused with a clear error rather than silently refreshed, so a
stale/incomplete implementation of the refresh path can never be a
silent failure mode.

Tokens are stored via the OS keychain (the `keyring` package - Windows
Credential Manager, macOS Keychain, or the Linux Secret Service,
whichever the OS provides), never in a file JARVIS manages and never in
the audit log. This is a deliberately different storage location from
the plain env-var credentials jarvis.integrations.credentials handles
(a static password/API key you set once in your shell), because OAuth
tokens are semi-persistent secrets a running process reads and refreshes
on its own - keeping them in an OS-native secret store, rather than in
this process's environment or a JSON file, is the standard model for
that lifecycle. The OAuth client_secret is never stored by this module
either - it is only ever a caller-supplied argument to
exchange_code_for_tokens(), used for exactly one HTTP request and never
persisted, logged, or echoed back in any return value.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlencode

import keyring
import keyring.errors

from jarvis.core.secrets import mask_secret
from jarvis.integrations.base import CredentialError

# Refresh this many seconds before actual expiry, so a token already
# being used for a request doesn't expire mid-flight.
_EXPIRY_SAFETY_MARGIN_SECONDS = 60

_KEYRING_SERVICE_PREFIX = "jarvis-oauth"

_TOKEN_REQUEST_TIMEOUT_SECONDS = 15


def _redact_secret_value(text: str, value: str | None) -> str:
    """Scrub a known secret value (a client_secret, an access/refresh
    token) out of arbitrary text (e.g. an exception message from
    urllib) before it can reach a raised error. Mirrors
    jarvis.integrations.connectors.stripe._redact_key()'s approach,
    applied here to whichever secret a token-endpoint call used."""
    if not value:
        return text
    return text.replace(value, "****")


@dataclass(frozen=True)
class OAuthTokens:
    """One service's OAuth token pair. access_token is the short-lived
    credential actually used to authenticate; refresh_token is the
    longer-lived credential used to obtain a new access_token without
    the user repeating the browser authorization step. expires_at is a
    Unix timestamp (seconds); scope is the space-separated granted scope
    string, kept here so a caller can confirm it never includes more
    than the read-only access it asked for.
    """

    access_token: str
    refresh_token: str
    expires_at: float
    scope: str


def is_token_expired(tokens: OAuthTokens, *, now: float | None = None) -> bool:
    """Whether tokens.access_token should be treated as expired and
    refreshed before use. Includes a small safety margin so a token is
    refreshed slightly before its real expiry, not exactly at it.
    """
    current = now if now is not None else time.time()
    return current >= (tokens.expires_at - _EXPIRY_SAFETY_MARGIN_SECONDS)


@dataclass(frozen=True)
class OAuthProviderConfig:
    """Public, non-secret configuration for one OAuth provider (endpoint
    URLs, scope) - these are published, documented values, not
    credentials. The client_id/client_secret a specific user's app
    registration uses are NOT stored here; they come from the
    environment (jarvis.integrations.credentials), exactly like every
    other credential in this codebase.
    """

    name: str
    authorization_endpoint: str
    token_endpoint: str
    scope: str


# Well-known, publicly documented endpoints - not secrets. Scopes are
# read-only mail access only, matching this connector's READ_ONLY-only
# action set; requesting a broader scope would be inconsistent with what
# the connector can actually do.
GOOGLE_OAUTH_PROVIDER = OAuthProviderConfig(
    name="google",
    authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    scope="https://www.googleapis.com/auth/gmail.readonly",
)

# A separate provider config from GOOGLE_OAUTH_PROVIDER above, even
# though both target Google's OAuth endpoints - kept distinct because the
# scope differs (calendar.readonly, not gmail.readonly) and each
# connector should only ever be able to request the specific scope its
# own action set needs. Used by
# jarvis.integrations.connectors.google_calendar.GoogleCalendarConnector.
GOOGLE_CALENDAR_OAUTH_PROVIDER = OAuthProviderConfig(
    name="google",
    authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
    token_endpoint="https://oauth2.googleapis.com/token",
    scope="https://www.googleapis.com/auth/calendar.readonly",
)

MICROSOFT_OAUTH_PROVIDER = OAuthProviderConfig(
    name="microsoft",
    authorization_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
    token_endpoint="https://login.microsoftonline.com/common/oauth2/v2.0/token",
    scope="https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
)

# Meta's Instagram API with Instagram Login flow - the current
# (post-2024) way to authorize Instagram Business/Creator API access
# directly against an Instagram account, WITHOUT requiring a linked
# Facebook Page or a Facebook Login dialog. This replaced the older
# "Facebook Login for Business" flow (facebook.com/dialog/oauth +
# graph.facebook.com token endpoint + a separate Page->Instagram Business
# Account id lookup via /me/accounts) - that older flow is no longer what
# this provider uses. Authorization happens at instagram.com directly;
# the token endpoint is api.instagram.com; and the initial token exchange
# response already includes the Instagram user id directly (no Page
# lookup step needed) - see meta_instagram_oauth_setup.py, which performs
# the exchange and derives the Instagram Business Account id from that
# response.
#
# Scope set matches what Meta's current Instagram Login permissions model
# calls them (the "instagram_business_*" names, distinct from the older
# "instagram_manage_*"/"pages_*" names the previous flow used):
# instagram_business_basic (profile/media - required baseline),
# instagram_business_manage_messages (message reading - Stage 1A only
# reads; no send action exists in InstagramConnector),
# instagram_business_manage_comments (comment reading - Stage 1A only
# reads; no reply/delete action exists in InstagramConnector),
# instagram_business_manage_insights (media insights), and
# instagram_business_content_publish. That last one is requested because
# it appears in the app's current available-permissions list and is
# needed for any future publish action - but requesting the scope grants
# no capability by itself: InstagramConnector (jarvis.integrations
# .connectors.instagram) has NO publish/create-media action implemented
# in this stage, so no code path in this codebase can currently use it.
# Adding a publish action later would still go through
# jarvis.integrations.approval.confirm_external_action() like every other
# write action in this codebase.
#
# Unlike GOOGLE_OAUTH_PROVIDER/GOOGLE_CALENDAR_OAUTH_PROVIDER, Meta's
# token endpoint does not return a standard OAuth refresh_token from this
# authorization-code exchange - Meta instead uses a separate "long-lived
# token" exchange (a plain GET request trading a short-lived token for a
# ~60-day one, via graph.instagram.com/access_token). exchange_code_for_
# tokens() below still works unmodified for the initial exchange (it only
# requires access_token in the response, and already tolerates a missing
# refresh_token - see its own docstring); the long-lived-token exchange
# step is handled entirely inside meta_instagram_oauth_setup.py, not in
# this provider-agnostic module.
META_INSTAGRAM_OAUTH_PROVIDER = OAuthProviderConfig(
    name="meta",
    authorization_endpoint="https://www.instagram.com/oauth/authorize",
    token_endpoint="https://api.instagram.com/oauth/access_token",
    scope=(
        "instagram_business_basic,instagram_business_manage_messages,"
        "instagram_business_manage_comments,instagram_business_content_publish,"
        "instagram_business_manage_insights"
    ),
)


def build_authorization_url(
    provider: OAuthProviderConfig,
    *,
    client_id: str,
    redirect_uri: str,
    state: str,
) -> str:
    """Build the URL the user opens in their own browser to grant access.
    Pure string construction - makes no network request itself. The
    resulting flow is: JARVIS shows this URL and asks the user to open it
    and approve access themselves; only after that human step does an
    authorization code exist for exchange_code_for_tokens() to use. JARVIS
    never opens the browser or submits the consent form on the user's
    behalf.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": provider.scope,
        "access_type": "offline",  # request a refresh_token, not just a short-lived access_token
        "state": state,
        "prompt": "consent",
    }
    return f"{provider.authorization_endpoint}?{urlencode(params)}"


def exchange_code_for_tokens(
    provider: OAuthProviderConfig,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    authorization_code: str,
) -> OAuthTokens:
    """Exchange a fresh authorization code for an OAuthTokens pair via a
    real HTTP POST to provider.token_endpoint - the standard OAuth 2.0
    "authorization code" grant.

    This is a one-time, user-initiated "connect my account" step, never
    called automatically or from within the normal agent/tool-call loop:
    the caller must already have a fresh `authorization_code` obtained by
    the user opening build_authorization_url()'s URL in their own browser
    and approving access there - this function performs no browser
    interaction of any kind itself.

    `client_secret` is used for exactly this one request (sent as a POST
    body field, exactly as Google's token endpoint requires) and is never
    stored, logged, or included in any return value or exception - see
    _redact_secret_value(). The returned OAuthTokens is plain data; it is
    the CALLER's responsibility to persist it (e.g. via
    TokenStore(service).save(tokens)) if it should be kept - this
    function itself never touches the keychain.

    Raises CredentialError (never a raw exception) for any failure: a
    rejected code, a network error, a timeout, or a malformed response -
    mirroring every connector's own _get()-style error handling
    elsewhere in this codebase. Never raises for an expected outcome; the
    caller decides how to present a CredentialError to the user.
    """
    body = urlencode(
        {
            "code": authorization_code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        provider.token_endpoint,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=_TOKEN_REQUEST_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise CredentialError(
            _redact_secret_value(
                f"OAuth token exchange failed ({e.code}): {error_body}", client_secret
            )
        ) from None
    except urllib.error.URLError as e:
        raise CredentialError(
            _redact_secret_value(f"OAuth token exchange connection error: {e.reason}", client_secret)
        ) from None
    except TimeoutError:
        raise CredentialError(
            f"OAuth token exchange timed out after {_TOKEN_REQUEST_TIMEOUT_SECONDS}s."
        ) from None

    try:
        payload = json.loads(response_body)
    except json.JSONDecodeError as e:
        raise CredentialError(
            _redact_secret_value(f"OAuth token endpoint returned invalid JSON: {e}", client_secret)
        ) from None

    access_token = payload.get("access_token")
    if not access_token:
        raise CredentialError(
            _redact_secret_value(
                f"OAuth token endpoint response did not include an access_token: {payload}",
                client_secret,
            )
        )

    # Google's authorization-code grant only returns a refresh_token on
    # the FIRST consent (access_type=offline + prompt=consent, both
    # already set by build_authorization_url()) - a re-authorization
    # without revoking prior access first may omit it. Falling back to an
    # empty string (rather than raising) keeps this function usable for
    # that edge case; TokenStore.save() can still store the pair, and
    # is_token_expired()-driven re-auth remains the fallback path for a
    # session with no usable refresh_token, exactly as it already is for
    # any expired token today (refresh_access_token() is unimplemented).
    refresh_token = payload.get("refresh_token", "")

    expires_in = payload.get("expires_in", 3600)  # seconds; Google's documented default-shaped fallback
    expires_at = time.time() + float(expires_in)

    return OAuthTokens(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scope=payload.get("scope", provider.scope),
    )


def refresh_access_token(
    provider: OAuthProviderConfig,
    *,
    client_id: str,
    client_secret: str,
    tokens: OAuthTokens,
) -> OAuthTokens:
    """Use tokens.refresh_token to obtain a new access_token, without
    requiring the user to repeat the browser authorization step.

    NOT IMPLEMENTED in this stage, for the same reason as
    exchange_code_for_tokens(): no real HTTP call to
    provider.token_endpoint happens here yet.
    """
    raise NotImplementedError(
        "refresh_access_token() is intentionally not implemented yet - "
        "no real OAuth token refresh happens in this stage of JARVIS."
    )


class TokenStore:
    """Stores/retrieves one service's OAuthTokens via the OS keychain
    (the `keyring` package). Never writes tokens to a file JARVIS manages
    and never logs a token value - only jarvis.core.secrets.mask_secret()
    -masked previews are ever surfaced for display.
    """

    def __init__(self, service: str) -> None:
        self._keyring_service = f"{_KEYRING_SERVICE_PREFIX}-{service}"

    def is_available(self) -> bool:
        """Whether a usable OS keychain backend exists on this system.
        False on a system with no keychain (rare, but possible in some
        minimal/headless environments) - callers must treat that as
        "OAuth storage unavailable", not attempt to fall back to writing
        tokens to a plain file."""
        try:
            backend = keyring.get_keyring()
        except Exception:
            return False
        # keyring's "fail" backend answers get()/set() without raising,
        # but can't actually persist anything - explicitly excluded so
        # is_available() doesn't lie about a functioning keychain.
        return backend is not None and "fail" not in type(backend).__module__

    def load(self) -> OAuthTokens | None:
        """Return the stored tokens, or None if none are stored yet or
        the keychain is unavailable/unreadable. Never raises."""
        try:
            raw = keyring.get_password(self._keyring_service, "tokens")
        except keyring.errors.KeyringError:
            return None
        if not raw:
            return None
        try:
            access_token, refresh_token, expires_at, scope = raw.split("\x1f", 3)
            return OAuthTokens(
                access_token=access_token,
                refresh_token=refresh_token,
                expires_at=float(expires_at),
                scope=scope,
            )
        except (ValueError, TypeError):
            return None  # corrupted entry - treat as "nothing stored"

    def save(self, tokens: OAuthTokens) -> bool:
        """Persist tokens to the OS keychain. Returns True on success,
        False if the keychain is unavailable or the write failed - never
        raises, and never falls back to writing the tokens anywhere else
        (a file, an env var, a log)."""
        raw = "\x1f".join(
            [tokens.access_token, tokens.refresh_token, str(tokens.expires_at), tokens.scope]
        )
        try:
            keyring.set_password(self._keyring_service, "tokens", raw)
            return True
        except keyring.errors.KeyringError:
            return False

    def clear(self) -> bool:
        """Remove any stored tokens for this service ("disconnect
        account"). Returns True if a stored entry was removed, False if
        there was nothing stored or the keychain is unavailable - both
        are treated as "nothing to do", not an error."""
        try:
            keyring.delete_password(self._keyring_service, "tokens")
            return True
        except keyring.errors.PasswordDeleteError:
            return False
        except keyring.errors.KeyringError:
            return False

    def describe(self) -> str:
        """Human-readable, masked status - never the raw token value."""
        tokens = self.load()
        if tokens is None:
            return f"{self._keyring_service}: no tokens stored."
        status = "expired" if is_token_expired(tokens) else "valid"
        return (
            f"{self._keyring_service}: tokens stored ({status}), "
            f"access_token={mask_secret(tokens.access_token)}, "
            f"scope={tokens.scope}."
        )
