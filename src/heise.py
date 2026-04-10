"""Heise Medien Rechnungs-Download ueber Plenigo Self-Service Portal."""

import time
from pathlib import Path

import requests as http_req
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.config import HEISE_EMAIL, HEISE_PASSWORD


HEISE_URL = "https://www.heise.de/sso/registration/add_subscriber_id/plenigo?plsnippet=order"
PLENIGO_BASE = "https://selfservice.plenigo.com"


def _login_heise(page, email: str, password: str) -> bool:
    """Login bei Heise SSO (email + password)."""
    print("  Heise Login ...")

    email_input = page.locator('input[name="email"], input[type="email"], input#username')
    if email_input.count() > 0:
        email_input.first.fill(email)
        page.wait_for_timeout(500)

    pw_input = page.locator('input[name="password"], input[type="password"]')
    if pw_input.count() > 0:
        pw_input.first.fill(password)
        page.wait_for_timeout(500)

    submit = page.locator('button[type="submit"], button:has-text("Anmelden"), button:has-text("Login")')
    if submit.count() > 0:
        submit.first.click()
        page.wait_for_timeout(5000)

    if "anmelden" in page.url or "login" in page.url:
        print("  Heise Login fehlgeschlagen")
        return False

    print("  Heise Login erfolgreich")
    return True


def download_heise_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
) -> list[tuple[dict, Path]]:
    """Lädt Heise-Rechnungen ueber das Plenigo Self-Service Portal.

    Returns:
        Liste von (entry, filepath) Tupeln.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    heise_entries = [
        e for e in entries
        if not e.get("is_credit") and "HEISE" in e.get("vendor", "").upper()
    ]
    if not heise_entries:
        return []

    print(f"\n  Heise: Suche {len(heise_entries)} Rechnung(en) ...")

    page.goto(HEISE_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    # Auth-Check + Auto-Login
    if "sso/login" in page.url or "anmelden" in page.url or "login" in page.url:
        if HEISE_EMAIL and HEISE_PASSWORD:
            if not _login_heise(page, HEISE_EMAIL, HEISE_PASSWORD):
                return []
            page.goto(HEISE_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
        else:
            print("  Heise: Nicht eingeloggt und keine Credentials konfiguriert")
            print("  -> HEISE_EMAIL/HEISE_PASSWORD setzen oder op://Private/Heise konfigurieren")
            return []

    iframe = page.locator('iframe[src*="plenigo"]')
    if iframe.count() == 0:
        print("  Heise: Plenigo-iframe nicht gefunden (nicht eingeloggt?)")
        print("  -> Bitte in Chrome Canary bei heise.de einloggen")
        return []

    iframe_src = iframe.first.get_attribute("src") or ""
    if not iframe_src:
        print("  Keine iframe-URL")
        return []

    page.goto(iframe_src, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    rech_link = page.locator('a:has-text("Rechnungen"), [data-test*="invoice"]')
    if rech_link.count() > 0:
        rech_link.first.click()
        page.wait_for_timeout(3000)

    pdf_links = page.locator('a[href*="get_pdf"]')
    count = pdf_links.count()
    print(f"  {count} Rechnung(en) gefunden")

    if count == 0:
        return []

    cookies = page.context.cookies(PLENIGO_BASE)
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    results = []
    used_links = set()

    for entry in heise_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        print(f"  Heise  {amount:.2f} EUR  ({date_str})")

        for i in range(count):
            href = pdf_links.nth(i).get_attribute("href") or ""
            if href in used_links:
                continue

            full_url = f"{PLENIGO_BASE}{href}" if href.startswith("/") else href

            try:
                resp = http_req.get(full_url, headers={"Cookie": cookie_str}, timeout=30)
                if resp.status_code == 200 and resp.content[:4] == b"%PDF":
                    date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                    fname = f"{date_prefix}Heise_Rechnung.pdf"
                    save_path = download_dir / fname
                    save_path.write_bytes(resp.content)
                    results.append((entry, save_path))
                    used_links.add(href)
                    print(f"  -> {fname} ({len(resp.content) / 1024:.1f} KB)")
                    break
                else:
                    print(f"  HTTP {resp.status_code}, {len(resp.content)} bytes")
            except Exception as e:
                print(f"  Download fehlgeschlagen: {e}")

    if results:
        print(f"  {len(results)} Heise-Rechnung(en) heruntergeladen")
    return results
