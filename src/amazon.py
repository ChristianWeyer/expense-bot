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
        print("  → Bitte im Browser loesen. Warte max. 120s ...")
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


def _extract_all_order_amounts(page) -> dict[str, float]:
    """Extrahiert alle Bestellbetraege von der Uebersichtsseite in einem Rutsch."""
    try:
        raw = page.evaluate("""() => {
            const cards = document.querySelectorAll('.order-card');
            const results = {};
            for (const card of cards) {
                const oidEl = card.querySelector('.yohtmlc-order-id span[dir="ltr"]');
                if (!oidEl) continue;
                const oid = oidEl.textContent.trim();
                const items = card.querySelectorAll('.order-header__header-list-item');
                for (const item of items) {
                    const label = item.querySelector('.a-color-secondary.a-text-caps');
                    if (label && label.textContent.trim() === 'Summe') {
                        const amtEl = item.querySelector('.a-size-base.a-color-secondary');
                        if (amtEl) results[oid] = amtEl.textContent.trim();
                    }
                }
            }
            return results;
        }""")
        amounts = {}
        for oid, amt_str in raw.items():
            # Parse "43,90 €" or "43,90\xa0€"
            clean = amt_str.replace("€", "").replace("\xa0", "").replace(" ", "").replace(".", "").replace(",", ".").strip()
            try:
                amounts[oid] = float(clean)
            except ValueError:
                pass
        return amounts
    except Exception:
        return {}


def _get_order_invoice_pdf(page, order_id: str) -> tuple[str | None, str | None]:
    """Klickt den Rechnung-Popover fuer eine Bestellung und extrahiert den PDF-Link.

    Amazon-Popovers laden den Inhalt als globale Seitenelemente (nicht in einem
    Container). Nach dem Klick suchen wir nach order-spezifischen Links.

    Returns:
        (pdf_url, seller_name) — pdf_url ist None wenn kein Link gefunden.
    """
    popover_link = page.locator(f'a[href*="invoice/popover?orderId={order_id}"]')
    if popover_link.count() == 0:
        return None, None

    popover_link.first.click()
    page.wait_for_timeout(2000)

    # Pruefen ob es Audible ist: der documents/download Link enthaelt den Order-ID nicht,
    # aber der print.html Link schon. Audible hat einen /documents/download/ Link.
    seller = None

    # Finde den zuletzt geoeffneten Popover (hat die hoechste z-index / ist sichtbar)
    popover = page.locator('.a-popover:visible .invoice-list').last

    if popover.count() > 0:
        # 1. Direct invoice PDF links innerhalb des Popovers
        doc_link = popover.locator('a[href*="/documents/download/"]')
        if doc_link.count() > 0:
            href = doc_link.first.get_attribute("href") or ""
            return href, None

        # 2. Fallback: print.html Link innerhalb des Popovers
        print_link = popover.locator(f'a[href*="print.html"]')
        if print_link.count() > 0:
            href = print_link.first.get_attribute("href") or ""
            return href, None

    return None, seller


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


