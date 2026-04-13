"""Tests für src/cloudflare.py — API-basierter Rechnungs-Download."""

import pytest
from src.cloudflare import _get_cf_token, _filter_cloudflare_entries


class TestCloudflareConfig:
    def test_entry_filter(self):
        entries = [
            {"vendor": "CLOUDFLARE", "amount": 4.35, "is_credit": False},
            {"vendor": "ANTHROPIC", "amount": 100.0, "is_credit": False},
            {"vendor": "CLOUDFLARE, SAN FRANCISCO", "amount": 5.00, "is_credit": True},
        ]
        cf = _filter_cloudflare_entries(entries)
        assert len(cf) == 1
        assert cf[0]["amount"] == 4.35


@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveCloudflare:
    def test_api_token_available(self, live):
        token = _get_cf_token()
        assert token is not None, "CLOUDFLARE_API_TOKEN nicht konfiguriert"

    def test_download_invoice(self, live, tmp_path):
        from src.cloudflare import download_cloudflare_invoices
        token = _get_cf_token()
        if not token:
            pytest.skip("CLOUDFLARE_API_TOKEN nicht konfiguriert")
        entries = [{"vendor": "CLOUDFLARE", "amount": 4.35, "date": "19.03.26", "is_credit": False}]
        files = download_cloudflare_invoices(entries, tmp_path)
        print(f"Downloaded: {len(files)} files")
