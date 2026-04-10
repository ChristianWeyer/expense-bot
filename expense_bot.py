#!/usr/bin/env python3
"""
Expense Bot — Orchestrator

Parst Mastercard-PDFs, sucht und lädt Belege/Rechnungen aus verschiedenen
Quellen, und versendet einen konsolidierten Report per Email.
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from playwright.sync_api import sync_playwright

import src.config as _cfg
from src.config import (
    BAHN_EMAIL, BAHN_PASSWORD, RECIPIENT_EMAIL, AZURE_CLIENT_ID,
    AMAZON_EMAIL, AMAZON_PASSWORD,
    CC_EMAIL, CDP_URL, MC_PDF, KEEP_DAYS,
    BROWSER_DATA_DIR,
    ADOBE_EMAIL, ADOBE_PASSWORD,
    HEISE_EMAIL, HEISE_PASSWORD,
    GOOGLE_EMAIL, GOOGLE_PASSWORD,
    FIGMA_EMAIL, FIGMA_PASSWORD,
    OPENAI_EMAIL, OPENAI_PASSWORD,
)
from src.timer import Timer
from src.history import cleanup_old_invoices
from src.auth import get_graph_token
from src.bahn import login, download_invoices
from src.logger import setup_logging
from src.mail import send_email
from src.result import RunResult


def _check_credentials(args):
    """Prüft Credentials beim Start und warnt bei fehlenden optionalen Zugangsdaten."""
    # Required für Email-Versand
    required = [
        ("RECIPIENT_EMAIL", RECIPIENT_EMAIL),
        ("AZURE_CLIENT_ID", AZURE_CLIENT_ID),
    ]
    missing_required = [name for name, val in required if not val]
    if missing_required:
        print(f"FEHLER: Fehlende Pflicht-Credentials: {', '.join(missing_required)}")
        sys.exit(1)

    if args.mc_pdf and not os.environ.get("OPENAI_API_KEY"):
        print("FEHLER: OPENAI_API_KEY nicht gesetzt (nötig für MC-PDF-Parsing)")
        sys.exit(1)

    # Optional — nur Warnung
    optional = [
        ("BAHN_EMAIL/BAHN_PASSWORD", BAHN_EMAIL and BAHN_PASSWORD, "Bahn-Scraper wird übersprungen"),
        ("AMAZON_EMAIL/AMAZON_PASSWORD", AMAZON_EMAIL and AMAZON_PASSWORD, "Amazon-Scraper wird übersprungen"),
        ("GOOGLE_EMAIL/GOOGLE_PASSWORD", GOOGLE_EMAIL and GOOGLE_PASSWORD, "Google-Scraper wird übersprungen"),
        ("ADOBE_EMAIL/ADOBE_PASSWORD", ADOBE_EMAIL and ADOBE_PASSWORD, "Adobe-Scraper wird übersprungen"),
        ("HEISE_EMAIL/HEISE_PASSWORD", HEISE_EMAIL and HEISE_PASSWORD, "Heise-Scraper wird übersprungen"),
        ("FIGMA_EMAIL/FIGMA_PASSWORD", FIGMA_EMAIL and FIGMA_PASSWORD, "Figma-Scraper wird übersprungen"),
        ("OPENAI_EMAIL/OPENAI_PASSWORD", OPENAI_EMAIL and OPENAI_PASSWORD, "OpenAI-Portal-Scraper wird übersprungen"),
    ]
    for name, present, hint in optional:
        if not present:
            print(f"  ⚠️  Fehlend: {name} — {hint}")


def main():
    parser = argparse.ArgumentParser(description="Expense Bot")
    parser.add_argument("--all", action="store_true", help="Alle neuen Rechnungen herunterladen")
    parser.add_argument("--dry-run", action="store_true", help="Rechnungen laden, aber nicht per Email senden")
    parser.add_argument("--headed", action="store_true", help="Browser sichtbar starten")
    parser.add_argument("--mc-pdf", type=str, metavar="DATEI_ODER_ORDNER", default=MC_PDF)
    parser.add_argument("--cc", type=str, metavar="EMAIL", default=CC_EMAIL)
    parser.add_argument("--login-only", action="store_true")
    parser.add_argument("--cdp", type=str, metavar="URL", nargs="?", const=CDP_URL or "http://localhost:9222", default=CDP_URL)
    parser.add_argument("--marked-entries-only", action="store_true",
                        help="Nur gelb markierte Eintraege aus dem MC-PDF extrahieren")
    parser.add_argument("--only", type=str, metavar="SCRAPER",
                        help="Nur einen bestimmten Scraper ausfuehren (outlook, bahn, amazon, "
                             "google, spiegel, audible, heise, adobe, figma, cloudflare, portal)")
    args = parser.parse_args()

    # Set up logging to both stdout and log file
    log_dir = Path(__file__).resolve().parent / "logs"
    log_file = setup_logging(log_dir)

    timer = Timer()
    result = RunResult()

    _check_credentials(args)

    print("=" * 50)
    print("Expense Bot")
    print("=" * 50)

    # -- Run-Ordner erstellen --
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = _cfg.DOWNLOAD_DIR / run_timestamp
    run_dir.mkdir(parents=True, exist_ok=True)

    # Legacy: Bahn-Modul nutzt _cfg.DOWNLOAD_DIR intern als Fallback.
    # TODO: Remove once bahn.py is fully migrated to parameter passing.
    _cfg.DOWNLOAD_DIR = run_dir
    _cfg.BELEGE_DIR = run_dir

    print(f"\nBelege-Ordner: {run_dir}")

    # -- MC-PDF parsen --
    booking_refs = None
    non_db_raw = []

    if args.mc_pdf:
        from src.mastercard import extract_all_entries, get_db_entries, get_non_db_entries, print_summary

        mc_path = Path(args.mc_pdf)
        if mc_path.is_dir():
            pdf_files = sorted(
                [p for p in mc_path.iterdir() if p.suffix.lower() == ".pdf"],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if not pdf_files:
                print(f"Keine PDF-Dateien im Ordner: {mc_path}")
                return
            mc_path = pdf_files[0]
            print(f"\nOrdner: {args.mc_pdf}")
            print(f"   Neuestes PDF: {mc_path.name}")

        result.mc_pdf_name = mc_path.name

        # Run-Ordner nach MC-PDF benennen
        safe_name = re.sub(r"[^\w\-.]", "_", mc_path.stem)[:60]
        named_dir = run_dir.parent / f"{run_timestamp}_{safe_name}"
        if run_dir.exists() and not any(run_dir.iterdir()):
            run_dir.rename(named_dir)
            run_dir = named_dir
            _cfg.DOWNLOAD_DIR = run_dir  # legacy fallback for bahn.py
            _cfg.BELEGE_DIR = run_dir
        print(f"Belege-Ordner: {run_dir}")
        print(f"\nLese Mastercard-PDF: {mc_path}")

        all_entries = extract_all_entries(str(mc_path), marked_only=args.marked_entries_only)
        db_entries = get_db_entries(all_entries)
        non_db_raw = get_non_db_entries(all_entries)
        result.add_entries(all_entries)

        net = print_summary(db_entries, "DB-Buchungen")
        booking_refs = [b["booking_ref"] for b in net if b.get("booking_ref")]
        timer.lap("PDF-Parsing")

        if not booking_refs:
            print("Keine DB-Buchungsnummern im PDF gefunden.")

    # -- Welche Scraper laufen? --
    only = (args.only or "").lower().strip()
    VALID_SCRAPERS = {"outlook", "bahn", "amazon", "google", "spiegel", "audible",
                      "heise", "adobe", "figma", "cloudflare", "portal"}
    if only and only not in VALID_SCRAPERS:
        print(f"FEHLER: Unbekannter Scraper '{only}'. Verfügbar: {', '.join(sorted(VALID_SCRAPERS))}")
        sys.exit(1)
    if only:
        print(f"\n⚡ Nur Scraper: {only}")

    def _should_run(name: str) -> bool:
        return not only or only == name

    # -- Outlook Belege --
    html_fallbacks = []
    if non_db_raw and _should_run("outlook"):
        html_fallbacks = _fetch_outlook(non_db_raw, result, timer, download_dir=run_dir)

    # -- Spiegel (eigener Browser) --
    if non_db_raw and _should_run("spiegel"):
        _fetch_spiegel(non_db_raw, result, timer, args.headed, download_dir=run_dir)

    # -- Browser-Automation (nur wenn ein CDP-Scraper laufen soll) --
    cdp_scrapers = {"bahn", "amazon", "google", "audible", "heise", "adobe", "figma", "cloudflare", "portal"}
    needs_cdp = not only or only in cdp_scrapers

    if needs_cdp:
        with sync_playwright() as p:
            use_cdp = False
            browser = None

            if args.cdp:
                print(f"\nVerbinde mit Chrome ueber CDP: {args.cdp}")
                try:
                    browser = p.chromium.connect_over_cdp(args.cdp)
                    use_cdp = True
                    timer.lap("Chrome-Verbindung (CDP)")
                except Exception:
                    print("   CDP nicht erreichbar – starte eigenen Browser ...")

            if use_cdp and browser:
                context = browser.contexts[0] if browser.contexts else browser.new_context(
                    accept_downloads=True, locale="de-DE")
                page = context.new_page()
                try:
                    if _should_run("bahn"):
                        login(page, timer)
                    if args.login_only:
                        print(f"\nLogin erfolgreich!\nFertig: {timer.elapsed()}")
                        return

                    if _should_run("bahn"):
                        _fetch_bahn(page, timer, result, booking_refs, args.all, download_dir=run_dir)
                    if _should_run("amazon"):
                        _fetch_amazon(context, result, timer, download_dir=run_dir)
                    if not only:
                        _fetch_portals(page, result, timer, download_dir=run_dir)
                    else:
                        _fetch_portals_single(page, result, timer, only, download_dir=run_dir)
                finally:
                    page.close()
                    browser.close()
            else:
                BROWSER_DATA_DIR.mkdir(exist_ok=True)
                print("\nStarte headless Browser ...")
                context = p.chromium.launch_persistent_context(
                    user_data_dir=str(BROWSER_DATA_DIR),
                    headless=not args.headed, accept_downloads=True, locale="de-DE")
                page = context.new_page()
                try:
                    if _should_run("bahn"):
                        login(page, timer)
                    if args.login_only:
                        print("\nLogin erfolgreich! ENTER zum Schliessen ...")
                        input()
                        context.close()
                        print(f"\nFertig: {timer.elapsed()}")
                        return

                    if _should_run("bahn"):
                        _fetch_bahn(page, timer, result, booking_refs, args.all, download_dir=run_dir)
                    if _should_run("amazon"):
                        _fetch_amazon(context, result, timer, download_dir=run_dir)
                    if not only:
                        _fetch_portals(page, result, timer, download_dir=run_dir)
                    else:
                        _fetch_portals_single(page, result, timer, only, download_dir=run_dir)
                finally:
                    context.close()

    # -- HTML-Fallbacks anwenden (Outlook Email-Body als letzter Ausweg) --
    if html_fallbacks:
        applied = 0
        discarded_files = []
        for m in html_fallbacks:
            entry = m["entry"]
            files = m.get("files", [])
            er = result.find_entry(entry.get("_id", ""))
            if not er:
                for candidate in result.entries:
                    if candidate.entry is entry:
                        er = candidate
                        break
            if not er:
                candidates = [
                    c for c in result.entries
                    if (c.status == "pending"
                        and c.vendor == entry.get("vendor", "")
                        and abs(c.amount - entry.get("amount", 0)) < 0.01
                        and c.date == entry.get("date", ""))
                ]
                if len(candidates) == 1:
                    er = candidates[0]
            if er and er.status == "pending" and files:
                result.mark_matched(entry, files, source="outlook:html",
                                    email_subject=m.get("email_subject", ""))
                applied += 1
            else:
                # Entry wurde von Portal-Scraper als PDF geholt → HTML-Datei löschen
                discarded_files.extend(files)
        # Verwaiste HTML-Dateien aufräumen
        for f in discarded_files:
            if f.exists() and (".html" in str(f)):
                f.unlink(missing_ok=True)
        if applied:
            print(f"\n  {applied} Outlook HTML-Fallback(s) angewendet (kein Portal-PDF gefunden)")
        if discarded_files:
            print(f"  {len(discarded_files)} verwaiste HTML-Datei(en) entfernt (Portal-PDF vorhanden)")

    # -- Pending -> Unmatched (mit Diagnostik) --
    for er in result.entries:
        if er.status == "pending" and not er.is_credit and not er.is_fx_fee:
            er.status = "unmatched"
            if not er.note:
                er.note = "Kein Scraper/Email-Match gefunden"

    # -- Email (nur bei vollem Run) --
    print(f"\n{result.summary()}")
    if only:
        print(f"\n⚡ Nur '{only}' getestet — kein Email-Versand")
        print(f"  Belege in: {run_dir}")
    else:
        send_email(result, timer, dry_run=args.dry_run, cc_email=args.cc)
        cleanup_old_invoices(KEEP_DAYS)
    print(f"\nFertig! Gesamtzeit: {timer.elapsed()}")


# --- Fetch-Funktionen mit direkter Result-Zuordnung ---

def _fetch_outlook(non_db_raw: list[dict], result: RunResult, timer: Timer, *, download_dir: Path) -> list[dict]:
    """Outlook Belege suchen und direkt im Result tracken.

    Returns:
        Liste von HTML-Body-Fallbacks (Entry+File), die nur verwendet werden
        wenn kein Portal-Scraper eine echte PDF liefert.
    """
    from src.outlook import match_and_download_receipts, _cleanup_pdf_browser
    token = get_graph_token()
    outlook_results = match_and_download_receipts(token, non_db_raw, download_dir)
    _cleanup_pdf_browser()  # Shared Playwright-Browser schliessen vor Spiegel/CDP

    html_fallbacks = []
    for m in outlook_results.get("matched", []):
        entry = m["entry"]
        files = m.get("files", [])
        if not files:
            continue

        # Prüfe ob NUR HTML-Body (kein echtes PDF) — dann als Fallback merken
        has_real_pdf = any(".html" not in str(f) for f in files)
        if has_real_pdf:
            result.mark_matched(entry, files, source="outlook",
                                email_subject=m.get("email_subject", ""))
        else:
            # HTML-Body-only: nicht als matched markieren, damit Portal-Scraper
            # noch eine echte Rechnung holen können
            html_fallbacks.append(m)

    timer.lap(f"Outlook ({len(outlook_results.get('downloaded_files', []))} PDFs)")
    return html_fallbacks


def _fetch_bahn(page, timer: Timer, result: RunResult, booking_refs: list[str] | None, download_all: bool, *, download_dir: Path):
    """DB Rechnungen downloaden und per Booking-Ref zuordnen."""
    files, failed = download_invoices(page, timer, download_all=download_all, booking_refs=booking_refs, download_dir=download_dir)

    # Zuordnung: Dateiname enthält die Booking-Ref
    for f in files:
        matched = False
        for er in result.entries:
            if er.is_db and er.status == "pending" and not er.is_credit:
                ref = er.entry.get("booking_ref", "")
                if ref and ref in f.name:
                    er.status = "matched"
                    er.files = [f]
                    er.source = "bahn.de"
                    matched = True
                    break
        if not matched:
            # Datei existiert, aber kein Entry-Match -> loggen
            print(f"  WARNUNG: DB-PDF {f.name} konnte keinem Eintrag zugeordnet werden")

    for ref in failed:
        for er in result.entries:
            if er.is_db and er.entry.get("booking_ref") == ref and er.status == "pending":
                er.status = "unmatched"
                er.note = "Download fehlgeschlagen"
                break


def _fetch_amazon(context, result: RunResult, timer: Timer, *, download_dir: Path):
    """Amazon Rechnungen downloaden — Scraper gibt (entry, file) Paare zurueck."""
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    amazon_entries = [e for e in pending
                      if "AMZN" in e.get("vendor", "").upper() or "AMAZON" in e.get("vendor", "").upper()]
    if not (AMAZON_EMAIL and AMAZON_PASSWORD and amazon_entries):
        return

    from src.amazon import download_amazon_invoices
    amazon_page = context.new_page()
    amazon_results = download_amazon_invoices(amazon_page, amazon_entries, download_dir, AMAZON_EMAIL, AMAZON_PASSWORD)
    amazon_page.close()

    for entry, filepath in amazon_results:
        result.mark_matched(entry, [filepath], source="amazon.de")

    if amazon_results:
        timer.lap(f"Amazon ({len(amazon_results)} Rechnungen)")


def _fetch_spiegel(non_db_raw: list[dict], result: RunResult, timer: Timer, headed: bool, *, download_dir: Path):
    """Spiegel Rechnung — eigener Browser-Context."""
    from src.spiegel import download_spiegel_invoices
    spiegel_results = download_spiegel_invoices(non_db_raw, download_dir, headed=headed)
    for entry, filepath in spiegel_results:
        result.mark_matched(entry, [filepath], source="spiegel")
    if spiegel_results:
        timer.lap(f"Spiegel ({len(spiegel_results)} PDFs)")


def _fetch_portals(page, result: RunResult, timer: Timer, *, download_dir: Path):
    """Alle Portal-Scraper — jeder bekommt nur seine pending Entries."""
    total = 0

    # Cloudflare (API, kein Browser)
    from src.cloudflare import download_cloudflare_invoices
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    cf_results = download_cloudflare_invoices(pending, download_dir)
    for entry, filepath in cf_results:
        result.mark_matched(entry, [filepath], source="cloudflare-api")
    total += len(cf_results)

    # OpenAI + Adobe (Portal JSON configs)
    from src.portal import download_portal_invoices
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    portal_results = download_portal_invoices(page, pending, download_dir)
    for entry, filepath, portal_id in portal_results:
        result.mark_matched(entry, [filepath], source=f"portal:{portal_id}")
    total += len(portal_results)

    # Heise
    from src.heise import download_heise_invoices
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    heise_results = download_heise_invoices(page, pending, download_dir)
    for entry, filepath in heise_results:
        result.mark_matched(entry, [filepath], source="heise")
    total += len(heise_results)

    # Adobe
    from src.adobe import download_adobe_invoices
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    adobe_results = download_adobe_invoices(page, pending, download_dir)
    for entry, filepath in adobe_results:
        result.mark_matched(entry, [filepath], source="adobe")
    total += len(adobe_results)

    # Figma
    from src.figma import download_figma_invoices
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    figma_results = download_figma_invoices(page, pending, download_dir)
    for entry, filepath in figma_results:
        result.mark_matched(entry, [filepath], source="figma")
    total += len(figma_results)

    # Google (CDP iframe)
    from src.google import download_google_invoices
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    google_results = download_google_invoices(page, pending, download_dir)
    for entry, filepath in google_results:
        result.mark_matched(entry, [filepath], source="google")
    total += len(google_results)

    # Audible
    from src.audible import download_audible_invoices
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    audible_results = download_audible_invoices(page, pending, download_dir)
    for entry, filepath in audible_results:
        result.mark_matched(entry, [filepath], source="audible")
    total += len(audible_results)

    if total:
        timer.lap(f"Portale ({total} Rechnungen)")


def _fetch_portals_single(page, result: RunResult, timer: Timer, scraper: str, *, download_dir: Path):
    """Führt einen einzelnen Portal-Scraper aus (für --only Modus)."""
    pending = [er.entry for er in result.non_db_entries if er.status == "pending"]
    total = 0

    if scraper == "cloudflare":
        from src.cloudflare import download_cloudflare_invoices
        for entry, filepath in download_cloudflare_invoices(pending, download_dir):
            result.mark_matched(entry, [filepath], source="cloudflare-api")
            total += 1
    elif scraper == "portal":
        from src.portal import download_portal_invoices
        for entry, filepath, pid in download_portal_invoices(page, pending, download_dir):
            result.mark_matched(entry, [filepath], source=f"portal:{pid}")
            total += 1
    elif scraper == "heise":
        from src.heise import download_heise_invoices
        for entry, filepath in download_heise_invoices(page, pending, download_dir):
            result.mark_matched(entry, [filepath], source="heise")
            total += 1
    elif scraper == "adobe":
        from src.adobe import download_adobe_invoices
        for entry, filepath in download_adobe_invoices(page, pending, download_dir):
            result.mark_matched(entry, [filepath], source="adobe")
            total += 1
    elif scraper == "figma":
        from src.figma import download_figma_invoices
        for entry, filepath in download_figma_invoices(page, pending, download_dir):
            result.mark_matched(entry, [filepath], source="figma")
            total += 1
    elif scraper == "google":
        from src.google import download_google_invoices
        for entry, filepath in download_google_invoices(page, pending, download_dir):
            result.mark_matched(entry, [filepath], source="google")
            total += 1
    elif scraper == "audible":
        from src.audible import download_audible_invoices
        for entry, filepath in download_audible_invoices(page, pending, download_dir):
            result.mark_matched(entry, [filepath], source="audible")
            total += 1

    if total:
        timer.lap(f"{scraper} ({total} Rechnungen)")


if __name__ == "__main__":
    main()
