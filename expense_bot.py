#!/usr/bin/env python3
"""
Expense Bot
===========
Automatisiert den Download von DB-Rechnungen von bahn.de,
sucht Belege aus Outlook-Mailordnern, und versendet alles
per Email über Microsoft Graph API (OAuth).

Nutzung:
    python expense_bot.py                               # Letzte Reise
    python expense_bot.py --mc-pdf abrechnung.pdf       # DB-Rechnungen aus Mastercard-PDF
    python expense_bot.py --mc-pdf abr.pdf --fetch-receipts  # + Belege aus Outlook
    python expense_bot.py --dry-run                     # Nur anzeigen, nicht senden
"""

import os
import sys
import json
import base64
import argparse
import hashlib
import time
from datetime import datetime
from pathlib import Path

import requests
import msal
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout


# ─── Timing ──────────────────────────────────────────────────────────
class Timer:
    """Einfacher Timer für Timing-Ausgaben."""

    def __init__(self):
        self._start = time.monotonic()
        self._lap = self._start

    def elapsed(self) -> str:
        """Gesamtzeit seit Start."""
        secs = time.monotonic() - self._start
        return self._fmt(secs)

    def lap(self, label: str) -> str:
        """Zeit seit letztem Lap, gibt formatierten String aus."""
        now = time.monotonic()
        secs = now - self._lap
        total = now - self._start
        self._lap = now
        msg = f"  ⏱  {label}: {self._fmt(secs)}  (gesamt: {self._fmt(total)})"
        print(msg)
        return msg

    @staticmethod
    def _fmt(secs: float) -> str:
        if secs < 60:
            return f"{secs:.1f}s"
        mins = int(secs // 60)
        rest = secs % 60
        return f"{mins}m {rest:.1f}s"

# ─── Konfiguration laden ────────────────────────────────────────────
load_dotenv(Path(__file__).parent / ".env")

# Pflichtfelder werden erst in main() validiert, damit das Modul importierbar bleibt.
BAHN_EMAIL = os.environ.get("BAHN_EMAIL")
BAHN_PASSWORD = os.environ.get("BAHN_PASSWORD")
RECIPIENT_EMAIL = os.environ.get("RECIPIENT_EMAIL")

# Microsoft Graph / OAuth
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID")
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "common")
CC_EMAIL = os.environ.get("CC_EMAIL", "").strip() or None
CDP_URL = os.environ.get("CDP_URL", "").strip() or None
MC_PDF = os.environ.get("MC_PDF", "").strip() or None
try:
    KEEP_DAYS = int(os.environ.get("KEEP_DAYS", "30"))
except ValueError:
    KEEP_DAYS = 30

DOWNLOAD_DIR = Path(__file__).parent / "rechnungen"
BELEGE_DIR = Path(__file__).parent / "belege"
HISTORY_FILE = Path(__file__).parent / ".download_history.json"
TOKEN_CACHE_FILE = Path(__file__).parent / ".token_cache.json"
BROWSER_DATA_DIR = Path(__file__).parent / ".browser-data"

# URLs
HOME_URL = "https://www.bahn.de"
TRIPS_URL = "https://www.bahn.de/buchung/reisen"
GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"

# OAuth Scopes
SCOPES = ["Mail.Send", "Mail.Read"]

# Wiederverwendete Selektoren
DOWNLOAD_BTN_SELECTOR = (
    'a:has-text("Rechnung als PDF herunterladen"):visible, '
    'button:has-text("Rechnung als PDF herunterladen"):visible'
)


# ─── Token-Cache (MSAL) ─────────────────────────────────────────────
def _get_token_cache() -> msal.SerializableTokenCache:
    """Lädt oder erstellt den MSAL Token-Cache."""
    cache = msal.SerializableTokenCache()
    if TOKEN_CACHE_FILE.exists():
        cache.deserialize(TOKEN_CACHE_FILE.read_text())
    return cache


def _save_token_cache(cache: msal.SerializableTokenCache):
    """Speichert den Token-Cache mit eingeschränkten Dateiberechtigungen."""
    if cache.has_state_changed:
        TOKEN_CACHE_FILE.write_text(cache.serialize())
        TOKEN_CACHE_FILE.chmod(0o600)


def get_graph_token() -> str:
    """
    Holt ein gültiges Access-Token für Microsoft Graph.
    Beim ersten Mal: Device Code Flow (Browser-Login).
    Danach: automatisch per Refresh-Token.
    """
    cache = _get_token_cache()

    app = msal.PublicClientApplication(
        AZURE_CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{AZURE_TENANT_ID}",
        token_cache=cache,
    )

    # Versuch 1: Token aus Cache (silent)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_token_cache(cache)
            return result["access_token"]

    # Versuch 2: Device Code Flow (interaktiv)
    print("\n🔐 Microsoft-Anmeldung erforderlich (einmalig)")
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"  ❌ Device Code Flow fehlgeschlagen: {flow}")
        sys.exit(1)

    print(f"  → Öffne: {flow['verification_uri']}")
    print(f"  → Code:  {flow['user_code']}")
    print(f"  Warte auf Anmeldung im Browser ...")

    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        print(f"  ❌ Anmeldung fehlgeschlagen: {result.get('error_description', result)}")
        sys.exit(1)

    _save_token_cache(cache)
    print("  ✅ Anmeldung erfolgreich! Token wird gecacht.")
    return result["access_token"]


# ─── Hilfsfunktionen ────────────────────────────────────────────────
def load_history() -> set:
    """Lädt die Historie der bereits verarbeiteten Rechnungen."""
    if HISTORY_FILE.exists():
        with open(HISTORY_FILE) as f:
            return set(json.load(f))
    return set()


def save_history(history: set):
    """Speichert die Historie."""
    with open(HISTORY_FILE, "w") as f:
        json.dump(list(history), f, indent=2)


