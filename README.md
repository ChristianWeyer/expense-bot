# Expense Bot

Automatisiert die Beleg-Sammlung aus Mastercard/BusinessCard-Abrechnungen (Sparkasse/qards Format). Parst das MC-PDF per GPT Vision, sucht Belege in Outlook-Emails und diversen Vendor-Portalen, und versendet alles per Email.

## Unterstützte Quellen

| Quelle | Methode | Auth |
|--------|---------|------|
| **Deutsche Bahn** | bahn.de Playwright (CDP) | 1Password + 2FA |
| **Amazon** | amazon.de Playwright (CDP) | 1Password |
| **Outlook Emails** | Microsoft Graph API | OAuth (Device Code) |
| **OpenAI API** | platform.openai.com Stripe | 1Password |
| **ChatGPT** | chatgpt.com Stripe | 1Password |
| **Adobe** | account.adobe.com Billing | 1Password |
| **Figma** | figma.com API | 1Password |
| **Google/YouTube** | pay.google.com Transaktionsdetails | CDP Session |
| **Heise** | Plenigo Billing Portal | 1Password |
| **Spiegel** | gruppenkonto.spiegel.de | 1Password (eigener Browser) |
| **Audible** | audible.de Monatsbeitraege | Amazon Session |
| **Cloudflare** | Billing API | API Token |
| **Hetzner, GitHub, Anthropic, ...** | Outlook Email-Belege (Stripe PDFs) | Graph API |

## Setup

### 1. Skript-Setup

```bash
git clone <repo>
cd bahn-rechnung-bot
./setup.sh          # Erstellt venv, installiert Dependencies
cp env.template .env
```

### 2. .env konfigurieren

Mindestens erforderlich:
- `RECIPIENT_EMAIL` — Ziel-Adresse fuer den Beleg-Report
- `AZURE_CLIENT_ID` + `AZURE_TENANT_ID` — fuer Graph API (Email)
- `OPENAI_API_KEY` — fuer MC-PDF Parsing (GPT Vision)

Alle Vendor-Credentials werden automatisch aus **1Password CLI** geladen (`op read`). Die `OP_*` Variablen in `.env` definieren die 1Password-Referenzen. Alternativ koennen Credentials direkt in `.env` gesetzt werden.

Siehe `env.template` fuer alle Optionen.

### 3. Azure App Registration

Fuer Email-Versand und Outlook-Belegsuche:

