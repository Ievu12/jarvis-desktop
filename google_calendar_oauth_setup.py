"""Vienkartinis Google Calendar OAuth prijungimo scenarijus.

NĖRA JARVIS projekto dalis — nekuria naujo failo projekte, nekeičia
jokio esamo kodo. Paleidžiamas tiesiai iš JARVIS venv, kad turėtų
prieigą prie jau įgyvendintų funkcijų.

Visiškai ATSKIRTAS nuo Gmail prijungimo: naudoja savo OAuth Client
(galite naudoti tą patį Google Cloud projektą kaip Gmail, bet Calendar
API turi būti atskirai įjungtas), savo scope
(calendar.readonly — ne gmail.readonly) ir savo tokenų saugyklą
(TokenStore("google_calendar") — atskiras Windows Credential Manager
įrašas nuo "jarvis-oauth-gmail"). Paleidus šį scenarijų, Gmail OAuth
tokenai NELIEČIAMI ir NEKEIČIAMI.

Niekas iš įvestų reikšmių (client_secret, authorization_code, tokenai)
niekada nerašomi į failą, log'ą ar terminalo istoriją už šios sesijos
ribų — client_secret įvedamas per getpass (nerodomas ekrane), tokenai
įrašomi tik į OS keychain per TokenStore.
"""

import getpass

from jarvis.integrations.oauth import (
    GOOGLE_CALENDAR_OAUTH_PROVIDER,
    TokenStore,
    build_authorization_url,
    exchange_code_for_tokens,
)

print("=== Google Calendar OAuth prijungimas (READ-only) ===\n")
print("Pastaba: šis scenarijus NELIEČIA jūsų jau veikiančio Gmail prijungimo.\n")

client_id = input("Įveskite jūsų OAuth Client ID: ").strip()
client_secret = getpass.getpass("Įveskite jūsų OAuth Client Secret (nebus rodomas): ").strip()
redirect_uri = input(
    "Įveskite Redirect URI (turi tiksliai sutapti su Google Cloud Console įrašu, "
    "pvz. http://localhost:8080): "
).strip()

state = "jarvis-google-calendar-setup"  # vienkartinei rankinei sesijai pakanka fiksuotos reikšmės

auth_url = build_authorization_url(
    GOOGLE_CALENDAR_OAUTH_PROVIDER,
    client_id=client_id,
    redirect_uri=redirect_uri,
    state=state,
)

print("\n1. Atidarykite šį URL savo naršyklėje ir patvirtinkite prieigą:\n")
print(auth_url)
print(
    "\n2. Po patvirtinimo Google nukreips jus į adresą, prasidedantį jūsų "
    "redirect_uri su '?code=...' parametru."
)
print("   Nukopijuokite TIK 'code=' reikšmę (be '&state=...' dalies, jei tokia yra).\n")

authorization_code = input("Įklijuokite gautą 'code' reikšmę čia: ").strip()

print("\nKeičiamas kodas į tokenus...")
try:
    tokens = exchange_code_for_tokens(
        GOOGLE_CALENDAR_OAUTH_PROVIDER,
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        authorization_code=authorization_code,
    )
except Exception as e:
    print(f"\nNepavyko gauti tokenų: {e}")
    raise SystemExit(1)

store = TokenStore("google_calendar")
saved = store.save(tokens)

if saved:
    print("\nTokenai sėkmingai išsaugoti Windows Credential Manager.")
    print(store.describe())
else:
    print("\nNepavyko išsaugoti tokenų į OS keychain (keyring nepasiekiamas).")

print(
    "\nDabar galite patikrinti: jarvis integrations  "
    "(turėtų rodyti 'google_calendar: configured.')"
)
print("Gmail integracija liko nepaliesta - jos tokenai atskiri.")
