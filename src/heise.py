"""Heise Medien Rechnungs-Download über Plenigo Self-Service Portal."""

import time
from pathlib import Path

import requests as http_req
from playwright.sync_api import TimeoutError as PlaywrightTimeout


HEISE_URL = "https://www.heise.de/sso/registration/add_subscriber_id/plenigo?plsnippet=order"
PLENIGO_BASE = "https://selfservice.plenigo.com"


def download_heise_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
) -> list[Path]:
    """Lädt Heise-Rechnungen über das Plenigo Self-Service Portal."""
    download_dir.mkdir(parents=True, exist_ok=True)

    heise_entries = [
        e for e in entries
        if not e.get("is_credit") and "HEISE" in e.get("vendor", "").upper()
    ]
    if not heise_entries:
        return []

    print(f"\n📰 Heise: Suche {len(heise_entries)} Rechnung(en) ...")

    # Heise-Seite laden um Plenigo-iframe zu finden
    page.goto(HEISE_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    iframe = page.locator('iframe[src*="plenigo"]')
    if iframe.count() == 0:
        print("  ❌ Plenigo-iframe nicht gefunden")
        return []

    iframe_src = iframe.first.get_attribute("src") or ""
    if not iframe_src:
        print("  ❌ Keine iframe-URL")
        return []

    # Direkt zum Plenigo Self-Service navigieren
    page.goto(iframe_src, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    # Rechnungen-Tab klicken
    rech_link = page.locator('a:has-text("Rechnungen"), [data-test*="invoice"]')
    if rech_link.count() > 0:
        rech_link.first.click()
        page.wait_for_timeout(3000)

    # PDF-Links extrahieren
    pdf_links = page.locator('a[href*="get_pdf"]')
    count = pdf_links.count()
    print(f"  📋 {count} Rechnung(en) gefunden")

    if count == 0:
        return []

    # Session-ID aus den Links extrahieren
    first_href = pdf_links.first.get_attribute("href") or ""

    # Cookies aus der Plenigo-Session holen
    cookies = page.context.cookies(PLENIGO_BASE)
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    downloaded = []
    used_links = set()

    for entry in heise_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        print(f"  🔍 Heise  {amount:.2f} EUR  ({date_str})")

        # Nächsten ungenutzten PDF-Link nehmen
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
                    downloaded.append(save_path)
                    used_links.add(href)
                    print(f"  ✅ {fname} ({len(resp.content) / 1024:.1f} KB)")
                    break
                else:
                    print(f"  ⚠️  HTTP {resp.status_code}, {len(resp.content)} bytes")
            except Exception as e:
                print(f"  ⚠️  Download fehlgeschlagen: {e}")

    if downloaded:
        print(f"  📦 {len(downloaded)} Heise-Rechnung(en) heruntergeladen")
    return downloaded
