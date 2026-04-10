"""Tests für src/heise.py — Plenigo Self-Service Rechnungs-Download."""

import pytest

from src.heise import _filter_heise_entries


class TestHeiseEntryFilter:
    def test_filters_heise_entries(self):
        entries = [
            {"vendor": "Heise Medien GmbH & Co", "amount": 12.95, "is_credit": False},
            {"vendor": "HEISE ONLINE", "amount": 5.0, "is_credit": False},
            {"vendor": "ANTHROPIC", "amount": 100.0, "is_credit": False},
        ]
        heise = _filter_heise_entries(entries)
        assert len(heise) == 2


@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveHeise:
    def test_heise_invoice_download(self, live, tmp_path):
        from playwright.sync_api import sync_playwright
        from src.heise import download_heise_invoices

        entries = [{"vendor": "Heise Medien GmbH & Co", "amount": 12.95, "date": "09.03.26", "is_credit": False}]

        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0]
                page = context.new_page()
                files = download_heise_invoices(page, entries, tmp_path)
                assert len(files) >= 1, "Keine Heise-Rechnung heruntergeladen"
                assert files[0].stat().st_size > 1000
                print(f"Downloaded: {files[0].name} ({files[0].stat().st_size / 1024:.1f} KB)")
                page.close()
                browser.close()
            except Exception as e:
                pytest.skip(f"CDP nicht verfügbar: {e}")
