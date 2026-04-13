"""Adobe Rechnungs-Download per Playwright.

Navigiert zu account.adobe.com/orders/billing-history und laedt die passende
Invoice-PDF per Klick auf den Download-Button in der jeweiligen Zeile herunter.
"""

import re
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.config import ADOBE_EMAIL, ADOBE_PASSWORD
from src.util import parse_date


BILLING_URL = "https://account.adobe.com/orders/billing-history"


def _login_adobe(page, email: str, password: str) -> bool:
    """Login bei Adobe (email + password, mit 2FA-Support).

    Adobe IMS hat mehrstufigen Login-Flow mit JavaScript-rendering.
    Bei frischem Profil kann das Passwort-Feld nicht direkt sichtbar sein.
    Fallback: auf manuellen Login warten.
    """
    print("  🔑 Adobe Login ...")

    # Email eingeben (mit grosszügigerem Warten fürs IMS-Rendering)
    try:
        page.wait_for_selector('input[name="username"], input[type="email"]', timeout=10000)
    except PlaywrightTimeout:
        pass

    email_input = page.locator('input[name="username"], input[type="email"]')
    if email_input.count() > 0:
        try:
            email_input.first.fill(email)
            page.wait_for_timeout(800)
            cont_btn = page.locator('button:has-text("Continue"), button:has-text("Weiter"), button[type="submit"]')
            if cont_btn.count() > 0:
                cont_btn.first.click()
                page.wait_for_timeout(4000)
        except Exception:
            pass

    # Passwort eingeben
    pw_input = page.locator('input[name="password"], input[type="password"]')
    try:
        pw_input.first.wait_for(state="visible", timeout=8000)
        pw_input.first.fill(password)
        page.wait_for_timeout(500)
        submit = page.locator('button:has-text("Continue"), button:has-text("Anmelden"), button[type="submit"]')
        if submit.count() > 0:
            submit.first.click()
            page.wait_for_timeout(5000)
    except PlaywrightTimeout:
        # Auto-Login nicht möglich — manuellen Login abwarten
        print("  📱 Adobe: Auto-Login nicht möglich")
        print("  → Bitte manuell in Chrome Canary einloggen. Warte max. 120s ...")
        try:
            page.wait_for_url(
                lambda u: "account.adobe.com" in u and "signin" not in u and "auth" not in u,
                timeout=120000,
            )
        except PlaywrightTimeout:
            print("  ❌ Adobe Login Timeout")
            return False

    # 2FA-Check
    if "challenge" in page.url or "mfa" in page.url or "verify" in page.url:
        print("  📱 Adobe 2FA erforderlich!")
        print("  → Bitte im Browser loesen. Warte max. 120s ...")
        try:
            page.wait_for_url(
                lambda u: "account.adobe.com" in u and "challenge" not in u,
                timeout=120000,
            )
        except PlaywrightTimeout:
            print("  ❌ Adobe Login Timeout")
            return False

    if "signin" in page.url or "auth.services.adobe.com" in page.url:
        print("  ❌ Adobe Login fehlgeschlagen")
        return False

    print("  ✅ Adobe Login erfolgreich")
    return True


def _parse_date(date_str: str) -> datetime | None:
    """Parst Adobe-Datumsformate — delegiert an zentrale parse_date()."""
    return parse_date(date_str)


def _parse_entry_date(date_str: str) -> datetime | None:
    """Parst MC-Entry-Datum — delegiert an zentrale parse_date()."""
    return parse_date(date_str)


def _extract_rows(page) -> list[dict]:
    """Extrahiert alle Rechnungszeilen aus der Adobe Billing-Tabelle.

    Adobe nutzt React Spectrum: DATE ist role=rowheader, Rest ist role=gridcell.
    Zuverlaessige Extraktion ueber data-testid Attribute.
    """
    rows = page.evaluate("""() => {
        const grid = document.querySelector('[role="grid"]');
        if (!grid) return [];
        const rowEls = grid.querySelectorAll('[role="row"]');
        const results = [];
        for (const row of rowEls) {
            const dateEl = row.querySelector('[data-testid="formatted-date"]');
            if (!dateEl) continue;
            const dateText = dateEl.textContent.trim();
            const priceInt = row.querySelector('[data-testid="price-integer"]');
            const priceDec = row.querySelector('[data-testid="price-decimals"]');
            const priceCur = row.querySelector('[data-testid="price-currency-symbol-after"]');
            let amountText = '';
            if (priceInt && priceDec) {
                amountText = priceInt.textContent + ',' + priceDec.textContent;
                if (priceCur) amountText += ' ' + priceCur.textContent;
            }
            const cells = row.querySelectorAll('[role="gridcell"]');
            const type = cells.length > 0 ? cells[0].textContent.trim() : '';
            const orderNr = cells.length > 1 ? cells[1].textContent.trim() : '';
            const plan = row.querySelector('[data-testid="product-name"]');
            const planText = plan ? plan.textContent.trim() : '';
            results.push({date: dateText, type: type, order: orderNr, plan: planText, amount: amountText});
        }
        return results;
    }""")
    return rows


