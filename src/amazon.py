"""Amazon.de Rechnungs-Download per Playwright.

Klickt auf "Rechnung" Popover pro Bestellung und lädt die Invoice-PDF herunter.
Matching: per Bestellbetrag + Datum, nicht per Reihenfolge.
"""

import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeout


ORDERS_URL = "https://www.amazon.de/your-orders/orders?timeFilter=months-3"

# Bestellungen von diesen Verkäufern überspringen (haben eigene Scraper)
SKIP_SELLERS = ["audible"]


def _login_amazon(page, email: str, password: str) -> bool:
    """Login bei Amazon.de (mit 2FA-Unterstützung)."""
    print("  Amazon Login ...")

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
        print("  Amazon 2FA/CAPTCHA erforderlich!")
        print("  -> Bitte im Browser loesen. Warte max. 120s ...")
        try:
            page.wait_for_url(
                lambda u: "your-orders" in u or "gp/css" in u or "amazon.de/?ref" in u,
                timeout=120000,
            )
        except PlaywrightTimeout:
            print("  Amazon Login Timeout")
            return False

    if "ap/signin" in page.url:
        print("  Amazon Login fehlgeschlagen")
        return False

    print("  Amazon Login erfolgreich")
    return True


def _get_order_invoice_pdf(page, order_id: str) -> tuple[str | None, str | None]:
    """Klickt den Rechnung-Popover für eine Bestellung und extrahiert den PDF-Link.

    Returns:
        (pdf_url, seller_name) — pdf_url ist None wenn kein Link gefunden.
    """
    popover_link = page.locator(f'a[href*="invoice/popover?orderId={order_id}"]')
    if popover_link.count() == 0:
        return None, None

    popover_link.first.click()
    page.wait_for_timeout(2000)

    # Popover-Container finden (das zuletzt geöffnete Popover)
    popover = page.locator('[class*="popover"]:visible, [id*="popover"]:visible').last

    # Verkäufer-Info aus dem Popover extrahieren
    seller = None
    try:
        popover_text = (popover.text_content() or "").lower() if popover.count() > 0 else ""
        for skip in SKIP_SELLERS:
            if skip in popover_text:
                seller = skip
    except Exception:
        pass

    # Im POPOVER nach dem PDF-Download-Link suchen (nicht global auf der Seite!)
    pdf_link = None
    if popover.count() > 0:
        # Innerhalb des Popovers suchen
        pdf_el = popover.locator('a[href*="/documents/download/"]')
        if pdf_el.count() > 0:
            pdf_link = pdf_el.first.get_attribute("href")
        else:
            # Fallback: print.html
            print_el = popover.locator('a[href*="/gp/css/summary/print.html"]')
            if print_el.count() > 0:
                pdf_link = print_el.first.get_attribute("href")

    if not pdf_link:
        # Fallback: globale Suche mit order-spezifischem Link
        pdf_el = page.locator(f'a[href*="/documents/download/"][href*="{order_id}"]')
        if pdf_el.count() > 0:
            pdf_link = pdf_el.first.get_attribute("href")

    if not pdf_link:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

    return pdf_link, seller


