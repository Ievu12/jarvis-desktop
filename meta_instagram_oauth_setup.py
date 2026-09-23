"""Vienkartinis Instagram (Meta Instagram API with Instagram Login) OAuth
prijungimo scenarijus.

NĖRA JARVIS projekto dalis — nekuria naujo failo projekte, nekeičia
jokio esamo kodo. Paleidžiamas tiesiai iš JARVIS venv, kad turėtų
prieigą prie jau įgyvendintų funkcijų.

Visiškai ATSKIRTAS nuo Gmail ir Google Calendar prijungimo: naudoja savo
Meta OAuth Client (App ID/App Secret - visai kitas dalykas nei Google
Client ID/Secret), savo scope (žr. jarvis.integrations.oauth
.META_INSTAGRAM_OAUTH_PROVIDER) ir savo tokenų saugyklą
(TokenStore("instagram") — atskiras Windows Credential Manager įrašas
nuo "jarvis-oauth-gmail"/"jarvis-oauth-google_calendar"). Paleidus šį
scenarijų, Gmail ir Google Calendar OAuth tokenai NELIEČIAMI ir
NEKEIČIAMI.

Naudoja DABARTINĮ (post-2024) "Instagram API with Instagram Login" srautą
- autorizacija vyksta tiesiogiai instagram.com, token exchange per
api.instagram.com, o pradinis atsakymas jau grąžina Instagram Business
Account ID (user_id lauke) TIESIOGIAI - NEBEREIKIA atskiros paieškos per
susietą Facebook Page (tai buvo senesnio "Facebook Login for Business"
srauto žingsnis ir šiame scenarijuje jo nebėra).

Meta reikalauja VIENO papildomo žingsnio po pradinio authorization-code
exchange: trumpaamžio (short-lived) tokeno keitimas į ilgaamžį
(long-lived, ~60 dienų) - paprastas GET užklausimas per
graph.instagram.com/access_token, ne refresh_token grant.

SVARBU: šis scenarijus SĄMONINGAI NEnaudoja jarvis.integrations.oauth
.exchange_code_for_tokens() pradiniam authorization-code exchange
žingsniui - ta bendroji (Gmail/Calendar naudojama) funkcija siunčia
'application/x-www-form-urlencoded' POST kūną, kurį Google/Microsoft
token endpoint'ai priima, bet Meta's Instagram Login token endpoint'as
(api.instagram.com/oauth/access_token) REIKALAUJA 'multipart/form-data'
- kitokio kūno formato naudojimas grąžina Meta klaidą "400 Invalid
platform app". Šis scenarijus todėl turi savo, Instagram-specifinę
_exchange_authorization_code() funkciją tiksliai šiam žingsniui.

Šis scenarijus TAIP PAT SĄMONINGAI NEnaudoja jarvis.integrations.oauth
.build_authorization_url() autorizacijos URL sudarymui -  ta bendroji
funkcija prideda 'access_type=offline' ir 'prompt=consent' parametrus,
kurie yra Google OAuth 2.0-specifiniai (naudojami refresh_token gavimui)
ir kurių dokumentuotas Instagram Login autorizacijos URL formatas
NEATPAŽĮSTA - jų buvimas galėjo prisidėti prie "Invalid platform app"
klaidos token exchange žingsnyje. Instagram Login dokumentuotas
autorizacijos URL formatas yra tiksliai:
  https://www.instagram.com/oauth/authorize?client_id=...&redirect_uri=...
  &response_type=code&scope=...
- be jokių papildomų parametrų. Šis scenarijus todėl turi savo,
Instagram-specifinę _build_instagram_authorization_url() funkciją
tiksliai šiam žingsniui.

Visi keturi Instagram-specifiniai žingsniai (authorization URL sudarymas,
authorization-code exchange, account ID paieška, long-lived token
exchange) dabar yra šiame scenarijuje, savomis funkcijomis. jarvis
.integrations.oauth.py (bendrasis, connector-agnostic modulis, kurį
naudoja Gmail/Calendar) NĖRA keičiamas dėl jokio šių Meta-specifinių
reikalavimų - jo build_authorization_url()/exchange_code_for_tokens()
lieka tiksliai tokie patys, kokie buvo, ir toliau tinka Google/Microsoft
OAuth srautams.

Niekas iš įvestų reikšmių (app_secret, authorization_code, tokenai)
niekada nerašomi į failą, log'ą ar terminalo istoriją už šios sesijos
ribų — app_secret įvedamas per getpass (nerodomas ekrane), tokenai
įrašomi tik į OS keychain per TokenStore.
"""