def _filter_amazon_entries(entries: list[dict]) -> list[dict]:
    """Filtert Amazon-Einträge aus MC-Entries."""
    return [
        e for e in entries
        if not e.get("is_credit")
        and ("AMZN" in e.get("vendor", "").upper() or "AMAZON" in e.get("vendor", "").upper())
    ]


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

    amazon_entries = _filter_amazon_entries(entries)
    if not amazon_entries:
        return []

    print(f"\n  🔍 Amazon.de: Suche {len(amazon_entries)} Rechnung(en) ...")

    page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    if "ap/signin" in page.url or "ap/cvf" in page.url:
        if not _login_amazon(page, email, password):
            return []
        page.goto(ORDERS_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)

    # Sammle Bestellungen mit Invoice-URLs ueber alle Seiten
    order_invoices = []
    unmatched_amounts = {round(e.get("amount", 0), 2) for e in amazon_entries}
    max_pages = 5
    total_orders = 0

    for page_num in range(max_pages):
        page_orders = _collect_orders(page)
        if not page_orders and page_num == 0:
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(5000)
            page_orders = _collect_orders(page)

        page_amounts = _extract_all_order_amounts(page)
        total_orders += len(page_orders)

        # Pro Bestellung auf DIESER Seite: Popover klicken und Invoice-URL holen
        for order in page_orders:
            oid = order["order_id"]
            # Bereits erfasst?
            if any(inv["order_id"] == oid for inv in order_invoices):
                continue

            pdf_url, seller = _get_order_invoice_pdf(page, oid)

            if seller and seller in SKIP_SELLERS:
                page.keyboard.press("Escape")
                page.wait_for_timeout(300)
                continue

            if pdf_url:
                amt = page_amounts.get(oid)
                order_invoices.append({"order_id": oid, "pdf_url": pdf_url, "amount": amt})

            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

        # Pruefen ob alle MC-Betraege gefunden wurden
        for amt in page_amounts.values():
            unmatched_amounts.discard(round(amt, 2))

        if not unmatched_amounts:
            break

        # Naechste Seite
        next_btn = page.locator('.a-pagination .a-last a')
        if next_btn.count() == 0 or page.locator('.a-pagination .a-last.a-disabled').count() > 0:
            break
        next_btn.first.click()
        page.wait_for_timeout(3000)

    print(f"  ✅ {total_orders} Bestellungen, {len(order_invoices)} mit Rechnung ({page_num + 1} Seite(n))")

    if not order_invoices:
        print("  ⚠️ Keine Bestellungen mit Rechnung gefunden")
        return []

    # Schritt 2: Pro MC-Entry die PASSENDE Bestellung per Betrag finden und downloaden
    results = []
    used_orders = set()

    for idx, entry in enumerate(amazon_entries, 1):
        amount = entry.get("amount", 0)
        date_str = entry.get("date", "")
        vendor = entry.get("vendor", "?")
        print(f"  [{idx}/{len(amazon_entries)}] {vendor}  {amount:.2f} EUR  ({date_str})")

        # Match per Betrag (bester Treffer)
        best_invoice = None
        best_diff = float('inf')
        for inv in order_invoices:
            if inv["order_id"] in used_orders:
                continue
            if inv["amount"] is not None:
                diff = abs(inv["amount"] - amount)
                if diff < best_diff:
                    best_diff = diff
                    best_invoice = inv

        # Wenn kein Betrags-Match <= 1 EUR: Fallback auf naechste ungenutzte
        if best_invoice is None or best_diff > 1.0:
            for inv in order_invoices:
                if inv["order_id"] not in used_orders:
                    best_invoice = inv
                    break

        if not best_invoice:
            print(f"       ⚠️ Keine passende Bestellung gefunden")
            continue

        oid = best_invoice["order_id"]
        match_info = f"(Diff: {best_diff:.2f})" if best_diff < float('inf') else "(kein Betrags-Match)"
        downloaded_path = _download_pdf(page, best_invoice["pdf_url"], oid, date_str, download_dir)
        if downloaded_path:
            if _validate_amazon_pdf(downloaded_path):
                used_orders.add(oid)
                results.append((entry, downloaded_path))
                print(f"       📎 {downloaded_path.name} {match_info}")
            else:
                print(f"       ⚠️ PDF zu klein/kaputt (uebersprungen)")
                downloaded_path.unlink(missing_ok=True)
        else:
            print(f"       ❌ Download fehlgeschlagen fuer {oid}")

        time.sleep(1)

    print(f"  ✅ {len(results)} Amazon-Rechnung(en) heruntergeladen")
    return results


def _validate_amazon_pdf(filepath: Path) -> bool:
    """Prüft ob ein PDF eine Rechnung ist. Akzeptiert alles ausser leere/kaputte PDFs."""
    try:
        if filepath.stat().st_size < 500:
            return False
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
                print(f"       ❌ HTTP {resp.status_code} beim Download fuer {order_id} ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"       ❌ Download fehlgeschlagen fuer {order_id}: {e}")

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
