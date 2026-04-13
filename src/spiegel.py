"""Spiegel Abo-Rechnungs-Download.

Navigiert zur Rechnungsseite und lädt die passende PDF herunter.
Nutzt eigenes Browser-Data-Verzeichnis (separate Spiegel-Session).
"""

import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from src.config import ROOT_DIR, _get_secret
from src.util import parse_date

KONTO_URL = "https://gruppenkonto.spiegel.de/meinkonto/uebersicht.html"
ABO_URL = "https://gruppenkonto.spiegel.de/meinkonto/abonnements/anzeigen.html"
BROWSER_DATA = ROOT_DIR / ".browser-data-spiegel"

SPIEGEL_EMAIL = _get_secret("SPIEGEL_EMAIL", "op://Shared/Spiegel/username")
SPIEGEL_PASSWORD = _get_secret("SPIEGEL_PASSWORD", "op://Shared/Spiegel/password")


def _filter_spiegel_entries(entries: list[dict]) -> list[dict]:
    """Filtert Spiegel-Einträge aus MC-Entries."""
    return [
        e for e in entries
        if not e.get("is_credit") and "SPIEGEL" in e.get("vendor", "").upper()
    ]


def _login_spiegel(page, email: str, password: str) -> bool:
    """Login bei Spiegel Gruppenkonto (zweistufig: Email -> Passwort)."""
    print("  🔑 Spiegel Login ...")

    email_input = page.locator('input[name="loginform:username"], input[type="email"]')
    if email_input.count() > 0:
        email_input.first.fill(email)
        page.wait_for_timeout(500)

    submit = page.locator('button:has-text("Anmelden"), button[type="submit"]')
    if submit.count() > 0:
        submit.first.click()
        page.wait_for_timeout(3000)

    pw_input = page.locator('input[name="password"]:visible, input[type="password"]:visible')
    try:
        pw_input.first.wait_for(state="visible", timeout=10000)
        pw_input.first.fill(password)
        page.wait_for_timeout(500)

        submit = page.locator('button:has-text("Anmelden"), button[type="submit"]')
        if submit.count() > 0:
            submit.first.click()
            page.wait_for_timeout(5000)
    except PlaywrightTimeout:
        print("  ⚠️ Passwort-Feld nicht sichtbar")
        return False

    if "anmelden" in page.url:
        print("  ❌ Spiegel Login fehlgeschlagen")
        return False

    print("  ✅ Spiegel Login erfolgreich")
    return True


def _find_rechnungen_url(page) -> str | None:
    """Findet die Rechnungs-URL fuer das aktive Abo."""
    page.goto(ABO_URL, wait_until="domcontentloaded", timeout=15000)
    page.wait_for_timeout(3000)

    # Suche nach "Rechnungen" Link mit Subscription-ID
    links = page.query_selector_all('a[href*="rechnungen2.html"]')
    for a in links:
        href = a.get_attribute("href") or ""
        if "dnt_uSubId" in href:
            full = href if href.startswith("http") else f"https://gruppenkonto.spiegel.de{href}"
            return full
    return None


def _parse_date(date_str: str) -> datetime | None:
    """Parst Datumsformate — delegiert an zentrale parse_date()."""
    return parse_date(date_str)


def download_spiegel_invoices(
    entries: list[dict],
    download_dir: Path,
    headed: bool = False,
) -> list[tuple[dict, Path]]:
    """Lädt Spiegel Abo-Rechnungen herunter.

    Returns:
        Liste von (entry, filepath) Tupeln.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    spiegel_entries = _filter_spiegel_entries(entries)
    if not spiegel_entries:
        return []

    if not SPIEGEL_EMAIL or not SPIEGEL_PASSWORD:
        print("\n  ⚠️ Spiegel: Keine Credentials konfiguriert")
        return []

    print(f"\n  🔍 Spiegel: Suche {len(spiegel_entries)} Rechnung(en) ...")

    BROWSER_DATA.mkdir(exist_ok=True)

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(BROWSER_DATA),
            headless=not headed,
            accept_downloads=True,
            locale="de-DE",
        )
        page = context.new_page()

        try:
            page.goto(KONTO_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            if "anmelden" in page.url:
                if not _login_spiegel(page, SPIEGEL_EMAIL, SPIEGEL_PASSWORD):
                    return []

            # Rechnungsseite finden
            rechnungen_url = _find_rechnungen_url(page)
            if not rechnungen_url:
                print("  ⚠️ Keine Rechnungsseite gefunden")
                return []

            page.goto(rechnungen_url, wait_until="domcontentloaded", timeout=15000)
            page.wait_for_timeout(3000)

            # Rechnungstabelle parsen: Datum | Nr | Beschreibung | Preis | Download-Link
            rows = page.evaluate("""() => {
                const trs = document.querySelectorAll('tr');
                const results = [];
                for (const tr of trs) {
                    const tds = tr.querySelectorAll('td');
                    if (tds.length >= 4) {
                        const link = tr.querySelector('a[href*="downloadInvoiceId"]');
                        results.push({
                            date: tds[0]?.innerText?.trim() || '',
                            nr: tds[1]?.innerText?.trim() || '',
                            desc: tds[2]?.innerText?.trim() || '',
                            price: tds[3]?.innerText?.trim() || '',
                            href: link?.href || '',
                        });
                    }
                }
                return results;
            }""")
            print(f"  {len(rows)} Rechnung(en) auf der Seite")

            results = []
            used_nrs = set()

            for entry in spiegel_entries:
                amount = entry.get("amount", 0)
                date_str = entry.get("date", "")
                entry_date = _parse_date(date_str)
                print(f"  Spiegel  {amount:.2f} EUR  ({date_str})")

                # Match per Datum (naechstes Rechnungsdatum zum MC-Datum)
                best_row = None
                best_diff = float('inf')
                for row in rows:
                    if row["nr"] in used_nrs:
                        continue
                    row_date = _parse_date(row["date"])
                    if row_date and entry_date:
                        diff = abs((row_date - entry_date).days)
                        if diff <= 14 and diff < best_diff:
                            best_diff = diff
                            best_row = row

                if not best_row:
                    print(f"  ⚠️ Keine passende Rechnung gefunden")
                    continue

                href = best_row["href"]
                if not href:
                    continue

                date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                fname = f"{date_prefix}Spiegel_Rechnung_{best_row['nr']}.pdf"
                save_path = download_dir / fname

                try:
                    invoice_id = best_row["nr"]
                    dl_link = page.locator(f'a[href*="downloadInvoiceId={invoice_id}"]')
                    if dl_link.count() == 0:
                        # Fallback: suche mit kuerzerer ID
                        dl_link = page.locator(f'a[href*="downloadInvoiceId"]').filter(
                            has_text="herunterladen"
                        )

                    with page.expect_download(timeout=15000) as dl_info:
                        dl_link.first.click()
                    download = dl_info.value
                    download.save_as(str(save_path))

                    if save_path.stat().st_size > 500 and save_path.read_bytes()[:5] == b"%PDF-":
                        results.append((entry, save_path))
                        used_nrs.add(best_row["nr"])
                        print(f"  📎 {fname} ({save_path.stat().st_size / 1024:.1f} KB)")
                    else:
                        save_path.unlink(missing_ok=True)
                        print(f"  ⚠️ Kein gueltiges PDF")
                except PlaywrightTimeout:
                    print(f"  ❌ Download-Timeout")
                except Exception as e:
                    print(f"  ❌ Download fehlgeschlagen: {e}")

            if results:
                print(f"  ✅ {len(results)} Spiegel-Rechnung(en) heruntergeladen")
            return results

        finally:
            context.close()
