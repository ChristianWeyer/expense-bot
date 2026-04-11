#!/usr/bin/env python3
"""Isolierter Scraper-Test mit Fake-MC-Entries.

Nutzt keine echte MC-PDF, sondern hardcodierte Test-Entries pro Scraper.
Läuft gegen echte Live-APIs/Portale via CDP.

Nutzung:
    python test_scraper.py google
    python test_scraper.py amazon
    python test_scraper.py all
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

# Test-Entries pro Scraper (basierend auf MC 03-2026)
TEST_ENTRIES = {
    "google": [
        {"vendor": "GOOGLE*YOUTUBE MEMBER", "amount": 4.99, "date": "10.03.26", "is_credit": False},
        {"vendor": "WL*GOOGLE", "amount": 4.99, "date": "21.03.26", "is_credit": False},
    ],
    "spiegel": [
        {"vendor": "SPIEGEL* KN 1920835 AB", "amount": 24.99, "date": "16.03.26", "is_credit": False},
    ],
    "audible": [
        {"vendor": "Audible Gmbh*QC5DN5HQ5", "amount": 9.95, "date": "06.03.26", "is_credit": False},
    ],
    "heise": [
        {"vendor": "Heise Medien GmbH & Co.", "amount": 12.95, "date": "09.03.26", "is_credit": False},
    ],
    "adobe": [
        {"vendor": "ADOBE", "amount": 66.45, "date": "21.03.26", "is_credit": False},
    ],
    "figma": [
        {"vendor": "FIGMA", "amount": 134.05, "date": "11.03.26", "is_credit": False},
    ],
    "cloudflare": [
        {"vendor": "CLOUDFLARE", "amount": 4.35, "date": "19.03.26", "is_credit": False},
    ],
    "amazon": [
        {"vendor": "AMZN Mktp DE*IT5HF5H85", "amount": 126.03, "date": "12.03.26", "is_credit": False},
        {"vendor": "Amazon.de", "amount": 21.29, "date": "16.03.26", "is_credit": False},
    ],
    "bahn": [
        # Bahn braucht Buchungs-Referenzen, keine MC-Entries
        ["739359417117", "365524305823"],
    ],
    "portal-chatgpt": [
        {"vendor": "OPENAI *CHATGPT SUBSCR", "amount": 61.94, "date": "03.03.26", "is_credit": False},
        {"vendor": "OPENAI *CHATGPT SUBSCR", "amount": 20.68, "date": "20.03.26", "is_credit": False},
    ],
    "portal-openai": [
        {"vendor": "OPENAI", "amount": 155.15, "date": "09.03.26", "is_credit": False},
    ],
    "outlook": [
        {"vendor": "ANTHROPIC", "amount": 103.36, "date": "03.03.26", "is_credit": False},
        {"vendor": "FIGMA", "amount": 134.05, "date": "11.03.26", "is_credit": False},
    ],
}


def _dl_dir(name: str) -> Path:
    p = Path(f"/tmp/scraper_test_{name}")
    p.mkdir(exist_ok=True)
    for f in p.glob("*"):
        f.unlink()
    return p


def _print_results(name: str, results):
    print(f"\n  => {len(results)} Ergebnis(se)")
    for item in results:
        if isinstance(item, tuple):
            if len(item) == 2:
                e, f = item
                print(f"     {e.get('vendor', '?'):<30s} {e.get('amount', 0):>7.2f} EUR -> {f.name}")
            elif len(item) == 3:
                e, f, pid = item
                print(f"     [{pid}] {e.get('vendor', '?'):<30s} {e.get('amount', 0):>7.2f} EUR -> {f.name}")
        else:
            print(f"     {item}")


def test_google():
    from src.google import download_google_invoices
    dl = _dl_dir("google")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            results = download_google_invoices(page, TEST_ENTRIES["google"], dl)
            _print_results("google", results)
        finally:
            page.close()
            browser.close()


def test_spiegel():
    from src.spiegel import download_spiegel_invoices
    dl = _dl_dir("spiegel")
    results = download_spiegel_invoices(TEST_ENTRIES["spiegel"], dl)
    _print_results("spiegel", results)


def test_audible():
    from src.audible import download_audible_invoices
    dl = _dl_dir("audible")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            results = download_audible_invoices(page, TEST_ENTRIES["audible"], dl)
            _print_results("audible", results)
        finally:
            page.close()
            browser.close()


def test_heise():
    from src.heise import download_heise_invoices
    dl = _dl_dir("heise")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            results = download_heise_invoices(page, TEST_ENTRIES["heise"], dl)
            _print_results("heise", results)
        finally:
            page.close()
            browser.close()


def test_adobe():
    from src.adobe import download_adobe_invoices
    dl = _dl_dir("adobe")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            results = download_adobe_invoices(page, TEST_ENTRIES["adobe"], dl)
            _print_results("adobe", results)
        finally:
            page.close()
            browser.close()


def test_figma():
    from src.figma import download_figma_invoices
    dl = _dl_dir("figma")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            results = download_figma_invoices(page, TEST_ENTRIES["figma"], dl)
            _print_results("figma", results)
        finally:
            page.close()
            browser.close()


def test_cloudflare():
    from src.cloudflare import download_cloudflare_invoices
    dl = _dl_dir("cloudflare")
    results = download_cloudflare_invoices(TEST_ENTRIES["cloudflare"], dl)
    _print_results("cloudflare", results)


def test_amazon():
    from src.amazon import download_amazon_invoices
    from src.config import AMAZON_EMAIL, AMAZON_PASSWORD
    dl = _dl_dir("amazon")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        page = ctx.new_page()
        try:
            results = download_amazon_invoices(page, TEST_ENTRIES["amazon"], dl, AMAZON_EMAIL, AMAZON_PASSWORD)
            _print_results("amazon", results)
        finally:
            page.close()
            browser.close()


def test_bahn():
    from src.bahn import login, download_invoices
    from src.timer import Timer
    dl = _dl_dir("bahn")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        timer = Timer()
        try:
            login(page, timer)
            files, failed = download_invoices(page, timer, booking_refs=TEST_ENTRIES["bahn"][0], download_dir=dl)
            print(f"\n  => {len(files)} PDFs, {len(failed)} fehlgeschlagen")
            for f in files:
                print(f"     {f.name}")
        finally:
            page.close()
            browser.close()


def test_portal_chatgpt():
    from src.portal import download_portal_invoices
    dl = _dl_dir("portal_chatgpt")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            results = download_portal_invoices(page, TEST_ENTRIES["portal-chatgpt"], dl)
            _print_results("portal-chatgpt", results)
        finally:
            page.close()
            browser.close()


def test_portal_openai():
    from src.portal import download_portal_invoices
    dl = _dl_dir("portal_openai")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        page = browser.contexts[0].new_page()
        try:
            results = download_portal_invoices(page, TEST_ENTRIES["portal-openai"], dl)
            _print_results("portal-openai", results)
        finally:
            page.close()
            browser.close()


def test_outlook():
    from src.outlook import match_and_download_receipts
    from src.auth import get_graph_token
    dl = _dl_dir("outlook")
    token = get_graph_token()
    results = match_and_download_receipts(token, TEST_ENTRIES["outlook"], dl)
    print(f"\n  => {len(results.get('matched', []))} matched, {len(results.get('unmatched', []))} unmatched")
    for m in results.get("matched", []):
        files = m.get("files", [])
        for f in files:
            print(f"     {m['entry']['vendor']:<30s} -> {f.name}")


SCRAPERS = {
    "google": test_google,
    "spiegel": test_spiegel,
    "audible": test_audible,
    "heise": test_heise,
    "adobe": test_adobe,
    "figma": test_figma,
    "cloudflare": test_cloudflare,
    "amazon": test_amazon,
    "bahn": test_bahn,
    "portal-chatgpt": test_portal_chatgpt,
    "portal-openai": test_portal_openai,
    "outlook": test_outlook,
}


def main():
    if len(sys.argv) < 2:
        print("Nutzung: python test_scraper.py <scraper>")
        print(f"Verfuegbar: {', '.join(sorted(SCRAPERS.keys()))}, all")
        sys.exit(1)

    target = sys.argv[1].lower()

    if target == "all":
        for name in SCRAPERS:
            print(f"\n{'='*60}")
            print(f"  {name.upper()}")
            print(f"{'='*60}")
            try:
                SCRAPERS[name]()
            except Exception as e:
                print(f"  ❌ {name} failed: {e}")
        return

    if target not in SCRAPERS:
        print(f"Unbekannter Scraper: {target}")
        print(f"Verfuegbar: {', '.join(sorted(SCRAPERS.keys()))}")
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  {target.upper()}")
    print(f"{'='*60}")
    SCRAPERS[target]()


if __name__ == "__main__":
    main()