def file_hash(filepath: Path) -> str:
    """Erstellt einen SHA256-Hash einer Datei zur Duplikat-Erkennung."""
    return hashlib.sha256(filepath.read_bytes()).hexdigest()


def _file_hash_md5(filepath: Path) -> str:
    """Erstellt einen MD5-Hash (nur für Rückwärtskompatibilität mit alter History)."""
    return hashlib.md5(filepath.read_bytes()).hexdigest()


def is_known_file(filepath: Path, history: set) -> bool:
    """Prüft ob eine Datei bereits in der History ist (SHA256 oder alter MD5)."""
    return file_hash(filepath) in history or _file_hash_md5(filepath) in history


def cleanup_old_invoices(keep_days: int):
    """Löscht PDFs aus rechnungen/ die älter als keep_days Tage sind."""
    if not DOWNLOAD_DIR.exists():
        return
    cutoff = time.time() - (keep_days * 86400)
    removed = 0
    for pdf in DOWNLOAD_DIR.iterdir():
        if pdf.suffix.lower() != ".pdf":
            continue
        if pdf.stat().st_mtime < cutoff:
            pdf.unlink()
            removed += 1
    if removed:
        print(f"🧹 {removed} alte Rechnung(en) gelöscht (älter als {keep_days} Tage)")


# ─── bahn.de Login ──────────────────────────────────────────────────
def login(page, timer: Timer):
    """Loggt sich bei bahn.de ein über den Keycloak-Login-Flow."""
    print("🔑 Login bei bahn.de ...")

    # 1. Startseite laden
    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # 2. Cookie-Banner behandeln (falls vorhanden)
    #    Verschiedene Varianten: "Nur erforderliche", "Alle ablehnen", "Einstellungen speichern", etc.
    try:
        cookie_selectors = [
            "button:has-text('Nur erforderliche')",
            "button:has-text('Alle ablehnen')",
            "button:has-text('Einstellungen speichern')",
            "button:has-text('Reject')",
            "[id*='cookie'] button:first-of-type",
            "[class*='cookie'] button:first-of-type",
            "[class*='consent'] button:first-of-type",
            # Fallback: Alle akzeptieren (besser als overlay bleibt)
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Accept')",
        ]
        banner_dismissed = False
        for sel in cookie_selectors:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click(force=True)
                print(f"  🍪 Cookie-Banner geschlossen")
                page.wait_for_timeout(1500)
                banner_dismissed = True
                break

        # Falls immer noch ein Overlay da ist: per JS entfernen
        if not banner_dismissed:
            removed = page.evaluate("""() => {
                const overlays = document.querySelectorAll(
                    '[class*="cookie"], [class*="consent"], [class*="overlay"], [id*="cookie"], [id*="consent"]'
                );
                let removed = 0;
                for (const el of overlays) {
                    if (el.offsetHeight > 100) { el.remove(); removed++; }
                }
                return removed;
            }""")
            if removed > 0:
                print(f"  🍪 {removed} Overlay-Element(e) per JS entfernt")
                page.wait_for_timeout(500)
    except Exception as e:
        print(f"  ℹ️  Cookie-Banner Handling übersprungen: {e}")

    # 3. Prüfen ob bereits eingeloggt (gespeicherte Session)
    #    Nur eindeutige Indikatoren nutzen – NICHT "Meine Reisen" (ist immer im Menü)
    logged_in_indicator = page.locator(
        'a:has-text("Abmelden"), '
        'button:has-text("Abmelden"), '
        '[data-testid*="logout"]'
    )
    # Kurz warten, da die Seite evtl. noch lädt
    page.wait_for_timeout(2000)
    if logged_in_indicator.count() > 0:
        print("  ✅ Bereits eingeloggt (gespeicherte Session)!")
        timer.lap("Login (Session)")
        return

    # Auch prüfen: wenn "Anmelden" NICHT sichtbar ist, sind wir evtl. schon drin
    login_link = page.locator(
        'a:has-text("Anmelden"), '
        'button:has-text("Anmelden")'
    )
    if login_link.count() == 0:
        print("  ✅ Kein Anmelden-Button gefunden – vermutlich bereits eingeloggt!")
        timer.lap("Login (Session)")
        return

    # 4. "Anmelden"-Button klicken
    print("  → Klicke 'Anmelden' ...")
    login_link.first.click(force=True)  # force=True um Overlays zu umgehen

    # 5. Warten bis Keycloak-Login-Seite geladen (accounts.bahn.de)
    page.wait_for_timeout(3000)
    try:
        page.wait_for_url("**/accounts.bahn.de/**", timeout=15000)
    except PlaywrightTimeout:
        # Vielleicht schon eingeloggt?
        if "accounts.bahn.de" not in page.url:
            print("  ℹ️  Bereits eingeloggt oder unerwartete Seite:", page.url[:80])

    # 6. Login-Formular ausfüllen (Keycloak – zweistufig)
    if "accounts.bahn.de" in page.url:
        page.wait_for_timeout(2000)

        # Schritt 1: Email/Benutzername eingeben
        username_input = page.locator(
            'input[name="username"], '
            'input[name="email"], '
            'input[id="username"], '
            'input[type="email"]'
        )
        username_input.first.fill(BAHN_EMAIL)
        print(f"  → Email eingegeben")
        page.wait_for_timeout(500)

        # "Weiter"/"Next" Button klicken (Keycloak zeigt Passwort erst nach diesem Schritt)
        next_btn = page.locator(
            'button[type="submit"], '
            'input[type="submit"], '
            'button:has-text("Weiter"), '
            'button:has-text("Next"), '
            'button:has-text("Anmelden")'
        )
        next_btn.first.click()
        print("  → Weiter geklickt ...")
        page.wait_for_timeout(3000)

        # Schritt 2: Passwort eingeben (erscheint erst jetzt)
        password_input = page.locator('input[type="password"], input[name="password"]')
        try:
            password_input.first.wait_for(state="visible", timeout=10000)
            password_input.first.fill(BAHN_PASSWORD)
            print("  → Passwort eingegeben")
            page.wait_for_timeout(500)

            # Login absenden
            submit_btn = page.locator(
                'button[type="submit"], '
                'input[type="submit"], '
                'button:has-text("Anmelden"), '
                'button:has-text("Login")'
            )
            submit_btn.first.click()
            print("  → Anmeldung gesendet ...")
        except PlaywrightTimeout:
            print("  ⚠️  Passwort-Feld nicht gefunden – ggf. anderer Login-Flow")
            print(f"     Aktuelle URL: {page.url[:100]}")

        # Schritt 3: 2FA / SMS-Code abwarten (falls aktiviert)
        page.wait_for_timeout(3000)
        otp_input = page.locator(
            'input[name="otp"], '
            'input[name="code"], '
            'input[name="totp"], '
            'input[type="tel"], '
            'input[autocomplete="one-time-code"], '
            'input[placeholder*="Code"], '
            'input[placeholder*="code"]'
        )
        if otp_input.count() > 0:
            print("\n  📱 2FA-Code erforderlich!")
            print("  → Bitte gib den SMS-Code im Browser ein und bestätige.")
            print("  → Warte auf Abschluss ...\n")

            # Warten bis wir nicht mehr auf accounts.bahn.de sind (= Login fertig)
            try:
                page.wait_for_url(
                    lambda url: "accounts.bahn.de" not in url,
                    timeout=120000,  # 2 Minuten Zeit für SMS-Code
                )
            except PlaywrightTimeout:
                print("  ❌ Timeout – 2FA nicht abgeschlossen innerhalb von 2 Minuten")
                sys.exit(1)
        else:
            # Kein 2FA – einfach auf Redirect warten
            page.wait_for_timeout(5000)

    # 7. Prüfen ob Login erfolgreich
    current_url = page.url
    if "accounts.bahn.de" in current_url:
        print("  ❌ Login scheint fehlgeschlagen – bitte Zugangsdaten in .env prüfen")
        sys.exit(1)

    # Sicherstellen, dass Cookies/Session geschrieben werden
    page.wait_for_timeout(3000)
    print("  ✅ Login erfolgreich!")
    timer.lap("Login")


