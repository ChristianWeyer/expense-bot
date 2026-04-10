"""Figma Invoice Download — ueber interne API (kein Browser-Scraping noetig).

Die Figma API /api/plans/team/{id}/invoices liefert Stripe PDF-URLs direkt.
Braucht Figma-Session-Cookies aus dem CDP-Browser oder Auto-Login.
"""

import time
from datetime import datetime
from pathlib import Path

import requests as http_req
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.config import FIGMA_TEAM_ID, FIGMA_EMAIL, FIGMA_PASSWORD
from src.util import parse_date


FIGMA_LOGIN_URL = "https://www.figma.com/login"


def _login_figma(page, email: str, password: str) -> bool:
    """Login bei Figma (email + password)."""
    print("  🔑 Figma Login ...")

    page.goto(FIGMA_LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(3000)

    email_input = page.locator('input[name="email"], input[type="email"]')
    if email_input.count() > 0:
        email_input.first.fill(email)
        page.wait_for_timeout(500)

    pw_input = page.locator('input[name="password"], input[type="password"]')
    if pw_input.count() > 0:
        pw_input.first.fill(password)
        page.wait_for_timeout(500)

    submit = page.locator('button[type="submit"], button:has-text("Log in"), button:has-text("Anmelden")')
    if submit.count() > 0:
        submit.first.click()
        page.wait_for_timeout(5000)

    # 2FA-Check
    if "two_factor" in page.url or "mfa" in page.url:
        print("  📱 Figma 2FA erforderlich!")
        print("  → Bitte im Browser loesen. Warte max. 120s ...")
        try:
            page.wait_for_url(
                lambda u: "two_factor" not in u and "mfa" not in u and "login" not in u,
                timeout=120000,
            )
        except PlaywrightTimeout:
            print("  ❌ Figma Login Timeout")
            return False

    if "login" in page.url:
        print("  ❌ Figma Login fehlgeschlagen")
        return False

    print("  ✅ Figma Login erfolgreich")
    return True


def _filter_figma_entries(entries: list[dict]) -> list[dict]:
    """Filtert Figma-Einträge aus MC-Entries."""
    return [e for e in entries if not e.get("is_credit") and "FIGMA" in e.get("vendor", "").upper()]


def download_figma_invoices(page, entries: list[dict], download_dir: Path) -> list[tuple[dict, Path]]:
    """Lädt Figma-Invoices ueber die interne API.

    Returns:
        Liste von (entry, filepath) Tupeln.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    figma_entries = _filter_figma_entries(entries)
    if not figma_entries or not FIGMA_TEAM_ID:
        return []

    print(f"\n  🔍 Figma: Suche {len(figma_entries)} Rechnung(en) ...")

    cookies = page.context.cookies("https://www.figma.com")
    if not cookies:
        if FIGMA_EMAIL and FIGMA_PASSWORD:
            if not _login_figma(page, FIGMA_EMAIL, FIGMA_PASSWORD):
                return []
            cookies = page.context.cookies("https://www.figma.com")
        else:
            print("  ⚠️ Figma: Nicht eingeloggt und keine Credentials konfiguriert")
            print("  → FIGMA_EMAIL/FIGMA_PASSWORD setzen oder op://Private/Figma konfigurieren")
            return []
    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

    try:
        resp = http_req.get(
            f"https://www.figma.com/api/plans/team/{FIGMA_TEAM_ID}/invoices",
            headers={"Cookie": cookie_str},
            timeout=60,
        )
        if resp.status_code in (401, 403):
            if FIGMA_EMAIL and FIGMA_PASSWORD:
                print(f"  ⚠️ Figma: Session abgelaufen (HTTP {resp.status_code}), versuche Login ...")
                page.context.clear_cookies()
                if _login_figma(page, FIGMA_EMAIL, FIGMA_PASSWORD):
                    cookies = page.context.cookies("https://www.figma.com")
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)
                    resp = http_req.get(
                        f"https://www.figma.com/api/plans/team/{FIGMA_TEAM_ID}/invoices",
                        headers={"Cookie": cookie_str},
                        timeout=60,
                    )
                    if resp.status_code != 200:
                        print(f"  ❌ Figma API Fehler nach Login: HTTP {resp.status_code}")
                        return []
                else:
                    return []
            else:
                print(f"  ⚠️ Figma: Nicht eingeloggt (HTTP {resp.status_code})")
                return []
        if resp.status_code != 200:
            print(f"  ❌ API Fehler: HTTP {resp.status_code}")
            return []

        invoices = resp.json().get("meta", {}).get("invoices", [])
        paid = [inv for inv in invoices if inv.get("state") == "paid" and inv.get("invoice_pdf_url")]
        print(f"  {len(paid)} bezahlte Invoice(s) mit PDF")
    except Exception as e:
        print(f"  ❌ API Fehler: {e}")
        return []

    if not paid:
        return []

    results = []
    used_invoices = set()

    for entry in figma_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        print(f"  Figma  {amount:.2f} EUR  ({date_str})")

        entry_date = parse_date(date_str)

        # Passende Invoice finden (nach Datum, nur ungenutzte)
        best_inv = None
        best_distance = float('inf')

        for inv in paid:
            inv_id = inv.get("id", "")
            if inv_id in used_invoices:
                continue
            issued = inv.get("issued_at", "")[:10]
            inv_date = parse_date(issued)
            if inv_date and entry_date:
                distance = abs((inv_date - entry_date).days)
                if distance < best_distance:
                    best_distance = distance
                    best_inv = inv

        if not best_inv:
            # Fallback: nächste ungenutzte
            for inv in paid:
                if inv.get("id", "") not in used_invoices:
                    best_inv = inv
                    break

        if not best_inv:
            print(f"  ⚠️ Keine PDF-URL")
            continue

        pdf_url = best_inv.get("invoice_pdf_url", "")
        if not pdf_url:
            print(f"  ⚠️ Keine PDF-URL")
            continue

        try:
            pdf_resp = http_req.get(pdf_url, timeout=30)
            if pdf_resp.status_code == 200 and pdf_resp.content[:4] == b"%PDF":
                date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                fname = f"{date_prefix}Figma_Invoice.pdf"
                save_path = download_dir / fname
                save_path.write_bytes(pdf_resp.content)
                results.append((entry, save_path))
                used_invoices.add(best_inv.get("id", ""))
                print(f"  📎 {fname} ({len(pdf_resp.content) / 1024:.1f} KB)")
            else:
                print(f"  ❌ PDF-Download fehlgeschlagen: HTTP {pdf_resp.status_code}")
        except Exception as e:
            print(f"  ❌ Download fehlgeschlagen: {e}")

    if results:
        print(f"  ✅ {len(results)} Figma-Rechnung(en) heruntergeladen")
    return results
