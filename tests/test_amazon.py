"""Tests für src/amazon.py — Amazon Entry-Filter und Live-Download."""

import pytest
from pathlib import Path

from src.amazon import _filter_amazon_entries


class TestAmazonEntryFilter:
    def test_filters_amazon_entries(self):
        entries = [
            {"vendor": "AMZN Mktp DE", "amount": 126.03, "is_credit": False},
            {"vendor": "Amazon.de*VD9CW3WR5", "amount": 21.29, "is_credit": False},
            {"vendor": "ANTHROPIC", "amount": 103.36, "is_credit": False},
            {"vendor": "AMZN Mktp DE", "amount": 50.0, "is_credit": True},
        ]
        amazon = _filter_amazon_entries(entries)
        assert len(amazon) == 2
        assert amazon[0]["amount"] == 126.03
        assert amazon[1]["amount"] == 21.29


@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveAmazon:
    def test_amazon_login_and_orders(self, live, tmp_path):
        from src.config import AMAZON_EMAIL, AMAZON_PASSWORD
        from src.amazon import download_amazon_invoices
        from playwright.sync_api import sync_playwright

        if not AMAZON_EMAIL or not AMAZON_PASSWORD:
            pytest.skip("AMAZON_EMAIL/AMAZON_PASSWORD nicht konfiguriert")
        print(f"\n  Amazon creds: {AMAZON_EMAIL[:5]}*** / {'set' if AMAZON_PASSWORD else 'NONE'}")

        test_entries = [
            {"vendor": "AMZN Mktp DE", "amount": 126.03, "date": "12.03.26", "is_credit": False},
        ]

        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(Path(__file__).parent.parent / ".browser-data-amazon"),
                headless=False, accept_downloads=True, locale="de-DE",
            )
            page = context.new_page()
            try:
                files = download_amazon_invoices(page, test_entries, tmp_path, AMAZON_EMAIL, AMAZON_PASSWORD)
                print(f"Downloaded: {len(files)} files")
            finally:
                context.close()
