"""Spiegel Abo-Rechnungs-Download.

Navigiert zur Rechnungsseite und lädt die passende PDF herunter.
Nutzt eigenes Browser-Data-Verzeichnis (separate Spiegel-Session).
"""

import os
import re
import time
from datetime import datetime
from pathlib import Path

import requests as http_req
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

from src.config import ROOT_DIR, _get_secret

KONTO_URL = "https://gruppenkonto.spiegel.de/meinkonto/uebersicht.html"
ABO_URL = "https://gruppenkonto.spiegel.de/meinkonto/abonnements/anzeigen.html"
BROWSER_DATA = ROOT_DIR / ".browser-data-spiegel"

SPIEGEL_EMAIL = _get_secret("SPIEGEL_EMAIL", "op://Shared/Spiegel/username")
SPIEGEL_PASSWORD = _get_secret("SPIEGEL_PASSWORD", "op://Shared/Spiegel/password")


def _login_spiegel(page, email: str, password: str) -> bool:
    """Login bei Spiegel Gruppenkonto (zweistufig: Email -> Passwort)."""
    print("  Spiegel Login ...")

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
        print("  Passwort-Feld nicht sichtbar")
        return False

    if "anmelden" in page.url:
        print("  Spiegel Login fehlgeschlagen")
        return False

    print("  Spiegel Login erfolgreich")
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
    """Parst DD.MM.YYYY oder DD.MM.YY."""
    for fmt in ("%d.%m.%Y", "%d.%m.%y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


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

    spiegel_entries = [
        e for e in entries
        if not e.get("is_credit") and "SPIEGEL" in e.get("vendor", "").upper()
    ]
    if not spiegel_entries:
        return []

    if not SPIEGEL_EMAIL or not SPIEGEL_PASSWORD:
        print("\n  Spiegel: Keine Credentials konfiguriert")
        return []

    print(f"\n  Spiegel: Suche {len(spiegel_entries)} Rechnung(en) ...")

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
                print("  Keine Rechnungsseite gefunden")
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

            cookies = page.context.cookies("https://gruppenkonto.spiegel.de")
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

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
                    print(f"  Keine passende Rechnung gefunden")
                    continue

                href = best_row["href"]
                if not href:
                    continue

                try:
                    resp = http_req.get(href, headers={"Cookie": cookie_str}, timeout=15)
                    if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                        fname = f"{date_prefix}Spiegel_Rechnung_{best_row['nr']}.pdf"
                        save_path = download_dir / fname
                        save_path.write_bytes(resp.content)
                        results.append((entry, save_path))
                        used_nrs.add(best_row["nr"])
                        print(f"  -> {fname} ({len(resp.content) / 1024:.1f} KB)")
                    else:
                        print(f"  HTTP {resp.status_code}")
                except Exception as e:
                    print(f"  Download fehlgeschlagen: {e}")

            if results:
                print(f"  {len(results)} Spiegel-Rechnung(en) heruntergeladen")
            return results

        finally:
            context.close()