# ─── Rechnungs-Download ─────────────────────────────────────────────
def _debug_page_buttons(page, booking_ref: str):
    """Zeigt alle sichtbaren Buttons/Links auf der Seite (Debugging)."""
    try:
        buttons_info = page.evaluate("""() => {
            const results = [];
            // Alle Buttons und Links sammeln
            const elements = document.querySelectorAll('button, a[href], [role="button"]');
            for (const el of elements) {
                const text = el.textContent?.trim().replace(/\\s+/g, ' ') || '';
                if (text.toLowerCase().includes('rechnung') || text.toLowerCase().includes('invoice') ||
                    text.toLowerCase().includes('pdf') || text.toLowerCase().includes('download') ||
                    text.toLowerCase().includes('erstellen')) {
                    const rect = el.getBoundingClientRect();
                    results.push({
                        tag: el.tagName,
                        text: text.substring(0, 80),
                        href: el.href || '',
                        class: el.className?.substring?.(0, 60) || '',
                        visible: rect.height > 0 && rect.width > 0,
                        y: Math.round(rect.y),
                    });
                }
            }
            return results;
        }""")
        if buttons_info:
            print(f"    🔎 Relevante Elemente auf der Seite:")
            for b in buttons_info:
                vis = "👁" if b["visible"] else "🚫"
                print(f"       {vis} <{b['tag']}> \"{b['text']}\" y={b['y']} class={b['class'][:40]}")
                if b.get("href"):
                    print(f"          href={b['href'][:80]}")
        else:
            print(f"    🔎 Keine Rechnungs-Elemente auf der Seite gefunden")
    except Exception as e:
        print(f"    🔎 Debug-Scan fehlgeschlagen: {e}")


def _check_page_status(page, booking_ref: str) -> str:
    """Prüft den Status der Buchungsseite. Gibt zurück: 'not_found', 'no_invoice', 'create', 'download'.

    WICHTIG: Nur SICHTBARE Buttons zählen! bahn.de hat oft beide Buttons im DOM,
    aber nur einer ist sichtbar (der andere hat height=0 / display:none).
    """
    # Seite gültig?
    error_indicator = page.locator('text="nicht gefunden", text="Fehler", text="existiert nicht"')
    if error_indicator.count() > 0:
        return "not_found"

    # Explizit nach dem SICHTBAREN Download-Button suchen (rote Taste)
    download_btn = page.locator(DOWNLOAD_BTN_SELECTOR)
    if download_btn.count() > 0:
        return "download"

    # Nach SICHTBAREM "Rechnung erstellen" suchen
    create_btn = page.locator(
        'a:has-text("Rechnung erstellen"):visible, '
        'button:has-text("Rechnung erstellen"):visible'
    )
    if create_btn.count() > 0:
        return "create"

    return "no_invoice"


