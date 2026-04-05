# bahn.de Rechnungs-Bot

Automatisiert den Download von DB-Rechnungen von bahn.de und versendet sie per Email.
Kann Auftragsnummern direkt aus Mastercard/BusinessCard-Abrechnungs-PDFs extrahieren.
Email-Versand läuft über Microsoft Graph API mit OAuth (kein SMTP-Passwort nötig).

## Setup (einmalig)

### 1. Skript-Setup

```bash
cd bahn-rechnung-bot
./setup.sh
```

Erstellt ein Python-venv und installiert alle Abhängigkeiten (playwright, msal, requests, pdfplumber).

### 2. Azure App Registration

Damit der Bot Emails über dein Microsoft-365-Konto senden kann, brauchst du eine App Registration:

1. Gehe zu [Azure Portal → App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade)
2. Klicke "New registration"
3. Name: z.B. `bahn-rechnung-bot`
4. Supported account types: "Accounts in this organizational directory only" (Single Tenant) oder "Accounts in any organizational directory" (Multi-Tenant)
5. Redirect URI: leer lassen (nicht nötig für Device Code Flow)
6. Nach der Erstellung: kopiere die **Application (client) ID** und **Directory (tenant) ID**
7. Unter "Authentication":
   - Aktiviere "Allow public client flows" → **Yes**
8. Unter "API Permissions":
   - "Add a permission" → Microsoft Graph → Delegated → **Mail.Send**
   - Falls nötig: Admin Consent erteilen lassen

### 3. .env ausfüllen

```bash
# Falls noch nicht geschehen:
cp env.template .env
```

Trage ein:
- **BAHN_EMAIL / BAHN_PASSWORD**: Deine bahn.de Zugangsdaten
- **RECIPIENT_EMAIL**: Email-Adresse der Empfängerin
- **AZURE_CLIENT_ID**: Die Application (client) ID aus Schritt 2
- **AZURE_TENANT_ID**: Die Directory (tenant) ID (oder `common`)

## Nutzung

```bash
# Aus Mastercard-PDF – extrahiert DB-Buchungsnummern und lädt gezielt Rechnungen
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf

# Erst mal nur testen (lädt runter, sendet aber nicht)
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf --dry-run

# Browser sichtbar starten (zum Debuggen)
./run.sh --mc-pdf /pfad/zur/abrechnung.pdf --headed --dry-run

# Eigenen Chrome nutzen (Session bleibt erhalten, kein Login nötig!)
./run.sh --cdp --mc-pdf /pfad/zur/abrechnung.pdf

# Letzte Reise – Rechnung laden und versenden
./run.sh

# Alle neuen Rechnungen auf einmal
./run.sh --all
```

### Chrome Canary nutzen (empfohlen)

Damit du nicht jedes Mal neu einloggen musst, kann der Bot Chrome Canary als dedizierten Bot-Browser nutzen – parallel zu deinem normalen Chrome:

```bash
# 1. Chrome Canary mit Debugging-Port starten (einmalig):
/Applications/Google\ Chrome\ Canary.app/Contents/MacOS/Google\ Chrome\ Canary --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-canary-bahn-bot"

# 2. In Canary bei bahn.de einloggen (normal, mit "Angemeldet bleiben")

# 3. Bot mit --cdp starten:
./run.sh --cdp --mc-pdf /pfad/zur/abrechnung.pdf --dry-run
```

Vorteil: Dein normaler Chrome bleibt unangetastet, und die bahn.de-Session in Canary bleibt erhalten – kein erneutes Login/2FA nötig!

Falls Chrome Canary noch nicht installiert ist: https://www.google.com/chrome/canary/

### Nur den Mastercard-Parser testen (ohne bahn.de Login)

```bash
source .venv/bin/activate
python parse_mastercard.py /pfad/zur/abrechnung.pdf
```

### Erster Email-Versand (OAuth Login)

Beim ersten Mal, wenn eine Email gesendet wird, erscheint im Terminal:

```
🔐 Microsoft-Anmeldung erforderlich (einmalig)
  → Öffne: https://microsoft.com/devicelogin
  → Code:  ABC123DEF
```

Öffne den Link, gib den Code ein und melde dich an. Das Token wird danach lokal gecacht (`.token_cache.json`) und automatisch erneuert.

## Dateien

```
bahn-rechnung-bot/
├── setup.sh                # Einmaliges Setup (venv + Dependencies)
├── run.sh                  # Start-Skript (aktiviert venv automatisch)
├── bahn_invoice_bot.py     # Hauptskript (Login, Download, Email via Graph API)
├── parse_mastercard.py     # Mastercard-PDF Parser
├── requirements.txt        # Python-Abhängigkeiten mit Mindestversionen
├── env.template            # Vorlage für Konfiguration
├── .env                    # Deine Konfiguration (nicht committen!)
├── .token_cache.json       # OAuth Token-Cache (nicht committen!)
└── rechnungen/             # Heruntergeladene PDFs (wird automatisch erstellt)
```

## Hinweise

- Rechnungen werden in `./rechnungen/` gespeichert
- Bereits verarbeitete Rechnungen werden nicht erneut gesendet (Duplikat-Erkennung per SHA256-Hash)
- Alte PDFs in `rechnungen/` werden automatisch nach 30 Tagen gelöscht (konfigurierbar via `KEEP_DAYS` in `.env`)
- Storno-Paare im Mastercard-PDF (gleiche Auftragsnr. mit + und -) werden erkannt
- `.env` und `.token_cache.json` enthalten sensible Daten – niemals committen!
- Falls bahn.de das Layout ändert, müssen ggf. die CSS-Selektoren in `bahn_invoice_bot.py` angepasst werden
- Empfohlen: beim ersten Mal mit `--headed --dry-run` testen
