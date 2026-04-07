#!/usr/bin/env python3
"""
Expense Bot
===========
Orchestrator: Sammelt DB-Rechnungen, Outlook-Belege und Amazon-Rechnungen
aus einer Mastercard-Abrechnung und versendet alles per Email.

Nutzung:
    python expense_bot.py --mc-pdf abrechnung.pdf           # Alles automatisch
    python expense_bot.py --mc-pdf abrechnung.pdf --dry-run  # Ohne Email-Versand
    python expense_bot.py --headed                           # Browser sichtbar
"""

import argparse
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

from src.config import (
    BAHN_EMAIL, BAHN_PASSWORD, RECIPIENT_EMAIL, AZURE_CLIENT_ID,
    AMAZON_EMAIL, AMAZON_PASSWORD,
    CC_EMAIL, CDP_URL, MC_PDF, KEEP_DAYS,
    DOWNLOAD_DIR, BELEGE_DIR, BROWSER_DATA_DIR,
)
from src.timer import Timer
from src.history import cleanup_old_invoices
from src.auth import get_graph_token
from src.bahn import login, download_invoices
from src.mail import send_email


def main():
    parser = argparse.ArgumentParser(description="Expense Bot")
    parser.add_argument("--all", action="store_true", help="Alle neuen Rechnungen herunterladen")
    parser.add_argument("--dry-run", action="store_true", help="Rechnungen laden, aber nicht per Email senden")
    parser.add_argument("--headed", action="store_true", help="Browser sichtbar starten (zum Debuggen)")
    parser.add_argument("--mc-pdf", type=str, metavar="DATEI_ODER_ORDNER", default=MC_PDF,
                        help="Mastercard-PDF oder Ordner mit PDFs (neuestes wird genutzt)")
    parser.add_argument("--cc", type=str, metavar="EMAIL", default=CC_EMAIL,
                        help="CC-Empfänger für die Email")
    parser.add_argument("--login-only", action="store_true",
                        help="Nur einloggen (inkl. 2FA) und Session speichern")
    parser.add_argument("--cdp", type=str, metavar="URL", nargs="?", const=CDP_URL or "http://localhost:9222",
                        default=CDP_URL,
                        help="An laufenden Chrome (Canary) anhängen (CDP)")
    args = parser.parse_args()

    timer = Timer()

    # Pflichtfelder validieren
    missing = [name for name, val in [
        ("BAHN_EMAIL", BAHN_EMAIL), ("BAHN_PASSWORD", BAHN_PASSWORD),
        ("RECIPIENT_EMAIL", RECIPIENT_EMAIL), ("AZURE_CLIENT_ID", AZURE_CLIENT_ID),
    ] if not val]
    if missing:
        print(f"❌ Fehlende Umgebungsvariablen: {', '.join(missing)}")
        sys.exit(1)

    print("=" * 50)
    print("💼 Expense Bot")
    print("=" * 50)

    # ── Mastercard-PDF parsen ──
    booking_refs = None
    mc_pdf_name = None
    non_db_entries = []
    receipt_files = []
    unmatched_entries = []
    link_only_entries = []

    if args.mc_pdf:
        from src.mastercard import extract_all_entries, get_db_entries, get_non_db_entries, print_summary

        mc_path = Path(args.mc_pdf)

        if mc_path.is_dir():
            pdf_files = sorted(
                [p for p in mc_path.iterdir() if p.suffix.lower() == ".pdf"],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if not pdf_files:
                print(f"⚠️  Keine PDF-Dateien im Ordner: {mc_path}")
                return
            mc_path = pdf_files[0]
            print(f"\n📂 Ordner: {args.mc_pdf}")
            print(f"   Neuestes PDF: {mc_path.name}")
            if len(pdf_files) > 1:
                print(f"   ({len(pdf_files)} PDFs im Ordner, nutze das neueste)")

        mc_pdf_name = mc_path.name
        print(f"\n💳 Lese Mastercard-PDF: {mc_path}")

        all_entries = extract_all_entries(str(mc_path))
        db_entries = get_db_entries(all_entries)
        non_db_entries = get_non_db_entries(all_entries)
        net = print_summary(db_entries, "DB-Buchungen")
        booking_refs = [b["booking_ref"] for b in net if b.get("booking_ref")]

        timer.lap("PDF-Parsing")
        if not booking_refs:
            print("⚠️  Keine DB-Buchungsnummern im PDF gefunden.")
            print("    Falle zurück auf 'Meine Reisen'-Modus ...")

    # ── Belege aus Outlook holen ──
    if non_db_entries:
        from src.outlook import match_and_download_receipts
        token = get_graph_token()
        receipt_results = match_and_download_receipts(token, non_db_entries, BELEGE_DIR)
        receipt_files = receipt_results.get("downloaded_files", [])
        unmatched_entries = receipt_results.get("unmatched", [])
        link_only_entries = [m for m in receipt_results.get("matched", [])
                            if m.get("receipt_url") and not m.get("files")]
        timer.lap(f"Belege ({len(receipt_files)} PDFs)")

    # ── Browser-Automation (bahn.de + Amazon) ──
    with sync_playwright() as p:
        use_cdp = False
        browser = None

        if args.cdp:
            cdp_url = args.cdp
            print(f"\n🔗 Verbinde mit Chrome über CDP: {cdp_url}")
            try:
                browser = p.chromium.connect_over_cdp(cdp_url)
                use_cdp = True
                timer.lap("Chrome-Verbindung (CDP)")
            except Exception:
                print("   ⚠️  CDP nicht erreichbar – starte eigenen headless Browser ...")
                use_cdp = False

        if use_cdp and browser:
            context = browser.contexts[0] if browser.contexts else browser.new_context(
                accept_downloads=True, locale="de-DE",
            )
            page = context.new_page()

            try:
                login(page, timer)

                if args.login_only:
                    print("\n✅ Login erfolgreich! (CDP – Session ist im Chrome gespeichert)")
                    print(f"\n✨ Fertig! Gesamtzeit: {timer.elapsed()}")
                    return

                files, failed = download_invoices(page, timer, download_all=args.all, booking_refs=booking_refs)
                _download_amazon(context, unmatched_entries, receipt_files, timer)
                _download_portals(page, unmatched_entries, receipt_files, timer)

                total = len(booking_refs) if booking_refs else None
                send_email(files + receipt_files, timer, dry_run=args.dry_run, cc_email=args.cc,
                           mc_pdf_name=mc_pdf_name, failed_refs=failed, total_refs=total,
                           unmatched_entries=unmatched_entries, link_only_entries=link_only_entries)
            finally:
                page.close()
                browser.close()

        else:
            BROWSER_DATA_DIR.mkdir(exist_ok=True)
            print("\n🌐 Starte headless Browser ...")
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(BROWSER_DATA_DIR),
                headless=not args.headed,
                accept_downloads=True,
                locale="de-DE",
            )
            page = context.new_page()

            try:
                login(page, timer)

                if args.login_only:
                    print("\n✅ Login erfolgreich!")
                    print("   Session wird in .browser-data/ gespeichert.")
                    print("\n   Drücke ENTER um den Browser zu schließen ...")
                    input()
                    print("   Session gespeichert. Beim nächsten Mal sollte kein 2FA nötig sein.")
                    context.close()
                    print(f"\n✨ Fertig! Gesamtzeit: {timer.elapsed()}")
                    return

                files, failed = download_invoices(page, timer, download_all=args.all, booking_refs=booking_refs)
                _download_amazon(context, unmatched_entries, receipt_files, timer)
                _download_portals(page, unmatched_entries, receipt_files, timer)

                total = len(booking_refs) if booking_refs else None
                send_email(files + receipt_files, timer, dry_run=args.dry_run, cc_email=args.cc,
                           mc_pdf_name=mc_pdf_name, failed_refs=failed, total_refs=total,
                           unmatched_entries=unmatched_entries, link_only_entries=link_only_entries)
            finally:
                context.close()

    cleanup_old_invoices(KEEP_DAYS)
    print(f"\n✨ Fertig! Gesamtzeit: {timer.elapsed()}")


def _download_amazon(context, unmatched_entries: list, receipt_files: list, timer: Timer):
    """Amazon.de Rechnungen herunterladen (falls Credentials vorhanden)."""
    if not (AMAZON_EMAIL and AMAZON_PASSWORD and unmatched_entries):
        return
    from src.amazon import download_amazon_invoices
    amazon_page = context.new_page()
    amazon_files = download_amazon_invoices(
        amazon_page, unmatched_entries, BELEGE_DIR, AMAZON_EMAIL, AMAZON_PASSWORD)
    receipt_files.extend(amazon_files)
    amazon_page.close()
    if amazon_files:
        timer.lap(f"Amazon ({len(amazon_files)} Rechnungen)")


def _download_portals(page, unmatched_entries: list, receipt_files: list, timer: Timer):
    """Rechnungen von Vendor-Portalen herunterladen (über CDP)."""
    if not unmatched_entries:
        return
    from src.portal import download_portal_invoices
    portal_files = download_portal_invoices(page, unmatched_entries, BELEGE_DIR)
    receipt_files.extend(portal_files)
    if portal_files:
        timer.lap(f"Portale ({len(portal_files)} Rechnungen)")


if __name__ == "__main__":
    main()
