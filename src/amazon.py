"""Amazon.de Rechnungs-Download per Playwright.

Klickt auf "Rechnung" Popover pro Bestellung und lädt die Invoice-PDF herunter.
"""

import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout


ORDERS_URL = "https://www.amazon.de/your-orders/orders?timeFilter=months-3"


def _login_amazon(page, email: str, password: str) -> bool:
    """Login bei Amazon.de (mit 2FA-Unterstützung)."""
    print("  🔑 Amazon Login ...")

    email_input = page.locator('input[name="email"], input#ap_email')
    if email_input.count() > 0:
        email_input.first.fill(email)
        continue_btn = page.locator('input#continue, span#continue')
        if continue_btn.count() > 0:
            continue_btn.first.click()
            page.wait_for_timeout(2000)

    pw_input = page.locator('input[name="password"], input#ap_password')
    if pw_input.count() > 0:
        pw_input.first.fill(password)
        submit_btn = page.locator('input#signInSubmit, input[type="submit"]')
        if submit_btn.count() > 0:
            submit_btn.first.click()
            page.wait_for_timeout(3000)

    if "ap/cvf" in page.url or "ap/mfa" in page.url:
        print("  📱 Amazon 2FA/CAPTCHA erforderlich!")
        print("  → Bitte im Browser lösen. Warte max. 120s ...")
        try:
            page.wait_for_url(
                lambda u: "your-orders" in u or "gp/css" in u or "amazon.de/?ref" in u,
                timeout=120000,
            )
        except PlaywrightTimeout:
            print("  ❌ Amazon Login Timeout")
            return False

    if "ap/signin" in page.url:
        print("  ❌ Amazon Login fehlgeschlagen")
        return False

    print("  ✅ Amazon Login erfolgreich")
    return True


def _get_order_invoice_pdf(page, order_id: str) -> str | None:
    """Klickt den Rechnung-Popover für eine Bestellung und extrahiert den PDF-Link."""
    # Rechnung-Popover-Link für diese Bestellung finden
    popover_link = page.locator(f'a[href*="invoice/popover?orderId={order_id}"]')
    if popover_link.count() == 0:
        return None

    popover_link.first.click()
    page.wait_for_timeout(2000)

    # Im Popover nach dem direkten PDF-Download-Link suchen
    pdf_link = page.locator('a[href*="/documents/download/"][href*="invoice.pdf"]')
    if pdf_link.count() > 0:
        return pdf_link.first.get_attribute("href")

    # Fallback: print.html Link
    print_link = page.locator('a[href*="/gp/css/summary/print.html"]')
    if print_link.count() > 0:
        return print_link.first.get_attribute("href")

    # Popover schließen
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)
    return None


def _collect_orders(page) -> list[dict]:
    """Sammelt alle Bestellungen mit Order-ID und Betrag von der Übersicht."""
    orders = []

    # Alle Rechnung-Popover-Links → enthalten Order-IDs
    popover_links = page.locator('a[href*="invoice/popover?orderId="]')
    count = popover_links.count()

    for i in range(count):
        try:
            href = popover_links.nth(i).get_attribute("href") or ""
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            order_id = params.get("orderId", [""])[0]
            if not order_id or any(o["order_id"] == order_id for o in orders):
                continue
            orders.append({"order_id": order_id})
        except Exception:
            continue

    return orders


def download_amazon_invoices(
    page,
    entries: list[dict],
    download_dir: Path,
    email: str,
    password: str,
) -> list[Path]:
    """Lädt Amazon.de Rechnungen für MC-Einträge herunter."""
    download_dir.mkdir(parents=True, exist_ok=True)

    amazon_entries = [
        e for e in entries
        if not e.get("is_credit")
        and ("AMZN" in e.get("vendor", "").upper() or "AMAZON" in e.get("vendor", "").upper())
    ]
    if not amazon_entries:
        return []

    print(f"\n🛒 Amazon.de: Suche {len(amazon_entries)} Rechnung(en) ...")

    page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    if "ap/signin" in page.url or "ap/cvf" in page.url:
        if not _login_amazon(page, email, password):
            return []
        page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

    orders = _collect_orders(page)
    print(f"  📋 {len(orders)} Bestellungen gefunden")

    if not orders:
        print("  ⚠️  Keine Bestellungen auf der Übersicht")
        return []

    downloaded = []

    for idx, entry in enumerate(amazon_entries, 1):
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        print(f"  [{idx}/{len(amazon_entries)}] {vendor}  {amount:.2f} EUR  ({date_str})")

        # Für jede Bestellung den Popover öffnen und PDF-Link suchen
        for order in orders:
            oid = order.get("order_id")
            if order.get("_used"):
                continue

            pdf_url = _get_order_invoice_pdf(page, oid)
            if not pdf_url:
                continue

            # PDF herunterladen
            if "/documents/download/" in pdf_url:
                # Direkter PDF-Download per HTTP (nicht über den Browser)
                try:
                    full_url = f"https://www.amazon.de{pdf_url}" if pdf_url.startswith("/") else pdf_url
                    # Cookies aus dem Browser übernehmen
                    cookies = page.context.cookies("https://www.amazon.de")
                    cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

                    import requests as http_req
                    resp = http_req.get(full_url, headers={"Cookie": cookie_str}, timeout=30)
                    if resp.status_code == 200 and len(resp.content) > 1000:
                        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                        fname = f"{date_prefix}Amazon_{oid}_invoice.pdf"
                        save_path = download_dir / fname
                        save_path.write_bytes(resp.content)
                        downloaded.append(save_path)
                        order["_used"] = True
                        print(f"       ✅ {fname} ({len(resp.content) / 1024:.1f} KB)")
                        # Popover schließen
                        page.keyboard.press("Escape")
                        page.wait_for_timeout(500)
                        break
                    else:
                        print(f"       ⚠️  HTTP {resp.status_code} für {oid} ({len(resp.content)} bytes)")
                except Exception as e:
                    print(f"       ⚠️  Download fehlgeschlagen für {oid}: {e}")
            elif "print.html" in pdf_url:
                # Print-Seite als PDF speichern
                try:
                    invoice_page = page.context.new_page()
                    invoice_page.goto(f"https://www.amazon.de{pdf_url}", wait_until="domcontentloaded", timeout=30000)
                    invoice_page.wait_for_timeout(3000)

                    date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                    fname = f"{date_prefix}Amazon_{oid}.pdf"
                    save_path = download_dir / fname
                    invoice_page.pdf(path=str(save_path), format="A4", print_background=True)
                    invoice_page.close()

                    if save_path.stat().st_size > 1000:
                        downloaded.append(save_path)
                        order["_used"] = True
                        print(f"       ✅ {fname} ({save_path.stat().st_size / 1024:.1f} KB)")
                        break
                    else:
                        save_path.unlink(missing_ok=True)
                except Exception as e:
                    print(f"       ⚠️  Fehler: {e}")

            # Popover schließen
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)

        else:
            print(f"       ⚠️  Keine passende Rechnung gefunden")

        time.sleep(1)

    print(f"  📦 {len(downloaded)} Amazon-Rechnung(en) heruntergeladen")
    return downloaded