def download_invoice_by_ref(page, booking_ref: str, timer: Timer) -> Path | None:
    """Öffnet eine Buchung per Auftragsnummer und lädt die Rechnung herunter."""
    DOWNLOAD_DIR.mkdir(exist_ok=True)

    # Direkt zur Reise-Detailseite navigieren.
    # Wichtig: about:blank zwischenladen, um SPA-Router-Cache zu umgehen!
    reise_url = f"https://www.bahn.de/buchung/reise?auftragsnummer={booking_ref}"
    page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
    page.wait_for_timeout(500)
    page.goto(reise_url, wait_until="domcontentloaded", timeout=60000)

    # Warten bis der eigentliche Seiteninhalt geladen ist (nicht nur die Nav-Shell).
    # bahn.de ist eine SPA – der Content kommt asynchron nach dem DOM-Load.
    content_loaded = False
    matched_indicator = None
    for attempt in range(15):  # max 15 × 2s = 30s warten
        page.wait_for_timeout(2000)
        # Prüfen ob tatsächlich Reise-Content geladen wurde
        # Nutze spezifischere Indikatoren die NUR im Reise-Content vorkommen (nicht in der Nav)
        result = page.evaluate("""() => {
            const body = document.body?.innerText || '';

            // Server-Timeout erkennen
            if (body.includes('Verbindungsversuch mit dem Server') ||
                body.includes('versuchen weiterhin Ihre Anfrage')) {
                return '__SERVER_TIMEOUT__';
            }

            const indicators = [
                'Rechnung als PDF',
                'Rechnung erstellen',
                'Ticket als PDF',
                'Reservierungsinfos als PDF',
                'Hinfahrt',
                'Rückfahrt',
                'Auftragsnummer',
                'Sitzplatz',
                'Storniert',
                'Stornierung',
                'Diese Buchung wurde storniert',
                'Keine Reise gefunden',
                'nicht gefunden',
                'Reiseplan',
                'Wagenreihung'
            ];
            for (const ind of indicators) {
                if (body.includes(ind)) return ind;
            }
            return null;
        }""")
        if result == '__SERVER_TIMEOUT__':
            print(f"    ⚠️  bahn.de Server-Timeout erkannt – lade Seite neu ...")
            page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
            page.goto(reise_url, wait_until="domcontentloaded", timeout=60000)
            continue  # Nächster Versuch im Loop
        if result:
            content_loaded = True
            matched_indicator = result
            break
        # Seite scrollen kann manchmal Lazy-Load triggern
        if attempt == 2:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    if content_loaded:
        print(f"    ✅ Content geladen (Match: \"{matched_indicator}\")")
    else:
        print(f"    ⚠️  Seiteninhalt für {booking_ref} nicht geladen (Timeout nach 30s)")

    # Zum Seitenende scrollen – der Rechnungs-Button ist ganz unten
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 800)")
        page.wait_for_timeout(500)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1500)

    # Debug: was ist alles auf der Seite?
    _debug_page_buttons(page, booking_ref)

    # Status prüfen
    status = _check_page_status(page, booking_ref)
    print(f"    📊 Status: {status}")

    if status == "not_found":
        print(f"    ⚠️  Buchung {booking_ref} nicht gefunden auf bahn.de")
        return None

    if status == "no_invoice":
        # Diagnose: Was zeigt die Seite eigentlich?
        page_url = page.url
        page_title = page.title()
        page_text_snippet = page.evaluate("""() => {
            const body = document.body?.innerText || '';
            // Relevante Abschnitte suchen
            const lines = body.split('\\n').filter(l => l.trim().length > 0);
            // Erste 30 nicht-leere Zeilen
            return lines.slice(0, 30).join('\\n');
        }""")
        print(f"    ⚠️  Kein Rechnungs-Button für {booking_ref}")
        print(f"    📋 URL: {page_url[:100]}")
        print(f"    📋 Titel: {page_title[:60]}")
        # Zeige die ersten paar Zeilen des Seiteninhalts
        for line in page_text_snippet.split('\n')[:10]:
            line = line.strip()
            if line and len(line) > 3:
                print(f"    📋 {line[:100]}")
        return None

    # ── "Rechnung erstellen" Flow ──────────────────────────────────
    if status == "create":
        print(f"    📝 Rechnung muss erst erstellt werden ...")
        create_btn = page.locator(
            'a:has-text("Rechnung erstellen"):visible, '
            'button:has-text("Rechnung erstellen"):visible'
        )
        create_btn.first.scroll_into_view_if_needed()
        page.wait_for_timeout(500)

        # Klicken – es könnte ein Dialog/Popup/neues Formular erscheinen
        create_btn.first.click()
        page.wait_for_timeout(3000)

        # Möglichkeit 1: Ein Modal/Dialog mit Adresseingabe öffnet sich
        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="overlay"], [class*="dialog"]')
        if dialog.count() > 0:
            print(f"    📝 Dialog erkannt – suche Bestätigungsknopf ...")
            # Im Dialog nach einem Bestätigungsbutton suchen
            confirm_btn = dialog.locator(
                'button:has-text("Erstellen"), '
                'button:has-text("Rechnung erstellen"), '
                'button:has-text("Bestätigen"), '
                'button:has-text("Weiter"), '
                'button[type="submit"]'
            )
            if confirm_btn.count() > 0:
                confirm_btn.first.click()
                print(f"    → Dialog bestätigt, warte auf Erstellung ...")
                page.wait_for_timeout(5000)
            else:
                print(f"    ⚠️  Kein Bestätigungs-Button im Dialog gefunden")
                _debug_page_buttons(page, booking_ref)

        # Möglichkeit 2: Seite wird einfach neu geladen und zeigt jetzt den Download-Button
        # Nochmal scrollen und prüfen
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)

        # Warte bis der Download-Button SICHTBAR wird (max 15s)
        try:
            dl_btn = page.locator(DOWNLOAD_BTN_SELECTOR)
            dl_btn.first.wait_for(state="visible", timeout=15000)
            print(f"    ✅ Rechnung wurde erstellt! Jetzt herunterladen ...")
            status = "download"
        except PlaywrightTimeout:
            print(f"    ⚠️  Download-Button nach Erstellen nicht erschienen")
            _debug_page_buttons(page, booking_ref)
            return None

    # ── "Rechnung als PDF herunterladen" Flow ──────────────────────
    if status == "download":
        return _do_pdf_download(page, booking_ref)

    return None