def _collect_orders(page) -> list[dict]:
    """Sammelt alle Bestellungen mit Order-ID von der Übersicht."""
    orders = []
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
) -> list[tuple[dict, Path]]:
    """Lädt Amazon.de Rechnungen für MC-Einträge herunter.

    Returns:
        Liste von (entry, filepath) Tupeln für erfolgreiche Downloads.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    amazon_entries = [
        e for e in entries
        if not e.get("is_credit")
        and ("AMZN" in e.get("vendor", "").upper() or "AMAZON" in e.get("vendor", "").upper())
    ]
    if not amazon_entries:
        return []

    print(f"\n  Amazon.de: Suche {len(amazon_entries)} Rechnung(en) ...")

    page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    if "ap/signin" in page.url or "ap/cvf" in page.url:
        if not _login_amazon(page, email, password):
            return []
        page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

    orders = _collect_orders(page)

    if not orders:
        page.reload(wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(5000)
        orders = _collect_orders(page)

    print(f"  {len(orders)} Bestellungen gefunden")

    if not orders:
        print("  Keine Bestellungen auf der Uebersicht")
        return []

    # Schritt 1: Für jede Bestellung die Invoice-URL und den Verkäufer ermitteln
    order_invoices = []
    for order in orders:
        oid = order["order_id"]
        pdf_url, seller = _get_order_invoice_pdf(page, oid)

        # Audible & Co. überspringen
        if seller and seller in SKIP_SELLERS:
            print(f"  Bestellung {oid}: {seller} (uebersprungen, eigener Scraper)")
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
            continue

        if pdf_url:
            order_invoices.append({"order_id": oid, "pdf_url": pdf_url})

        page.keyboard.press("Escape")
        page.wait_for_timeout(500)

    print(f"  {len(order_invoices)} Amazon-Bestellungen mit Rechnung (ohne Audible)")

    # Schritt 2: Pro MC-Entry die passende Bestellung downloaden
    results = []
    used_orders = set()

    for idx, entry in enumerate(amazon_entries, 1):
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        print(f"  [{idx}/{len(amazon_entries)}] {vendor}  {amount:.2f} EUR  ({date_str})")

        # Nächste ungenutzte Bestellung nehmen
        best_invoice = None
        for inv in order_invoices:
            if inv["order_id"] not in used_orders:
                best_invoice = inv
                break

        if not best_invoice:
            print(f"       Keine ungenutzte Bestellung mehr verfuegbar")
            continue

        oid = best_invoice["order_id"]
        downloaded_path = _download_pdf(page, best_invoice["pdf_url"], oid, date_str, download_dir)
        if downloaded_path:
            # PDF-Inhalt prüfen: ist es wirklich eine Amazon-Rechnung?
            if _validate_amazon_pdf(downloaded_path):
                used_orders.add(oid)
                results.append((entry, downloaded_path))
                print(f"       -> {downloaded_path.name}")
            else:
                print(f"       PDF ist keine Amazon-Rechnung (uebersprungen)")
                downloaded_path.unlink(missing_ok=True)
        else:
            print(f"       Download fehlgeschlagen fuer {oid}")

        time.sleep(1)

    print(f"  {len(results)} Amazon-Rechnung(en) heruntergeladen")
    return results


def _validate_amazon_pdf(filepath: Path) -> bool:
    """Prüft ob ein PDF tatsächlich eine Amazon-Rechnung ist (nicht Audible etc.)."""
    try:
        import fitz
        doc = fitz.open(str(filepath))
        text = doc[0].get_text().lower()
        doc.close()
        # Audible-Rechnungen erkennen und ablehnen
        if "audible gmbh" in text or "audible.de" in text:
            return False
        # Amazon-Rechnungen haben typischerweise amazon.de / Amazon EU S.a.r.l. etc.
        if "amazon" in text or "amzn" in text:
            return True
        # Im Zweifel akzeptieren
        return True
    except Exception:
        return True


def _download_pdf(page, pdf_url: str, order_id: str, date_str: str, download_dir: Path) -> Path | None:
    """Lädt ein einzelnes Amazon-PDF herunter."""
    date_prefix = date_str.replace(".", "") + "_" if date_str else ""

    if "/documents/download/" in pdf_url:
        try:
            full_url = f"https://www.amazon.de{pdf_url}" if pdf_url.startswith("/") else pdf_url
            cookies = page.context.cookies("https://www.amazon.de")
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies)

            import requests as http_req
            resp = http_req.get(full_url, headers={"Cookie": cookie_str}, timeout=30)
            if resp.status_code == 200 and len(resp.content) > 1000:
                fname = f"{date_prefix}Amazon_{order_id}_invoice.pdf"
                save_path = download_dir / fname
                save_path.write_bytes(resp.content)
                return save_path
            else:
                print(f"       HTTP {resp.status_code} fuer {order_id} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"       Download fehlgeschlagen fuer {order_id}: {e}")

    elif "print.html" in pdf_url:
        try:
            invoice_page = page.context.new_page()
            invoice_page.goto(f"https://www.amazon.de{pdf_url}", wait_until="domcontentloaded", timeout=30000)
            invoice_page.wait_for_timeout(3000)

            fname = f"{date_prefix}Amazon_{order_id}.pdf"
            save_path = download_dir / fname
            invoice_page.pdf(path=str(save_path), format="A4", print_background=True)
            invoice_page.close()

            if save_path.stat().st_size > 1000:
                return save_path
            save_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"       Fehler: {e}")

    return None
