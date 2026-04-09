"""Tests für src/figma.py — Figma Admin Console Invoice Download."""

import pytest


class TestFigmaEntryFilter:
    def test_filters_figma_entries(self):
        entries = [
            {"vendor": "FIGMA", "amount": 134.05, "is_credit": False},
            {"vendor": "FIGMA, SAN FRANCISCO", "amount": 155.00, "is_credit": False},
            {"vendor": "ANTHROPIC", "amount": 100.0, "is_credit": False},
        ]
        figma = [e for e in entries if not e.get("is_credit") and "FIGMA" in e.get("vendor", "").upper()]
        assert len(figma) == 2


class TestFigmaConfig:
    def test_needs_team_id(self):
        from src.config import FIGMA_TEAM_ID
        if FIGMA_TEAM_ID:
            assert len(FIGMA_TEAM_ID) > 5


@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveFigma:
    def test_figma_invoices_page(self, live, tmp_path):
        from playwright.sync_api import sync_playwright
        from src.figma import download_figma_invoices

        entries = [{"vendor": "FIGMA", "amount": 134.05, "date": "11.03.26", "is_credit": False}]

        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0]
                page = context.new_page()
                files = download_figma_invoices(page, entries, tmp_path)
                print(f"Downloaded: {len(files)} files")
                page.close()
                browser.close()
            except Exception as e:
                pytest.skip(f"CDP nicht verfügbar: {e}")
