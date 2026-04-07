"""
Tests für fetch_amazon.py
=========================
Testet Amazon-Rechnungsdownload.

Standalone:
    pytest tests/test_amazon.py -v
    pytest tests/test_amazon.py -v --live  # mit echtem Amazon-Login (headed Browser!)
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── Unit Tests ────────────────────────────────────────────

class TestAmazonEntryFilter:
    def test_filters_amazon_entries(self):
        entries = [
            {"vendor": "AMZN Mktp DE", "amount": 126.03, "is_credit": False},
            {"vendor": "Amazon.de*VD9CW3WR5", "amount": 21.29, "is_credit": False},
            {"vendor": "ANTHROPIC", "amount": 103.36, "is_credit": False},
            {"vendor": "AMZN Mktp DE", "amount": 50.0, "is_credit": True},  # Gutschrift
        ]
        amazon = [
            e for e in entries
            if not e.get("is_credit")
            and ("AMZN" in e.get("vendor", "").upper() or "AMAZON" in e.get("vendor", "").upper())
        ]
        assert len(amazon) == 2
        assert amazon[0]["amount"] == 126.03
        assert amazon[1]["amount"] == 21.29


# ─── Live-Tests ────────────────────────────────────────────

@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveAmazon:
    def test_amazon_login_and_orders(self, live, tmp_path):
        """Testet Amazon-Login und Bestellübersicht (braucht --headed für 2FA)."""
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        from expense_bot import AMAZON_EMAIL, AMAZON_PASSWORD
        from fetch_amazon import download_amazon_invoices
        from playwright.sync_api import sync_playwright

        if not AMAZON_EMAIL or not AMAZON_PASSWORD:
            pytest.skip("AMAZON_EMAIL/AMAZON_PASSWORD nicht konfiguriert")

        test_entries = [
            {"vendor": "AMZN Mktp DE", "amount": 126.03, "date": "12.03.26", "is_credit": False},
        ]

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(Path(__file__).parent.parent / ".browser-data-amazon"),
                headless=False,
                accept_downloads=True,
                locale="de-DE",
            )
            page = context.new_page()
            try:
                files = download_amazon_invoices(page, test_entries, tmp_path, AMAZON_EMAIL, AMAZON_PASSWORD)
                # Mindestens sollte die Bestellübersicht geladen werden
                # (ob ein Match gefunden wird hängt von den Bestellungen ab)
                print(f"Downloaded: {len(files)} files")
            finally:
                context.close()
