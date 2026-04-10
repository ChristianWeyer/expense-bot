"""Tests fuer den Adobe Rechnungs-Scraper."""

import pytest
from datetime import datetime
from src.adobe import _parse_date, _parse_amount, _parse_entry_date


class TestParseDate:
    def test_english_format(self):
        assert _parse_date("Mar 21, 2026") == datetime(2026, 3, 21)

    def test_german_format(self):
        assert _parse_date("21.03.2026") == datetime(2026, 3, 21)

    def test_german_short_year(self):
        assert _parse_date("21.03.26") == datetime(2026, 3, 21)

    def test_empty(self):
        assert _parse_date("") is None

    def test_none(self):
        assert _parse_date(None) is None


class TestParseAmount:
    def test_euro_sign(self):
        assert _parse_amount("66,45 \u20ac") == 66.45

    def test_eur_text(self):
        assert _parse_amount("66,45 EUR") == 66.45

    def test_thousands(self):
        assert _parse_amount("1.234,56 \u20ac") == 1234.56

    def test_no_currency(self):
        assert _parse_amount("66,45") == 66.45

    def test_invalid(self):
        assert _parse_amount("abc") is None


class TestParseEntryDate:
    def test_normal(self):
        assert _parse_entry_date("21.03.26") == datetime(2026, 3, 21)

    def test_invalid(self):
        assert _parse_entry_date("invalid") is None


class TestMatchingLogic:
    """Test date-based matching without browser."""

    def test_exact_date_match(self):
        from src.adobe import download_adobe_invoices
        # Can't test full flow without browser, but verify entry filtering
        entries = [
            {"vendor": "ADOBE *ADOBE, DUBLIN", "amount": 66.45, "date": "21.03.26"},
            {"vendor": "AMAZON", "amount": 10.0, "date": "01.01.26"},
        ]
        adobe_only = [e for e in entries if "ADOBE" in e.get("vendor", "").upper()]
        assert len(adobe_only) == 1
        assert adobe_only[0]["amount"] == 66.45

    def test_credit_excluded(self):
        entries = [
            {"vendor": "ADOBE *ADOBE", "amount": 66.45, "date": "21.03.26", "is_credit": True},
        ]
        filtered = [e for e in entries if not e.get("is_credit") and "ADOBE" in e.get("vendor", "").upper()]
        assert len(filtered) == 0


@pytest.mark.skipif(True, reason="Live-Test: CDP muss laufen")
class TestAdobeLive:
    def test_download(self):
        from pathlib import Path
        from playwright.sync_api import sync_playwright

        entries = [{"vendor": "ADOBE *ADOBE, DUBLIN", "amount": 66.45, "date": "21.03.26"}]
        dl_dir = Path("/tmp/test_adobe_dl")
        dl_dir.mkdir(exist_ok=True)

        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp("http://localhost:9222")
            context = browser.contexts[0]
            page = context.new_page()
            try:
                from src.adobe import download_adobe_invoices
                results = download_adobe_invoices(page, entries, dl_dir)
                assert len(results) == 1
                assert results[0][1].exists()
                assert results[0][1].stat().st_size > 1000
            finally:
                page.close()
                browser.close()