def _do_pdf_download(page, booking_ref: str) -> Path | None:
    """Führt den eigentlichen PDF-Download durch."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    download_btn = page.locator(DOWNLOAD_BTN_SELECTOR)
    if download_btn.count() == 0:
        print(f"    ⚠️  Download-Button verschwunden für {booking_ref}")
        return None

    download_btn.first.scroll_into_view_if_needed()
    page.wait_for_timeout(500)

    # Ansatz 1: Prüfen ob der Button ein <a> mit href ist → direkt runterladen
    btn_href = download_btn.first.evaluate("el => el.href || ''")

    if btn_href and btn_href.startswith("http"):
        print(f"    → Link-basierter Download: {btn_href[:60]}...")
        # Direkt den Link als Download nutzen
        try:
            with page.expect_download(timeout=30000) as download_info:
                download_btn.first.click()
            download = download_info.value
            fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
            save_path = DOWNLOAD_DIR / f"{booking_ref}_{fname}"
            download.save_as(str(save_path))
            print(f"    ✅ PDF heruntergeladen: {save_path.name}")
            return save_path
        except PlaywrightTimeout:
            print(f"    ⚠️  expect_download Timeout – versuche Alternative ...")

    # Ansatz 2: Klicken und auf Download warten (generischer)
    pages_before = len(page.context.pages)
    downloaded_file = [None]

    def on_download(download):
        fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
        path = DOWNLOAD_DIR / f"{booking_ref}_{fname}"
        download.save_as(str(path))
        downloaded_file[0] = path
        print(f"    ✅ PDF heruntergeladen (event): {fname}")

    # Handler auf aktuelle Seite und neue Tabs (Fallback falls expect_download nicht greift)
    page.once("download", on_download)
    page.context.once("page", lambda new_page: new_page.once("download", on_download))

    try:
        with page.expect_download(timeout=15000) as download_info:
            download_btn.first.click()
            print(f"    → Download-Button geklickt ...")
        download = download_info.value
        fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
        save_path = DOWNLOAD_DIR / f"{booking_ref}_{fname}"
        download.save_as(str(save_path))
        print(f"    ✅ PDF heruntergeladen: {save_path.name}")
        _close_extra_tabs(page, pages_before)
        return save_path
    except PlaywrightTimeout:
        # Prüfen ob bahn.de eine Fehlermeldung zeigt
        error_msg = page.locator('text="Fehler beim Rechnungsabruf"')
        if error_msg.count() > 0:
            print(f"    ⚠️  bahn.de meldet: 'Fehler beim Rechnungsabruf' – temporäres Serverproblem")
            _close_extra_tabs(page, pages_before)
            return None

    # Ansatz 3: Neuer Tab geöffnet? (bahn.de macht das manchmal)
    page.wait_for_timeout(3000)
    all_pages = page.context.pages
    if len(all_pages) > pages_before:
        new_tab = all_pages[-1]
        print(f"    ℹ️  Neuer Tab geöffnet: {new_tab.url[:60]}")
        try:
            new_tab.wait_for_load_state("domcontentloaded", timeout=15000)
            new_tab.wait_for_timeout(2000)

            # Falls der neue Tab direkt ein PDF enthält
            if ".pdf" in new_tab.url.lower():
                print(f"    📄 PDF-URL im neuen Tab – versuche Response-Download ...")
                # Das PDF wurde als Seite geladen – Request abfangen
                response = new_tab.goto(new_tab.url)
                if response:
                    body = response.body()
                    fname = f"rechnung_{booking_ref}_{timestamp}.pdf"
                    save_path = DOWNLOAD_DIR / fname
                    save_path.write_bytes(body)
                    print(f"    ✅ PDF gespeichert: {fname}")
                    try:
                        new_tab.close()
                    except Exception:
                        pass
                    return save_path

            # Im neuen Tab nach Download-Button suchen
            new_tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            new_tab.wait_for_timeout(2000)

            new_dl_btn = new_tab.locator(
                'a:has-text("Rechnung als PDF herunterladen"), '
                'button:has-text("Rechnung als PDF herunterladen")'
            )
            if new_dl_btn.count() > 0:
                try:
                    with new_tab.expect_download(timeout=15000) as dl_info:
                        new_dl_btn.first.click()
                    download = dl_info.value
                    fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
                    save_path = DOWNLOAD_DIR / f"{booking_ref}_{fname}"
                    download.save_as(str(save_path))
                    print(f"    ✅ PDF heruntergeladen (neuer Tab): {save_path.name}")
                    try:
                        new_tab.close()
                    except Exception:
                        pass
                    return save_path
                except PlaywrightTimeout:
                    print(f"    ⚠️  Auch im neuen Tab kein Download ausgelöst")

            try:
                new_tab.close()
            except Exception:
                pass
        except Exception as e:
            print(f"    ⚠️  Fehler im neuen Tab: {e}")

    # Ansatz 4: Download-Event hat vielleicht doch gefeuert
    if downloaded_file[0]:
        _close_extra_tabs(page, pages_before)
        return downloaded_file[0]

    print(f"    ⚠️  Download fehlgeschlagen für {booking_ref}")
    _close_extra_tabs(page, pages_before)
    return None


def _close_extra_tabs(page, expected_count: int):
    """Schließt alle zusätzlich geöffneten Tabs."""
    try:
        for p in page.context.pages[expected_count:]:
            p.close()
    except Exception:
        pass


def download_invoices(page, timer: Timer, download_all: bool = False, booking_refs: list[str] | None = None) -> tuple[list[Path], list[str]]:
    """Navigiert zu 'Meine Reisen' und lädt Rechnungen herunter."""
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    history = load_history()
    downloaded_files = []

    # ── Modus 1: Gezielt nach Buchungsnummern suchen (Mastercard-PDF) ──
    if booking_refs:
        print(f"📄 Suche {len(booking_refs)} Rechnung(en) per Buchungsnummer ...")
        failed_refs = []
        for idx, ref in enumerate(booking_refs, 1):
            print(f"\n  🔍 [{idx}/{len(booking_refs)}] Suche Buchung {ref} ...")
            dl_start = time.monotonic()
            filepath = download_invoice_by_ref(page, ref, timer)
            if filepath:
                if is_known_file(filepath, history):
                    print(f"  ⏭️  Bereits verarbeitet: {ref}")
                    filepath.unlink()
                else:
                    history.add(file_hash(filepath))
                    downloaded_files.append(filepath)
                    print(f"  ✅ Heruntergeladen: {filepath.name} ({time.monotonic() - dl_start:.1f}s)")
            else:
                print(f"  ⏱  Buchung {ref}: {time.monotonic() - dl_start:.1f}s (kein Download)")
                failed_refs.append(ref)

        # ── Retry: Fehlgeschlagene Buchungen nochmal versuchen (bis zu 2 Runden) ──
        for retry_round in range(1, 3):
            if not failed_refs:
                break
            print(f"\n🔄 Retry {retry_round}/2: {len(failed_refs)} fehlgeschlagene Buchung(en) nochmal versuchen ...")
            still_failed = []
            for idx, ref in enumerate(failed_refs, 1):
                print(f"\n  🔁 [Retry {retry_round}.{idx}/{len(failed_refs)}] Suche Buchung {ref} ...")
                dl_start = time.monotonic()
                filepath = download_invoice_by_ref(page, ref, timer)
                if filepath:
                    if is_known_file(filepath, history):
                        print(f"  ⏭️  Bereits verarbeitet: {ref}")
                        filepath.unlink()
                    else:
                        history.add(file_hash(filepath))
                        downloaded_files.append(filepath)
                        print(f"  ✅ Heruntergeladen (Retry): {filepath.name} ({time.monotonic() - dl_start:.1f}s)")
                else:
                    still_failed.append(ref)
            failed_refs = still_failed

        if failed_refs:
            print(f"\n  ⚠️  {len(failed_refs)} Buchung(en) endgültig fehlgeschlagen: {', '.join(failed_refs)}")

        save_history(history)
        timer.lap(f"Download ({len(downloaded_files)}/{len(booking_refs)} Rechnungen)")
        return downloaded_files, failed_refs

    # ── Modus 2: Alle / letzte Reise(n) durchgehen ──
    print("📄 Suche Rechnungen über 'Meine Reisen' ...")

    page.goto(TRIPS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    past_tab = page.locator('text="Vergangene Reisen", [data-testid*="past"], button:has-text("Vergangene")')
    if past_tab.count() > 0:
        past_tab.first.click()
        page.wait_for_timeout(2000)

    trip_cards = page.locator('[class*="trip"], [class*="reise"], [class*="order"], [data-testid*="trip"]')
    trip_count = trip_cards.count()

    if trip_count == 0:
        trip_cards = page.locator('article, [class*="card"]')
        trip_count = trip_cards.count()

    print(f"  📋 {trip_count} Reise(n) gefunden")

    max_trips = trip_count if download_all else min(1, trip_count)

    for i in range(max_trips):
        try:
            print(f"\n  🚂 Verarbeite Reise {i + 1}/{max_trips} ...")

            trip_cards.nth(i).click()
            page.wait_for_timeout(2000)

            invoice_btn = page.locator(
                'button:has-text("Rechnung"), '
                'a:has-text("Rechnung"), '
                'button:has-text("Invoice"), '
                '[data-testid*="invoice"], '
                '[class*="invoice"]'
            )

            if invoice_btn.count() > 0:
                with page.expect_download(timeout=15000) as download_info:
                    invoice_btn.first.click()

                download = download_info.value
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                original_name = download.suggested_filename or f"rechnung_{timestamp}.pdf"
                save_path = DOWNLOAD_DIR / f"{timestamp}_{original_name}"
                download.save_as(save_path)

                if is_known_file(save_path, history):
                    print(f"  ⏭️  Bereits verarbeitet: {original_name}")
                    save_path.unlink()
                else:
                    history.add(file_hash(save_path))
                    downloaded_files.append(save_path)
                    print(f"  ✅ Heruntergeladen: {save_path.name}")
            else:
                print("  ⚠️  Kein Rechnungs-Button gefunden für diese Reise")

            page.go_back()
            page.wait_for_timeout(1500)

        except PlaywrightTimeout:
            print(f"  ⚠️  Timeout bei Reise {i + 1} – überspringe")
            page.go_back()
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"  ⚠️  Fehler bei Reise {i + 1}: {e}")
            page.go_back()
            page.wait_for_timeout(1000)

    save_history(history)
    return downloaded_files, []


# ─── Email-Versand über Microsoft Graph ──────────────────────────────
def send_email(files: list[Path], timer: Timer, dry_run: bool = False, cc_email: str | None = None,
               mc_pdf_name: str | None = None, failed_refs: list[str] | None = None,
               total_refs: int | None = None, unmatched_entries: list[dict] | None = None,
               link_only_entries: list[dict] | None = None):
    """Versendet die Rechnungen und Belege per Microsoft Graph API (OAuth)."""
    if not files:
        print("\n📭 Keine neuen Rechnungen/Belege zum Versenden.")
        return

    cc_info = f" (CC: {cc_email})" if cc_email else ""
    print(f"\n📧 Versende {len(files)} PDF(s) an {RECIPIENT_EMAIL}{cc_info} ...")

    if dry_run:
        print("  🏃 Dry-Run: Email wird NICHT gesendet")
        for f in files:
            print(f"    📎 {f.name} ({f.stat().st_size / 1024:.1f} KB)")
        return

    # Access-Token holen
    token = get_graph_token()

    # Email-Body
    now = datetime.now()
    db_files = [f for f in files if "DB_Rechnung" in f.name]
    receipt_files = [f for f in files if "DB_Rechnung" not in f.name]

    source_line = f"Quelle: Mastercard-Abrechnung \"{mc_pdf_name}\"\n\n" if mc_pdf_name else ""

    # DB-Rechnungen Status
    db_section = ""
    if db_files:
        if failed_refs and total_refs:
            db_section = (
                f"DB-Rechnungen: {len(db_files)} von {total_refs} heruntergeladen\n"
                f"⚠️  Fehlgeschlagene Buchungen (bitte manuell prüfen):\n"
            )
            for ref in failed_refs:
                db_section += f"  - Auftrag {ref}: https://www.bahn.de/buchung/reise?auftragsnummer={ref}\n"
        elif total_refs:
            db_section = f"DB-Rechnungen: alle {total_refs} erfolgreich heruntergeladen\n"
        db_section += "".join(f"  - {f.name}\n" for f in db_files)
        db_section += "\n"

    # Belege Status
    receipt_section = ""
    if receipt_files:
        receipt_section = f"Belege aus Outlook: {len(receipt_files)} PDFs heruntergeladen\n"
        receipt_section += "".join(f"  - {f.name}\n" for f in receipt_files)
        receipt_section += "\n"

    # Belege nur als Link (kein PDF-Anhang)
    link_section = ""
    if link_only_entries:
        link_section = f"ℹ️  Belege ohne PDF-Anhang ({len(link_only_entries)}) – bitte manuell herunterladen:\n"
        for m in link_only_entries:
            e = m.get("entry", {})
            vendor = e.get("vendor", "?")
            amount = e.get("amount", 0)
            url = m.get("receipt_url", "")
            link_section += f"  - {vendor}  {amount:.2f} EUR\n"
            if url:
                link_section += f"    → {url}\n"
        link_section += "\n"

    # Fehlende Belege
    unmatched_section = ""
    if unmatched_entries:
        unmatched_section = f"⚠️  Kein Beleg gefunden für {len(unmatched_entries)} Einträge:\n"
        for e in unmatched_entries:
            vendor = e.get("vendor", "?")
            amount = e.get("amount", 0)
            date = e.get("date", "")
            unmatched_section += f"  - {date}  {vendor}  {amount:.2f} EUR\n"
        unmatched_section += "\n"

    body_text = (
        f"--- Automatisch generierte Email (Expense Bot) ---\n\n"
        f"Hallo,\n\n"
        f"anbei {len(files)} Beleg(e) als PDF im Anhang.\n\n"
        f"{source_line}"
        f"{db_section}"
        f"{receipt_section}"
        f"{link_section}"
        f"{unmatched_section}"
        f"Diese Email wurde automatisch erstellt am {now.strftime('%d.%m.%Y um %H:%M Uhr')}.\n\n"
        f"Bei Fragen bitte direkt an den Absender wenden.\n"
        f"--- Ende der automatischen Nachricht ---"
    )

    # Attachments als Base64
    attachments = []
    for filepath in files:
        content_bytes = filepath.read_bytes()
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": filepath.name,
            "contentType": "application/pdf",
            "contentBytes": base64.b64encode(content_bytes).decode("utf-8"),
        })

    # Empfänger
    to_recipients = [{"emailAddress": {"address": RECIPIENT_EMAIL}}]
    cc_recipients = [{"emailAddress": {"address": cc_email}}] if cc_email else []

    # Betreff
    has_issues = bool(failed_refs or unmatched_entries)
    subject = (
        f"[Automatisch] Belege ({len(files)} PDFs)"
        f" – {now.strftime('%d.%m.%Y')}"
        f"{f' – {mc_pdf_name}' if mc_pdf_name else ''}"
        f"{' ⚠️ UNVOLLSTÄNDIG' if has_issues else ''}"
    )

    # Graph API Payload
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "Text",
                "content": body_text,
            },
            "toRecipients": to_recipients,
            "ccRecipients": cc_recipients,
            "attachments": attachments,
        },
        "saveToSentItems": True,
    }

    # Senden
    response = requests.post(
        GRAPH_SEND_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=30,
    )

    if response.status_code == 202:
        print("  ✅ Email erfolgreich gesendet!")
        print(f"\n  📎 Versendete Rechnungen ({len(files)}):")
        for f in files:
            print(f"     • {f.name} ({f.stat().st_size / 1024:.1f} KB)")
        timer.lap("Email-Versand")
    else:
        print(f"  ❌ Email-Versand fehlgeschlagen (HTTP {response.status_code})")
        print(f"     {response.text}")
        print("     Rechnungen sind trotzdem gespeichert in:", DOWNLOAD_DIR)
        sys.exit(1)


# ─── Main ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="bahn.de Rechnungs-Bot")
    parser.add_argument("--all", action="store_true", help="Alle neuen Rechnungen herunterladen")
    parser.add_argument("--dry-run", action="store_true", help="Rechnungen laden, aber nicht per Email senden")
    parser.add_argument("--headed", action="store_true", help="Browser sichtbar starten (zum Debuggen)")
    parser.add_argument("--mc-pdf", type=str, metavar="DATEI_ODER_ORDNER", default=MC_PDF,
                        help="Mastercard-PDF oder Ordner mit PDFs (neuestes wird genutzt). "
                             "Standard aus .env: MC_PDF")
    parser.add_argument("--cc", type=str, metavar="EMAIL", default=CC_EMAIL,
                        help="CC-Empfänger für die Email. Standard aus .env: CC_EMAIL")
    parser.add_argument("--login-only", action="store_true",
                        help="Nur einloggen (inkl. 2FA) und Session speichern – für Ersteinrichtung")
    parser.add_argument("--cdp", type=str, metavar="URL", nargs="?", const=CDP_URL or "http://localhost:9222",
                        default=CDP_URL,
                        help="An laufenden Chrome (Canary) anhängen (CDP). "
                             "Standard aus .env: CDP_URL")
    parser.add_argument("--fetch-receipts", action="store_true",
                        help="Auch Belege aus Outlook 'Belege'-Ordner suchen und herunterladen")
    args = parser.parse_args()

    timer = Timer()

    # Pflichtfelder validieren
    missing = [name for name, val in [
        ("BAHN_EMAIL", BAHN_EMAIL), ("BAHN_PASSWORD", BAHN_PASSWORD),
        ("RECIPIENT_EMAIL", RECIPIENT_EMAIL), ("AZURE_CLIENT_ID", AZURE_CLIENT_ID),
    ] if not val]
    if missing:
        print(f"❌ Fehlende Umgebungsvariablen in .env: {', '.join(missing)}")
        sys.exit(1)

    print("=" * 50)
    print("🚄 bahn.de Rechnungs-Bot")
    print("=" * 50)

    # Mastercard-PDF parsen (falls angegeben)
    booking_refs = None
    mc_pdf_name = None
    if args.mc_pdf:
        from parse_mastercard import extract_db_bookings, get_net_bookings, print_summary

        mc_path = Path(args.mc_pdf)

        # Wenn ein Ordner angegeben: neuestes PDF im Ordner finden
        if mc_path.is_dir():
            pdf_files = sorted(
                [p for p in mc_path.iterdir() if p.suffix.lower() == ".pdf"],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )

            if not pdf_files:
                print(f"⚠️  Keine PDF-Dateien im Ordner: {mc_path}")
                return
            mc_path = pdf_files[0]
            print(f"\n📂 Ordner: {args.mc_pdf}")
            print(f"   Neuestes PDF: {mc_path.name}")
            if len(pdf_files) > 1:
                print(f"   ({len(pdf_files)} PDFs im Ordner, nutze das neueste)")

        mc_pdf_name = mc_path.name
        print(f"\n💳 Lese Mastercard-PDF: {mc_path}")

        if args.fetch_receipts:
            # Alle Einträge extrahieren (DB + sonstige)
            from parse_mastercard import extract_all_entries, get_db_entries, get_non_db_entries
            all_entries = extract_all_entries(str(mc_path))
            db_entries = get_db_entries(all_entries)
            non_db_entries = get_non_db_entries(all_entries)
            net = print_summary(db_entries, "DB-Buchungen")
            booking_refs = [b["booking_ref"] for b in net if b.get("booking_ref")]
        else:
            bookings = extract_db_bookings(str(mc_path))
            non_db_entries = []
            net = print_summary(bookings)
            booking_refs = [b["booking_ref"] for b in net if b.get("booking_ref")]

        timer.lap("PDF-Parsing")
        if not booking_refs:
            print("⚠️  Keine DB-Buchungsnummern im PDF gefunden.")
            print("    Falle zurück auf 'Meine Reisen'-Modus ...")

    # ── Belege aus Outlook holen (falls --fetch-receipts) ──
    receipt_files = []
    unmatched_entries = []
    link_only_entries = []
    if args.fetch_receipts and non_db_entries:
        from fetch_receipts import match_and_download_receipts
        token = get_graph_token()
        receipt_results = match_and_download_receipts(token, non_db_entries, BELEGE_DIR)
        receipt_files = receipt_results.get("downloaded_files", [])
        unmatched_entries = receipt_results.get("unmatched", [])
        link_only_entries = [m for m in receipt_results.get("matched", [])
                            if m.get("receipt_url") and not m.get("files")]
        timer.lap(f"Belege ({len(receipt_files)} PDFs)")

    with sync_playwright() as p:
        use_cdp = False
        browser = None

        # ── CDP-Modus versuchen (falls konfiguriert) ──
        if args.cdp:
            cdp_url = args.cdp
            print(f"\n🔗 Verbinde mit Chrome über CDP: {cdp_url}")
            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
                use_cdp = True
                timer.lap("Chrome-Verbindung (CDP)")
            except Exception:
                print(f"   ⚠️  CDP nicht erreichbar – starte eigenen headless Browser ...")
                use_cdp = False

        if use_cdp and browser:
            # Bestehenden Kontext nutzen (mit eingeloggter Session!)
            context = browser.contexts[0] if browser.contexts else browser.new_context(
                accept_downloads=True,
                locale="de-DE",
            )
            page = context.new_page()

            try:
                login(page, timer)

                if args.login_only:
                    print("\n✅ Login erfolgreich! (CDP – Session ist im Chrome gespeichert)")
                    print(f"\n✨ Fertig! Gesamtzeit: {timer.elapsed()}")
                    return

                files, failed = download_invoices(page, timer, download_all=args.all, booking_refs=booking_refs)
                total = len(booking_refs) if booking_refs else None
                send_email(files + receipt_files, timer, dry_run=args.dry_run, cc_email=args.cc,
                           mc_pdf_name=mc_pdf_name, failed_refs=failed, total_refs=total,
                           unmatched_entries=unmatched_entries, link_only_entries=link_only_entries)
            finally:
                page.close()  # Nur den Tab schließen, nicht Chrome selbst!
                browser.close()  # CDP-Verbindung trennen (Chrome läuft weiter)

        else:
            # ── Headless Playwright-Browser (Fallback) ──
            BROWSER_DATA_DIR.mkdir(exist_ok=True)
            print("\n🌐 Starte headless Browser ...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=not args.headed,
                accept_downloads=True,
                locale="de-DE",
            )
            page = context.new_page()

            try:
                login(page, timer)

                if args.login_only:
                    print("\n✅ Login erfolgreich!")
                    print("   Session wird in .browser-data/ gespeichert.")
                    print("\n   Drücke ENTER um den Browser zu schließen ...")
                    input()
                    print("   Session gespeichert. Beim nächsten Mal sollte kein 2FA nötig sein.")
                    context.close()
                    print(f"\n✨ Fertig! Gesamtzeit: {timer.elapsed()}")
                    return

                files, failed = download_invoices(page, timer, download_all=args.all, booking_refs=booking_refs)
                total = len(booking_refs) if booking_refs else None
                send_email(files + receipt_files, timer, dry_run=args.dry_run, cc_email=args.cc,
                           mc_pdf_name=mc_pdf_name, failed_refs=failed, total_refs=total,
                           unmatched_entries=unmatched_entries, link_only_entries=link_only_entries)
            finally:
                context.close()

    cleanup_old_invoices(KEEP_DAYS)
    print(f"\n✨ Fertig! Gesamtzeit: {timer.elapsed()}")


if __name__ == "__main__":
    main()
