"""Stripe Billing Portal Scraper — für Vendor die Stripe nutzen.

Viele SaaS-Vendor (Figma, Perplexity, etc.) leiten auf einen Stripe Billing
Portal weiter wo alle Invoices mit PDF-Download verfügbar sind.

Ablauf:
1. Vendor-Billing-Seite laden
2. "Manage Plan" / "Manage Subscription" Button klicken
3. Auf Stripe-Portal-Redirect warten
4. Invoices im Stripe Portal finden und PDFs herunterladen
"""

import re
import time
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeout


def _find_and_click_manage_button(page) -> bool:
    """Sucht und klickt den 'Manage Plan/Subscription' Button."""
    manage_selectors = [
        'a:has-text("Manage")',
        'button:has-text("Manage")',
        'a:has-text("Manage Plan")',
        'a:has-text("Manage Subscription")',
        'a:has-text("Manage plan")',
        'a:has-text("Abo verwalten")',
        'a:has-text("Abonnement verwalten")',
        'button:has-text("Manage Plan")',
        'button:has-text("Manage Subscription")',
        '[data-testid*="manage"]',
    ]

    for sel in manage_selectors:
        btn = page.locator(sel)
        if btn.count() > 0:
            btn.first.click()
            return True
    return False


def _download_stripe_invoices(page, download_dir: Path, vendor_name: str, date_str: str) -> list[Path]:
    """Extrahiert und lädt Invoice-PDFs von einer Stripe Portal/Invoice Seite."""
    downloaded = []

    # Stripe Invoice-Links finden
    invoice_links = page.locator('a[href*="invoice.stripe.com"], a[data-testid="hip-link"]')
    count = invoice_links.count()

    if count == 0:
        # Vielleicht sind wir auf einer Stripe Billing Portal-Seite mit anderer Struktur
        # Suche nach "Invoice history" oder "Rechnungsverlauf" Links
        hist = page.locator('a:has-text("Invoice history"), a:has-text("Rechnungsverlauf"), a:has-text("View invoices")')
        if hist.count() > 0:
            hist.first.click()
            page.wait_for_timeout(3000)
            invoice_links = page.locator('a[href*="invoice.stripe.com"], a[data-testid="hip-link"]')
            count = invoice_links.count()

    if count == 0:
        return []

    # Jede Invoice-Seite öffnen und PDF downloaden (max 3 für den aktuellen Abrechnungszeitraum)
    for i in range(min(count, 3)):
        try:
            href = invoice_links.nth(i).get_attribute("href")
            if not href or "invoice.stripe.com" not in href:
                continue

            inv_page = page.context.new_page()
            inv_page.goto(href, wait_until="domcontentloaded", timeout=30000)
            inv_page.wait_for_timeout(5000)

            dl_btn = inv_page.locator(
                'a:has-text("Download invoice"), a:has-text("Download receipt"), '
                'button:has-text("Download invoice"), button:has-text("Download receipt")'
            )

            if dl_btn.count() > 0:
                with inv_page.expect_download(timeout=15000) as dl_info:
                    dl_btn.first.click()
                download = dl_info.value
                fname = download.suggested_filename or f"{vendor_name}_invoice.pdf"
                date_prefix = date_str.replace(".", "") + "_" if date_str else ""
                save_path = download_dir / f"{date_prefix}{fname}"
                download.save_as(str(save_path))
                downloaded.append(save_path)
                print(f"     ✅ {save_path.name} ({save_path.stat().st_size / 1024:.1f} KB)")

            inv_page.close()
        except Exception as e:
            print(f"     ⚠️  Stripe-Download fehlgeschlagen: {e}")

        time.sleep(1)

    return downloaded


def download_via_stripe_portal(
    page,
    vendor_name: str,
    billing_url: str,
    entries: list[dict],
    download_dir: Path,
) -> list[Path]:
    """
    Navigiert zur Vendor-Billing-Seite, klickt 'Manage', folgt dem Stripe-Redirect
    und lädt Invoice-PDFs herunter.
    """
    download_dir.mkdir(parents=True, exist_ok=True)

    if not entries:
        return []

    print(f"\n  📋 {vendor_name} ({len(entries)} Einträge)")

    page.goto(billing_url, wait_until="domcontentloaded", timeout=30000)
    page.wait_for_timeout(5000)

    # Prüfe ob wir schon auf einer Stripe-Seite sind
    if "stripe.com" in page.url:
        date_str = entries[0].get("date", "") if entries else ""
        return _download_stripe_invoices(page, download_dir, vendor_name, date_str)

    # "Manage" Button suchen und klicken
    if not _find_and_click_manage_button(page):
        print(f"     ⚠️  Kein 'Manage'-Button gefunden auf {billing_url}")
        return []

    # Auf Stripe-Redirect warten
    try:
        page.wait_for_url(lambda u: "stripe.com" in u or "billing.stripe.com" in u, timeout=15000)
    except PlaywrightTimeout:
        # Vielleicht öffnet es in einem neuen Tab
        pages = page.context.pages
        stripe_page = None
        for p in pages:
            if "stripe.com" in p.url:
                stripe_page = p
                break
        if stripe_page:
            page = stripe_page
        else:
            print(f"     ⚠️  Kein Stripe-Redirect nach 'Manage'-Klick")
            return []

    page.wait_for_timeout(5000)
    print(f"     → Stripe Portal: {page.url[:60]}")

    date_str = entries[0].get("date", "") if entries else ""
    return _download_stripe_invoices(page, download_dir, vendor_name, date_str)