import getpass
import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from jarvis.integrations.oauth import META_INSTAGRAM_OAUTH_PROVIDER, OAuthTokens, TokenStore

_GRAPH_INSTAGRAM_API_BASE = "https://graph.instagram.com"
_REQUEST_TIMEOUT_SECONDS = 15
_DEFAULT_REDIRECT_URI = "https://oauth.pstmn.io/v1/callback"


def _redact(text: str, *secrets: str) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "****")
    return text


def _instagram_graph_get(path: str, params: dict, *, app_secret_for_redaction: str = "") -> dict:
    url = f"{_GRAPH_INSTAGRAM_API_BASE}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            _redact(f"Graph API request failed ({e.code}): {error_body}", app_secret_for_redaction)
        ) from None
    except urllib.error.URLError as e:
        raise RuntimeError(
            _redact(f"Graph API connection error: {e.reason}", app_secret_for_redaction)
        ) from None
    return json.loads(body)


def _build_multipart_body(fields: dict) -> tuple[bytes, str]:
    """Build a multipart/form-data request body from plain string
    fields - stdlib only (no 'requests' dependency, matching the rest of
    this codebase's urllib-only approach). Returns (body_bytes,
    content_type_header_value)."""
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f"--{boundary}\r\n")
        parts.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n')
        parts.append(f"{value}\r\n")
    parts.append(f"--{boundary}--\r\n")
    body = "".join(parts).encode("utf-8")
    content_type = f"multipart/form-data; boundary={boundary}"
    return body, content_type