def _parse_amount(amount_str: str) -> float | None:
    """Parst '66,45 EUR' oder '66,45 \\u20ac' zu float."""
    clean = amount_str.replace("EUR", "").replace("\u20ac", "").replace("\xa0", "").replace(" ", "").strip()
    clean = clean.replace(".", "").replace(",", ".")
    try:
        return float(clean)
    except ValueError:
        return None


def _filter_adobe_entries(entries: list[dict]) -> list[dict]:
    """Filtert Adobe-Einträge aus MC-Entries."""
    return [
        e for e in entries
        if not e.get("is_credit") and "ADOBE" in e.get("vendor", "").upper()
    ]


def download_adobe_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
) -> list[tuple[dict, Path]]:
    """Laedt Adobe Rechnungen fuer MC-Eintraege herunter.

    Returns:
        Liste von (entry, filepath) Tupeln.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    adobe_entries = _filter_adobe_entries(entries)
    if not adobe_entries:
        return []

    print(f"\n  🔍 Adobe: Suche {len(adobe_entries)} Rechnung(en) ...")

    page.goto(BILLING_URL, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    # Auth-Check + Auto-Login
    if "login" in page.url or "auth" in page.url or "signin" in page.url:
        if ADOBE_EMAIL and ADOBE_PASSWORD:
            if not _login_adobe(page, ADOBE_EMAIL, ADOBE_PASSWORD):
                return []
            page.goto(BILLING_URL, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
        else:
            print("  ⚠️ Adobe: Nicht eingeloggt und keine Credentials konfiguriert")
            print("  → ADOBE_EMAIL/ADOBE_PASSWORD setzen oder in Chrome Canary einloggen")
            return []

    # Warte auf Tabelle
    try:
        page.wait_for_selector('[role="grid"], table', timeout=15000)
        page.wait_for_timeout(2000)
    except PlaywrightTimeout:
        print("  ⚠️ Adobe: Keine Rechnungstabelle gefunden")
        return []

    rows = _extract_rows(page)
    print(f"  {len(rows)} Rechnung(en) auf der Seite")

    if not rows:
        return []

    results = []
    used_indices = set()

    for entry in adobe_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        entry_date = _parse_entry_date(date_str)
        vendor = entry.get("vendor", "?")
        print(f"  {vendor}  {amount:.2f} EUR  ({date_str})")

        # Match: Datum (+-21 Tage) + Betrag
        best_idx = None
        best_diff = float('inf')

        for i, row in enumerate(rows):
            if i in used_indices:
                continue
            row_date = _parse_date(row["date"])
            row_amount = _parse_amount(row["amount"])

            if entry_date and row_date:
                day_diff = abs((row_date - entry_date).days)
                if day_diff > 21:
                    continue

                # Betrag als Tiebreaker
                amount_diff = abs(row_amount - amount) if row_amount is not None else 0
                score = day_diff + amount_diff * 0.1
                if score < best_diff:
                    best_diff = score
                    best_idx = i

        if best_idx is None:
            print(f"  ⚠️ Keine passende Rechnung gefunden")
            continue

        matched_row = rows[best_idx]
        print(f"  ✅ Match: {matched_row['date']} | {matched_row['order']} | {matched_row['amount']}")

        # Download-Button in der Zeile klicken (Index + 1 wegen Header-Zeile)
        downloaded = _click_download(page, best_idx, matched_row, download_dir, date_str)
        if downloaded:
            used_indices.add(best_idx)
            results.append((entry, downloaded))
            print(f"  📎 {downloaded.name} ({downloaded.stat().st_size / 1024:.1f} KB)")
        else:
            print(f"  ❌ Download fehlgeschlagen")

        time.sleep(1)

    if results:
        print(f"  ✅ {len(results)} Adobe-Rechnung(en) heruntergeladen")
    return results


def _click_download(page, row_idx: int, row_data: dict, download_dir: Path, date_str: str) -> Path | None:
    """Klickt den Download-Button in der passenden Zeile."""
    try:
        # Datenzeilen = rows mit data-testid="formatted-date" (haben rowheader)
        data_rows = page.locator('[role="grid"] [role="row"]:has([data-testid="formatted-date"])').all()

        if row_idx >= len(data_rows):
            print(f"  ⚠️ Zeile {row_idx} nicht gefunden (nur {len(data_rows)} Zeilen)")
            return None

        target_row = data_rows[row_idx]

        # Download-Button: aria-label="Download PDF"
        dl_btn = target_row.locator('button[aria-label="Download PDF"]')
        if dl_btn.count() == 0:
            print(f"  ⚠️ Kein Download-Button gefunden")
            return None
        dl_btn = dl_btn.first

        # Klick und Download erwarten
        with page.expect_download(timeout=15000) as dl_info:
            dl_btn.click()
        download = dl_info.value

        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        order_nr = row_data.get("order", "")
        fname = download.suggested_filename or f"Adobe_{order_nr}_invoice.pdf"
        save_path = download_dir / f"{date_prefix}{fname}"
        download.save_as(str(save_path))

        # Validierung
        if save_path.stat().st_size < 500:
            save_path.unlink(missing_ok=True)
            return None

        return save_path

    except PlaywrightTimeout:
        print(f"  ❌ Download-Timeout (kein Download ausgeloest)")
        return None
    except Exception as e:
        print(f"  ❌ Fehler: {e}")
        return None
