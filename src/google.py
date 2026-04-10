"""Google Payments Beleg-Download (YouTube Premium, Google One).

Transaktionen sind in einem iframe von payments.google.com.
Klickt Transaktionen per Betrag an, öffnet Transaktionsdetails,
und lädt die Rechnung mit ausgewiesener Umsatzsteuer als PDF.
"""

import base64
import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout

from src.config import GOOGLE_EMAIL, GOOGLE_PASSWORD, PAGE_TIMEOUT, LOGIN_TIMEOUT


ACTIVITY_URL = "https://pay.google.com/gp/w/home/activity"


def _login_google(page, email: str, password: str) -> bool:
    """Login bei Google Account (email -> password -> ggf. 2FA)."""
    print("  Google Login ...")

    # Email eingeben
    email_input = page.locator('input[type="email"]')
    if email_input.count() > 0:
        email_input.first.fill(email)
        page.wait_for_timeout(500)
        next_btn = page.locator('button:has-text("Next"), button:has-text("Weiter"), #identifierNext button')
        if next_btn.count() > 0:
            next_btn.first.click()
            page.wait_for_timeout(3000)

    # Passwort eingeben
    pw_input = page.locator('input[type="password"]')
    try:
        pw_input.first.wait_for(state="visible", timeout=10000)
        pw_input.first.fill(password)
        page.wait_for_timeout(500)
        next_btn = page.locator('button:has-text("Next"), button:has-text("Weiter"), #passwordNext button')
        if next_btn.count() > 0:
            next_btn.first.click()
            page.wait_for_timeout(5000)
    except PlaywrightTimeout:
        print("  Passwort-Feld nicht sichtbar")
        return False

    # 2FA-Check
    if "challenge" in page.url or "signin/v2/challenge" in page.url:
        print("  Google 2FA erforderlich!")
        print("  -> Bitte im Browser loesen. Warte max. 120s ...")
        try:
            page.wait_for_url(
                lambda u: "challenge" not in u and "signin" not in u,
                timeout=LOGIN_TIMEOUT,
            )
        except PlaywrightTimeout:
            print("  Google Login Timeout")
            return False

    if "signin" in page.url:
        print("  Google Login fehlgeschlagen")
        return False

    print("  Google Login erfolgreich")
    return True


def _find_payments_iframe(page):
    """Findet das Google Payments iframe."""
    for frame in page.frames:
        if "payments.google.com" in frame.url and "timelineview" in frame.url:
            return frame
    return None


def _fetch_invoice_pdf(iframe, data_url: str) -> bytes | None:
    """Lädt eine Google-Rechnung als PDF-Bytes via JS fetch im iframe-Kontext."""
    escaped = data_url.replace("'", "\\'")
    b64_data = iframe.evaluate(f"""async () => {{
        try {{
            const resp = await fetch('{escaped}', {{ credentials: 'include' }});
            if (!resp.ok) return null;
            const buf = await resp.arrayBuffer();
            const bytes = new Uint8Array(buf);
            let binary = '';
            for (let i = 0; i < bytes.length; i++) {{
                binary += String.fromCharCode(bytes[i]);
            }}
            return btoa(binary);
        }} catch(e) {{
            return null;
        }}
    }}""")
    if not b64_data:
        return None
    return base64.b64decode(b64_data)


def download_google_invoices(page, entries: list[dict], download_dir: Path) -> list[tuple[dict, Path]]:
    """Lädt Google-Zahlungsbelege.

    Returns:
        Liste von (entry, filepath) Tupeln.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    google_entries = [
        e for e in entries
        if not e.get("is_credit")
        and any(k in e.get("vendor", "").upper() for k in ["GOOGLE", "YOUTUBE"])
        and "WL*GOOGLE" not in e.get("vendor", "").upper()
    ]
    if not google_entries:
        return []

    print(f"\n  Google Payments: Suche {len(google_entries)} Beleg(e) ...")

    page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
    page.wait_for_timeout(10000)

    # Auth-Check + Auto-Login
    if "accounts.google.com" in page.url or "signin" in page.url:
        if GOOGLE_EMAIL and GOOGLE_PASSWORD:
            if not _login_google(page, GOOGLE_EMAIL, GOOGLE_PASSWORD):
                return []
            page.goto(ACTIVITY_URL, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(10000)
        else:
            print("  Google: Nicht eingeloggt und keine Credentials konfiguriert")
            print("  -> GOOGLE_EMAIL/GOOGLE_PASSWORD setzen oder op://Private/Google konfigurieren")
            return []

    # iframe finden
    iframe = _find_payments_iframe(page)
    if not iframe:
        print("  Google: Nicht eingeloggt oder keine Transaktionen (kein payments iframe)")
        return []

    text = iframe.evaluate("() => document.body ? document.body.innerText : ''")
    if '\u20ac' not in text and 'YouTube' not in text:
        print("  Keine Transaktionen sichtbar")
        return []

    print("  Transaktionen geladen")
    results = []

    for entry in google_entries:
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        amount_str = f"{amount:.2f}".replace(".", ",")

        print(f"  {vendor}  {amount:.2f} EUR  ({date_str})")

        # Transaktion im iframe anklicken (TR.clickable mit role="row")
        rows = iframe.locator('tr.clickable[role="row"]')
        clicked = False
        for i in range(rows.count()):
            row_text = rows.nth(i).text_content() or ""
            if (amount_str in row_text or f"\u2212{amount_str}" in row_text) and "\u20ac" in row_text:
                rows.nth(i).click()
                clicked = True
                break

        if not clicked:
            print(f"  Betrag {amount_str} EUR nicht gefunden")
            continue

        page.wait_for_timeout(4000)

        # Prüfen ob Transaktionsdetails sichtbar
        has_details = iframe.evaluate("() => document.body.innerText.includes('Transaktionsdetails')")
        if not has_details:
            print(f"  Transaktionsdetails nicht geöffnet")
            continue

        # Download-URL aus dem Widget-Button extrahieren
        data_url = iframe.evaluate("""() => {
            const w = document.querySelector('.b3id-widget-button[data-url*="/doc/"]');
            return w ? w.getAttribute('data-url') : null;
        }""")

        if not data_url:
            print(f"  Kein Rechnungs-Download verfügbar")
            # Detail-Panel schliessen
            iframe.evaluate("""() => {
                const close = document.querySelector('[aria-label="Schließen"], [aria-label="Close"]');
                if (close) close.click();
            }""")
            page.wait_for_timeout(1000)
            continue

        # PDF herunterladen via JS fetch (im iframe-Kontext mit Google Auth-Cookies)
        pdf_bytes = _fetch_invoice_pdf(iframe, data_url)

        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        vendor_short = re.sub(r"[^\w]", "", vendor)[:20]
        fname = f"{date_prefix}{vendor_short}_Google_Rechnung.pdf"
        save_path = download_dir / fname

        if pdf_bytes and len(pdf_bytes) > 500 and pdf_bytes[:5] == b"%PDF-":
            save_path.write_bytes(pdf_bytes)
            results.append((entry, save_path))
            print(f"  -> {fname} ({len(pdf_bytes) / 1024:.1f} KB)")
        else:
            print(f"  Rechnung-Download fehlgeschlagen")

        # Detail-Panel schliessen
        iframe.evaluate("""() => {
            const close = document.querySelector('[aria-label="Schließen"], [aria-label="Close"]');
            if (close) close.click();
        }""")
        page.wait_for_timeout(2000)
        time.sleep(1)

    if results:
        print(f"  {len(results)} Google-Rechnung(en) heruntergeladen")
    return results
