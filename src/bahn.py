"""bahn.de Playwright-Automation — Login und Rechnungs-Download."""

import sys
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

import src.config as _cfg
from src.config import (
    BAHN_EMAIL, BAHN_PASSWORD, HOME_URL, TRIPS_URL,
    DOWNLOAD_BTN_SELECTOR,
    PAGE_TIMEOUT, DOWNLOAD_TIMEOUT, LOGIN_TIMEOUT,
)
from src.history import load_history, save_history, file_hash, is_known_file
from src.timer import Timer


def login(page, timer: Timer):
    """Loggt sich bei bahn.de ein über den Keycloak-Login-Flow."""
    print("🔑 Login bei bahn.de ...")

    page.goto(HOME_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    # Cookie-Banner
    try:
        cookie_selectors = [
            "button:has-text('Nur erforderliche')",
            "button:has-text('Alle ablehnen')",
            "button:has-text('Einstellungen speichern')",
            "button:has-text('Reject')",
            "[id*='cookie'] button:first-of-type",
            "[class*='cookie'] button:first-of-type",
            "[class*='consent'] button:first-of-type",
            "button:has-text('Alle akzeptieren')",
            "button:has-text('Accept')",
        ]
        banner_dismissed = False
        for sel in cookie_selectors:
            btn = page.locator(sel)
            if btn.count() > 0:
                btn.first.click(force=True)
                print("  🍪 Cookie-Banner geschlossen")
                page.wait_for_timeout(1500)
                banner_dismissed = True
                break
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

    # Bereits eingeloggt?
    logged_in_indicator = page.locator(
        'a:has-text("Abmelden"), button:has-text("Abmelden"), [data-testid*="logout"]'
    )
    page.wait_for_timeout(2000)
    if logged_in_indicator.count() > 0:
        print("  ✅ Bereits eingeloggt (gespeicherte Session)!")
        timer.lap("Login (Session)")
        return

    login_link = page.locator('a:has-text("Anmelden"), button:has-text("Anmelden")')
    if login_link.count() == 0:
        print("  ✅ Kein Anmelden-Button gefunden – vermutlich bereits eingeloggt!")
        timer.lap("Login (Session)")
        return

    print("  → Klicke 'Anmelden' ...")
    login_link.first.click(force=True)

    page.wait_for_timeout(3000)
    try:
        page.wait_for_url("**/accounts.bahn.de/**", timeout=PAGE_TIMEOUT)
    except PlaywrightTimeout:
        if "accounts.bahn.de" not in page.url:
            print("  ℹ️  Bereits eingeloggt oder unerwartete Seite:", page.url[:80])

    if "accounts.bahn.de" in page.url:
        page.wait_for_timeout(2000)

        username_input = page.locator(
            'input[name="username"], input[name="email"], input[id="username"], input[type="email"]'
        )
        username_input.first.fill(BAHN_EMAIL)
        print("  → Email eingegeben")
        page.wait_for_timeout(500)

        next_btn = page.locator(
            'button[type="submit"], input[type="submit"], '
            'button:has-text("Weiter"), button:has-text("Next"), button:has-text("Anmelden")'
        )
        next_btn.first.click()
        print("  → Weiter geklickt ...")
        page.wait_for_timeout(3000)

        password_input = page.locator('input[type="password"], input[name="password"]')
        try:
            password_input.first.wait_for(state="visible", timeout=10000)
            password_input.first.fill(BAHN_PASSWORD)
            print("  → Passwort eingegeben")
            page.wait_for_timeout(500)

            submit_btn = page.locator(
                'button[type="submit"], input[type="submit"], '
                'button:has-text("Anmelden"), button:has-text("Login")'
            )
            submit_btn.first.click()
            print("  → Anmeldung gesendet ...")
        except PlaywrightTimeout:
            print("  ⚠️  Passwort-Feld nicht gefunden – ggf. anderer Login-Flow")
            print(f"     Aktuelle URL: {page.url[:100]}")

        page.wait_for_timeout(3000)
        otp_input = page.locator(
            'input[name="otp"], input[name="code"], input[name="totp"], '
            'input[type="tel"], input[autocomplete="one-time-code"], '
            'input[placeholder*="Code"], input[placeholder*="code"]'
        )
        if otp_input.count() > 0:
            print("\n  📱 2FA-Code erforderlich!")
            print("  → Bitte gib den SMS-Code im Browser ein und bestätige.")
            print("  → Warte auf Abschluss ...\n")
            try:
                page.wait_for_url(
                    lambda url: "accounts.bahn.de" not in url,
                    timeout=LOGIN_TIMEOUT,
                )
            except PlaywrightTimeout:
                print("  ❌ Timeout – 2FA nicht abgeschlossen innerhalb von 2 Minuten")
                sys.exit(1)
        else:
            page.wait_for_timeout(5000)

    current_url = page.url
    if "accounts.bahn.de" in current_url:
        print("  ❌ Login scheint fehlgeschlagen – bitte Zugangsdaten prüfen")
        sys.exit(1)

    page.wait_for_timeout(3000)
    print("  ✅ Login erfolgreich!")
    timer.lap("Login")


def _debug_page_buttons(page, booking_ref: str):
    """Zeigt alle sichtbaren Buttons/Links auf der Seite (Debugging)."""
    try:
        buttons_info = page.evaluate("""() => {
            const results = [];
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
            print("    🔎 Relevante Elemente auf der Seite:")
            for b in buttons_info:
                vis = "👁" if b["visible"] else "🚫"
                print(f"       {vis} <{b['tag']}> \"{b['text']}\" y={b['y']} class={b['class'][:40]}")
                if b.get("href"):
                    print(f"          href={b['href'][:80]}")
        else:
            print("    🔎 Keine Rechnungs-Elemente auf der Seite gefunden")
    except Exception as e:
        print(f"    🔎 Debug-Scan fehlgeschlagen: {e}")


def _check_page_status(page, booking_ref: str) -> str:
    """Prüft den Status der Buchungsseite. Gibt 'not_found', 'no_invoice', 'create' oder 'download' zurück."""
    error_indicator = page.locator('text="nicht gefunden", text="Fehler", text="existiert nicht"')
    if error_indicator.count() > 0:
        return "not_found"

    download_btn = page.locator(DOWNLOAD_BTN_SELECTOR)
    if download_btn.count() > 0:
        return "download"

    create_btn = page.locator(
        'a:has-text("Rechnung erstellen"):visible, button:has-text("Rechnung erstellen"):visible'
    )
    if create_btn.count() > 0:
        return "create"

    return "no_invoice"


def download_invoice_by_ref(page, booking_ref: str, timer: Timer) -> Path | None:
    """Öffnet eine Buchung per Auftragsnummer und lädt die Rechnung herunter."""
    _cfg.DOWNLOAD_DIR.mkdir(exist_ok=True)

    reise_url = f"https://www.bahn.de/buchung/reise?auftragsnummer={booking_ref}"
    page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
    page.wait_for_timeout(500)
    page.goto(reise_url, wait_until="domcontentloaded", timeout=60000)

    # Session abgelaufen? Re-Login einmalig versuchen
    if "anmelden" in page.url.lower():
        print("    ⚠️  bahn.de Session abgelaufen — Re-Login ...")
        login(page, timer)
        page.goto(reise_url, wait_until="domcontentloaded", timeout=60000)
        if "anmelden" in page.url.lower():
            print("    ❌ Re-Login fehlgeschlagen — überspringe Buchung")
            return None

    # Warten auf SPA-Content
    content_loaded = False
    matched_indicator = None
    for attempt in range(15):
        page.wait_for_timeout(2000)
        result = page.evaluate("""() => {
            const body = document.body?.innerText || '';
            if (body.includes('Verbindungsversuch mit dem Server') ||
                body.includes('versuchen weiterhin Ihre Anfrage')) {
                return '__SERVER_TIMEOUT__';
            }
            const indicators = [
                'Rechnung als PDF', 'Rechnung erstellen', 'Ticket als PDF',
                'Reservierungsinfos als PDF', 'Hinfahrt', 'Rückfahrt',
                'Auftragsnummer', 'Sitzplatz', 'Storniert', 'Stornierung',
                'Diese Buchung wurde storniert', 'Keine Reise gefunden',
                'nicht gefunden', 'Reiseplan', 'Wagenreihung'
            ];
            for (const ind of indicators) {
                if (body.includes(ind)) return ind;
            }
            return null;
        }""")
        if result == '__SERVER_TIMEOUT__':
            print("    ⚠️  bahn.de Server-Timeout erkannt – lade Seite neu ...")
            page.goto("about:blank", wait_until="domcontentloaded", timeout=10000)
            page.wait_for_timeout(2000)
            page.goto(reise_url, wait_until="domcontentloaded", timeout=60000)
            continue
        if result:
            content_loaded = True
            matched_indicator = result
            break
        if attempt == 2:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

    if content_loaded:
        print(f"    ✅ Content geladen (Match: \"{matched_indicator}\")")
    else:
        print(f"    ⚠️  Seiteninhalt für {booking_ref} nicht geladen (Timeout nach 30s)")

    # Scrollen
    for _ in range(3):
        page.evaluate("window.scrollBy(0, 800)")
        page.wait_for_timeout(500)
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    page.wait_for_timeout(1500)

    _debug_page_buttons(page, booking_ref)

    status = _check_page_status(page, booking_ref)
    print(f"    📊 Status: {status}")

    if status == "not_found":
        print(f"    ⚠️  Buchung {booking_ref} nicht gefunden auf bahn.de")
        return None

    if status == "no_invoice":
        page_url = page.url
        page_title = page.title()
        page_text_snippet = page.evaluate("""() => {
            const body = document.body?.innerText || '';
            const lines = body.split('\\n').filter(l => l.trim().length > 0);
            return lines.slice(0, 30).join('\\n');
        }""")
        print(f"    ⚠️  Kein Rechnungs-Button für {booking_ref}")
        print(f"    📋 URL: {page_url[:100]}")
        print(f"    📋 Titel: {page_title[:60]}")
        for line in page_text_snippet.split('\n')[:10]:
            line = line.strip()
            if line and len(line) > 3:
                print(f"    📋 {line[:100]}")
        return None

    if status == "create":
        print("    📝 Rechnung muss erst erstellt werden ...")
        create_btn = page.locator(
            'a:has-text("Rechnung erstellen"):visible, button:has-text("Rechnung erstellen"):visible'
        )
        create_btn.first.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        create_btn.first.click()
        page.wait_for_timeout(3000)

        dialog = page.locator('[role="dialog"], [class*="modal"], [class*="overlay"], [class*="dialog"]')
        if dialog.count() > 0:
            print("    📝 Dialog erkannt – suche Bestätigungsknopf ...")
            confirm_btn = dialog.locator(
                'button:has-text("Erstellen"), button:has-text("Rechnung erstellen"), '
                'button:has-text("Bestätigen"), button:has-text("Weiter"), button[type="submit"]'
            )
            if confirm_btn.count() > 0:
                confirm_btn.first.click()
                print("    → Dialog bestätigt, warte auf Erstellung ...")
                page.wait_for_timeout(5000)
            else:
                print("    ⚠️  Kein Bestätigungs-Button im Dialog gefunden")
                _debug_page_buttons(page, booking_ref)

        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(3000)

        try:
            dl_btn = page.locator(DOWNLOAD_BTN_SELECTOR)
            dl_btn.first.wait_for(state="visible", timeout=DOWNLOAD_TIMEOUT)
            print("    ✅ Rechnung wurde erstellt! Jetzt herunterladen ...")
            status = "download"
        except PlaywrightTimeout:
            print("    ⚠️  Download-Button nach Erstellen nicht erschienen")
            _debug_page_buttons(page, booking_ref)
            return None

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

    btn_href = download_btn.first.evaluate("el => el.href || ''")

    if btn_href and btn_href.startswith("http"):
        print(f"    → Link-basierter Download: {btn_href[:60]}...")
        try:
            with page.expect_download(timeout=PAGE_TIMEOUT) as download_info:
                download_btn.first.click()
            download = download_info.value
            fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
            save_path = _cfg.DOWNLOAD_DIR / f"{booking_ref}_{fname}"
            download.save_as(str(save_path))
            print(f"    ✅ PDF heruntergeladen: {save_path.name}")
            return save_path
        except PlaywrightTimeout:
            print("    ⚠️  expect_download Timeout – versuche Alternative ...")

    pages_before = len(page.context.pages)
    downloaded_file = [None]

    def on_download(download):
        fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
        path = _cfg.DOWNLOAD_DIR / f"{booking_ref}_{fname}"
        download.save_as(str(path))
        downloaded_file[0] = path
        print(f"    ✅ PDF heruntergeladen (event): {fname}")

    page.once("download", on_download)
    page.context.once("page", lambda new_page: new_page.once("download", on_download))

    try:
        with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as download_info:
            download_btn.first.click()
            print("    → Download-Button geklickt ...")
        download = download_info.value
        fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
        save_path = _cfg.DOWNLOAD_DIR / f"{booking_ref}_{fname}"
        download.save_as(str(save_path))
        print(f"    ✅ PDF heruntergeladen: {save_path.name}")
        _close_extra_tabs(page, pages_before)
        return save_path
    except PlaywrightTimeout:
        error_msg = page.locator('text="Fehler beim Rechnungsabruf"')
        if error_msg.count() > 0:
            print("    ⚠️  bahn.de meldet: 'Fehler beim Rechnungsabruf' – temporäres Serverproblem")
            _close_extra_tabs(page, pages_before)
            return None

    page.wait_for_timeout(3000)
    all_pages = page.context.pages
    if len(all_pages) > pages_before:
        new_tab = all_pages[-1]
        print(f"    ℹ️  Neuer Tab geöffnet: {new_tab.url[:60]}")
        try:
            new_tab.wait_for_load_state("domcontentloaded", timeout=15000)
            new_tab.wait_for_timeout(2000)

            if ".pdf" in new_tab.url.lower():
                print("    📄 PDF-URL im neuen Tab – versuche Response-Download ...")
                response = new_tab.goto(new_tab.url)
                if response:
                    body = response.body()
                    fname = f"rechnung_{booking_ref}_{timestamp}.pdf"
                    save_path = _cfg.DOWNLOAD_DIR / fname
                    save_path.write_bytes(body)
                    print(f"    ✅ PDF gespeichert: {fname}")
                    try:
                        new_tab.close()
                    except Exception:
                        pass
                    return save_path

            new_tab.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            new_tab.wait_for_timeout(2000)

            new_dl_btn = new_tab.locator(
                'a:has-text("Rechnung als PDF herunterladen"), '
                'button:has-text("Rechnung als PDF herunterladen")'
            )
            if new_dl_btn.count() > 0:
                try:
                    with new_tab.expect_download(timeout=DOWNLOAD_TIMEOUT) as dl_info:
                        new_dl_btn.first.click()
                    download = dl_info.value
                    fname = download.suggested_filename or f"rechnung_{booking_ref}_{timestamp}.pdf"
                    save_path = _cfg.DOWNLOAD_DIR / f"{booking_ref}_{fname}"
                    download.save_as(str(save_path))
                    print(f"    ✅ PDF heruntergeladen (neuer Tab): {save_path.name}")
                    try:
                        new_tab.close()
                    except Exception:
                        pass
                    return save_path
                except PlaywrightTimeout:
                    print("    ⚠️  Auch im neuen Tab kein Download ausgelöst")

            try:
                new_tab.close()
            except Exception:
                pass
        except Exception as e:
            print(f"    ⚠️  Fehler im neuen Tab: {e}")

    if downloaded_file[0]:
        _close_extra_tabs(page, pages_before)
        return downloaded_file[0]

    print(f"    ⚠️  Download fehlgeschlagen für {booking_ref}")
    _close_extra_tabs(page, pages_before)
    return None


def _close_extra_tabs(page, expected_count: int):
    try:
        for p in page.context.pages[expected_count:]:
            p.close()
    except Exception:
        pass


def download_invoices(page, timer: Timer, download_all: bool = False, booking_refs: list[str] | None = None) -> tuple[list[Path], list[str]]:
    """Navigiert zu 'Meine Reisen' und lädt Rechnungen herunter."""
    _cfg.DOWNLOAD_DIR.mkdir(exist_ok=True)
    history = load_history()
    downloaded_files = []

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

    # Modus 2: Alle / letzte Reise(n) durchgehen
    print("📄 Suche Rechnungen über 'Meine Reisen' ...")
    page.goto(TRIPS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    # Session abgelaufen? Re-Login einmalig versuchen
    if "anmelden" in page.url.lower():
        print("  ⚠️  bahn.de Session abgelaufen — Re-Login ...")
        login(page, timer)
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
                'button:has-text("Rechnung"), a:has-text("Rechnung"), '
                'button:has-text("Invoice"), [data-testid*="invoice"], [class*="invoice"]'
            )

            if invoice_btn.count() > 0:
                with page.expect_download(timeout=DOWNLOAD_TIMEOUT) as download_info:
                    invoice_btn.first.click()
                download = download_info.value
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                original_name = download.suggested_filename or f"rechnung_{timestamp}.pdf"
                save_path = _cfg.DOWNLOAD_DIR / f"{timestamp}_{original_name}"
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