def _build_instagram_authorization_url(*, client_id: str, redirect_uri: str) -> str:
    """Build the Instagram Login authorization URL the user opens in
    their own browser - pure string construction, no network request.

    Deliberately NOT jarvis.integrations.oauth.build_authorization_url():
    that shared function adds 'access_type=offline' and 'prompt=consent'
    query parameters, which are Google OAuth 2.0-specific (requesting a
    refresh_token) and are not part of Meta's documented Instagram Login
    authorization URL format. Meta's documented format is exactly:
      https://www.instagram.com/oauth/authorize?client_id=...
      &redirect_uri=...&response_type=code&scope=...
    - only these four parameters. No 'state' parameter is sent either,
    matching Meta's own reference examples for this specific endpoint;
    this is a one-time, manually-driven local flow (the user pastes the
    'code' back by hand, not an automated redirect handler), so CSRF
    protection via 'state' has no practical role here the way it would
    for an automated web server callback.
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": META_INSTAGRAM_OAUTH_PROVIDER.scope,
    }
    return f"{META_INSTAGRAM_OAUTH_PROVIDER.authorization_endpoint}?{urllib.parse.urlencode(params)}"


def _exchange_authorization_code(
    *, client_id: str, client_secret: str, redirect_uri: str, authorization_code: str,
) -> dict:
    """Exchange a fresh authorization code for a short-lived access token
    via Meta's Instagram Login token endpoint - a multipart/form-data
    POST, per Meta's own API documentation for this specific endpoint.
    This is intentionally separate from jarvis.integrations.oauth
    .exchange_code_for_tokens(), which sends
    application/x-www-form-urlencoded and works correctly for
    Google/Microsoft's token endpoints but is rejected by Meta's
    Instagram Login one (400 Invalid platform app)."""
    body, content_type = _build_multipart_body(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
            "code": authorization_code,
        }
    )
    request = urllib.request.Request(
        META_INSTAGRAM_OAUTH_PROVIDER.token_endpoint,
        data=body,
        headers={"Content-Type": content_type},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            _redact(f"Authorization code exchange failed ({e.code}): {error_body}", client_secret)
        ) from None
    except urllib.error.URLError as e:
        raise RuntimeError(
            _redact(f"Authorization code exchange connection error: {e.reason}", client_secret)
        ) from None
    return json.loads(response_body)


print("=== Instagram (Instagram API with Instagram Login) OAuth prijungimas ===\n")
print("Pastaba: šis scenarijus NELIEČIA jūsų jau veikiančio Gmail/Calendar prijungimo.\n")
print(
    "Reikalavimas: jūsų Instagram paskyra turi būti Business arba Creator tipo. "
    "Šis srautas nebereikalauja susietos Facebook Page.\n"
)

print(
    "SVARBU: Instagram Login API naudoja SAVO, Instagram-specifinį App ID - "
    "jis GALI SKIRTIS nuo bendro Facebook App ID, matomo App Dashboard -> "
    "Settings -> Basic. Instagram-specifinį App ID rasite: App Dashboard -> "
    "Instagram -> API setup with Instagram Login -> Business login settings.\n"
)

client_id = input("Įveskite jūsų Instagram App ID (iš Instagram Business Login nustatymų): ").strip()
client_secret = getpass.getpass("Įveskite jūsų Instagram App Secret (nebus rodomas): ").strip()
redirect_uri = input(
    f"Įveskite Redirect URI (turi tiksliai sutapti su jūsų Meta App Instagram "
    f"Business Login nustatymais; Enter = numatytoji {_DEFAULT_REDIRECT_URI}): "
).strip() or _DEFAULT_REDIRECT_URI

auth_url = _build_instagram_authorization_url(client_id=client_id, redirect_uri=redirect_uri)

print("\n1. Atidarykite šį URL savo naršyklėje ir patvirtinkite prieigą:\n")
print(auth_url)
print(
    "\n2. Po patvirtinimo Instagram nukreips jus į adresą, prasidedantį jūsų "
    "redirect_uri su '?code=...' parametru."
)
print("   Nukopijuokite TIK 'code=' reikšmę (be '#_' galo, jei toks yra).\n")

authorization_code = input("Įklijuokite gautą 'code' reikšmę čia: ").strip()

print("\nKeičiamas kodas į trumpaamžį (short-lived) tokeną...")
try:
    short_lived_payload = _exchange_authorization_code(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authorization_code=authorization_code,
    )
except Exception as e:
    print(f"\nNepavyko gauti tokeno: {e}")
    raise SystemExit(1)

short_lived_access_token = short_lived_payload.get("access_token")
if not short_lived_access_token:
    print(f"\nAtsakyme nerastas access_token: {short_lived_payload} - nutraukiama.")
    raise SystemExit(1)

# Instagram Login API's authorization-code exchange response already
# includes user_id directly alongside access_token, but it's re-fetched
# here via a separate, well-documented GET to /me anyway - an
# independent confirmation step that also exercises the token once with
# a plain read before relying on it further.
print("Nustatomas Instagram Business Account ID...")
try:
    profile_payload = _instagram_graph_get(
        "/me",
        {"fields": "user_id", "access_token": short_lived_access_token},
        app_secret_for_redaction=client_secret,
    )
except Exception as e:
    print(f"\nNepavyko nustatyti Instagram Business Account ID: {e}")
    raise SystemExit(1)

instagram_business_account_id = profile_payload.get("user_id")
if not instagram_business_account_id:
    print("\nAtsakyme nerastas user_id - nutraukiama.")
    raise SystemExit(1)
instagram_business_account_id = str(instagram_business_account_id)

print("Keičiamas trumpaamžis tokenas į ilgaamžį (long-lived, ~60 dienų)...")
try:
    long_lived_payload = _instagram_graph_get(
        "/access_token",
        {
            "grant_type": "ig_exchange_token",
            "client_secret": client_secret,
            "access_token": short_lived_access_token,
        },
        app_secret_for_redaction=client_secret,
    )
except Exception as e:
    print(f"\nNepavyko gauti ilgaamžio tokeno: {e}")
    raise SystemExit(1)

long_lived_access_token = long_lived_payload.get("access_token")
if not long_lived_access_token:
    print("\nAtsakyme nerastas ilgaamžis access_token - nutraukiama.")
    raise SystemExit(1)
long_lived_expires_in = long_lived_payload.get("expires_in", 60 * 24 * 3600)  # ~60 dienų numatytoji

# OAuthTokens.scope neturi atskiro lauko Instagram Business Account ID
# saugojimui (tai bendrasis, connector-agnostic modelis - žr. oauth.py) -
# saugome jį kaip "<granted_scope>|<account_id>" suffix'ą, kurį
# InstagramConnector._account_id() atskirai iššifruoja. Tai vienintelė
# vieta, kurioje ši koduotė sudaroma.
tokens = OAuthTokens(
    access_token=long_lived_access_token,
    refresh_token="",  # Meta nesuteikia standartinio refresh_token - long-lived token yra vienintelis
    expires_at=time.time() + float(long_lived_expires_in),
    scope=f"{META_INSTAGRAM_OAUTH_PROVIDER.scope}|{instagram_business_account_id}",
)

store = TokenStore("instagram")
saved = store.save(tokens)

if saved:
    print("\nTokenai sėkmingai išsaugoti Windows Credential Manager.")
    print(store.describe())
else:
    print("\nNepavyko išsaugoti tokenų į OS keychain (keyring nepasiekiamas).")

print(
    "\nDabar galite patikrinti: jarvis integrations  "
    "(turėtų rodyti 'instagram: configured.')"
)
print("Gmail ir Google Calendar integracijos liko nepaliestos - jų tokenai atskiri.")