1. [Azure Portal -> App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. "New registration" -> Name: `expense-bot`
3. "Authentication" -> "Allow public client flows" -> **Yes**
4. "API Permissions" -> Microsoft Graph -> Delegated -> **Mail.Send** + **Mail.Read**
5. Client ID und Tenant ID in `.env` eintragen

### 4. Chrome Canary (empfohlen)

Fuer Portale mit Browser-Login (Bahn, Amazon, Google, Adobe, etc.):

```bash
# Chrome Canary installieren: https://www.google.com/chrome/canary/
# Einmalig: in Canary bei allen Portalen einloggen
# Der Bot nutzt die bestehende Session via CDP
```

## Nutzung

```bash
# Standard: MC-PDF angeben, Chrome Canary wird automatisch gestartet
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf

# Nur gelb markierte Eintraege (fuer selektive Abrechnung)
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf --marked-entries-only

# Dry-Run (laedt alles, sendet aber keine Email)
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf --dry-run

# Browser sichtbar (Debugging)
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf --headed

# Einzelnen Scraper testen (parst PDF, fuehrt nur diesen Scraper aus, kein Email-Versand)
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf --only google
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf --only amazon
# Verfuegbar: outlook, bahn, amazon, google, spiegel, audible, heise, adobe, figma, cloudflare, portal

# Ohne run.sh (manuell):
source .venv/bin/activate
python expense_bot.py --mc-pdf abrechnung.pdf --cdp http://localhost:9222
```

### Was passiert bei einem Run?

1. **MC-PDF parsen** — GPT Vision extrahiert alle Buchungseintraege (~2 Min)
2. **Outlook-Emails durchsuchen** — Graph API sucht passende Belege (~2 Min)
3. **DB-Rechnungen** — bahn.de per Playwright/CDP (~2 Min)
4. **Amazon-Rechnungen** — amazon.de per Playwright/CDP (~2 Min)
5. **Portal-Scraper** — ChatGPT, OpenAI, Heise, Adobe, Figma, Google, Audible, Cloudflare (~3 Min)
6. **Spiegel** — eigener Browser-Kontext (~15 Sek)
7. **Email versenden** — alle PDFs als Anhaenge (~3 Sek)

Gesamtdauer: ~12 Minuten. Ergebnis: ~80/84 Belege (>93% Match-Rate).

### Erster Run des Monats

1. **OAuth Token**: Beim ersten Email-Versand erscheint ein Device Code im Terminal. Link oeffnen, Code eingeben, anmelden. Token wird gecacht.
2. **Chrome Canary Sessions**: Falls Sessions abgelaufen sind, loggt der Bot automatisch per 1Password ein (inkl. 2FA-Wartezeit).
3. **1Password CLI**: Muss einmalig mit `op signin` authentifiziert sein.

### Token-Cache zuruecksetzen

Falls OAuth-Probleme auftreten:
```bash
rm .token_cache.json
# Naechster Run fordert erneut Device Code Login an
```

## Architektur

```
expense_bot.py              # Orchestrator (CLI, Ablaufsteuerung)
run.sh                      # Start-Skript (CDP-Check, Logging)
src/
  config.py                 # Konfiguration, 1Password CLI, Timeouts
  auth.py                   # Microsoft Graph OAuth (MSAL, Device Code)
  mastercard.py             # MC-PDF Parser (GPT Vision, seitenweise)
  outlook.py                # Outlook Belegsuche + Scoring (Graph API)
  bahn.py                   # bahn.de Login + Rechnungs-Download
  amazon.py                 # Amazon.de Rechnungs-Download
  google.py                 # Google Payments (YouTube, Google One)
  portal.py                 # Generischer Portal-Scraper (JSON-Config)
  figma.py                  # Figma Invoice API
  heise.py                  # Heise/Plenigo Billing
  adobe.py                  # Adobe Billing (React Spectrum)
  spiegel.py                # Spiegel Abo-Rechnungen
  audible.py                # Audible.de Monatsbeitraege
  cloudflare.py             # Cloudflare Billing API
  mail.py                   # Email-Versand (Graph API)
  result.py                 # RunResult Datenstruktur
  timer.py                  # Timer-Utility
  history.py                # Download-Historie, Cleanup
portals/
  chatgpt.json              # ChatGPT Subscription (Stripe)
  openai-api.json           # OpenAI API Billing (Stripe)
  cloudflare.json           # Cloudflare Dashboard
tests/
  test_outlook_scoring.py   # Outlook Email-Scoring (32 Tests)
  test_mastercard_marked.py # MC-PDF Marked-Entry Filterung (9 Tests)
  test_mastercard_llm.py    # LLM Integration Tests (15 Tests, $0.50)
  test_*.py                 # Weitere Unit-Tests (~170)
logs/                       # Run-Logs (via run.sh)
belege/                     # Heruntergeladene Belege (pro Run)
```

## Tests

```bash
# Alle Unit-Tests (~215)
pytest tests/ -x -q

# Inklusive LLM-Integration (kostet ~$0.50, braucht OPENAI_API_KEY)
RUN_LLM_TESTS=1 pytest tests/test_mastercard_llm.py -v
```

## Konfiguration

Alle Einstellungen in `.env` (siehe `env.template`):

| Variable | Pflicht | Beschreibung |
|----------|---------|--------------|
| `RECIPIENT_EMAIL` | ja | Ziel-Email fuer Beleg-Report |
| `AZURE_CLIENT_ID` | ja | Microsoft Graph OAuth Client ID |
| `OPENAI_API_KEY` | ja* | GPT Vision API Key (*wenn MC-PDF) |
| `CDP_URL` | nein | Chrome CDP URL (default: localhost:9222) |
| `MC_PDF` | nein | Pfad zum MC-PDF (oder via CLI) |
| `KEEP_DAYS` | nein | Belege-Aufbewahrung in Tagen (default: 30) |
| `OWN_EMAIL_DOMAIN` | nein | Eigene Domain (filtert ausgehende Emails) |

Vendor-Credentials: entweder direkt (`GOOGLE_EMAIL=...`) oder per 1Password (`OP_GOOGLE_EMAIL=op://...`).
