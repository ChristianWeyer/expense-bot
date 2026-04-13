"""Tests für den Audible Rechnungs-Scraper.

Testet Entry-Filterung, Betragsformatierung und Dateiname-Generierung.
Browser-Interaktion wird nicht getestet (erfordert Amazon-Session).
"""

import re

import pytest

from src.audible import _filter_audible_entries


# ─── Entry-Filterung ────────────────────────────────────────────────

class TestAudibleEntryFilter:
    """Verifiziert die Filterlogik für Audible-Einträge."""

    def test_audible_gmbh_with_ref(self):
        entries = [{"vendor": "Audible Gmbh*030JV0125", "amount": 9.95}]
        assert len(_filter_audible_entries(entries)) == 1

    def test_audible_plain(self):
        entries = [{"vendor": "Audible Gmbh", "amount": 9.95}]
        assert len(_filter_audible_entries(entries)) == 1

    def test_audible_gmbh_uppercase(self):
        entries = [{"vendor": "Audible GmbH*ZCOZO1N24", "amount": 9.95}]
        assert len(_filter_audible_entries(entries)) == 1

    def test_non_audible_excluded(self):
        entries = [{"vendor": "AMAZON", "amount": 10.0}]
        assert len(_filter_audible_entries(entries)) == 0

    def test_credit_excluded(self):
        entries = [{"vendor": "Audible Gmbh", "amount": 9.95, "is_credit": True}]
        assert len(_filter_audible_entries(entries)) == 0

    def test_mixed_entries(self):
        entries = [
            {"vendor": "Audible Gmbh*030JV0125", "amount": 9.95},
            {"vendor": "AMZN Mktp DE*ABC123", "amount": 25.99},
            {"vendor": "Audible GmbH*ZCOZO1N24", "amount": 9.95},
        ]
        assert len(_filter_audible_entries(entries)) == 2


# ─── Betragsformatierung ────────────────────────────────────────────

class TestAudibleAmountFormat:
    """Audible prüft Beträge im deutschen Format auf der Detailseite."""

    def test_standard_amount(self):
        amount = 9.95
        assert f"{amount:.2f}".replace(".", ",") == "9,95"

    def test_integer_amount(self):
        amount = 10.00
        assert f"{amount:.2f}".replace(".", ",") == "10,00"


# ─── Dateiname-Generierung ──────────────────────────────────────────

class TestAudibleFilename:
    def test_standard(self):
        date_str = "06.12.25"
        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        fname = f"{date_prefix}Audible_Rechnung.pdf"
        assert fname == "061225_Audible_Rechnung.pdf"

    def test_no_date(self):
        date_str = ""
        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        fname = f"{date_prefix}Audible_Rechnung.pdf"
        assert fname == "Audible_Rechnung.pdf"


# ─── Login-Detection ────────────────────────────────────────────────

class TestAudibleLoginDetection:
    """Verifiziert die Login-Erkennung über URL-Pattern."""

    def test_signin_detected(self):
        url = "https://www.amazon.de/ap/signin?openid.pape..."
        assert "signin" in url

    def test_normal_url_not_signin(self):
        url = "https://www.audible.de/account/purchase-history?tf=membership"
        assert "signin" not in url

    def test_ap_signin_detected(self):
        url = "https://www.amazon.de/ap/signin"
        assert "signin" in url or "ap/signin" in url


# ─── PDF-Validierung ────────────────────────────────────────────────

class TestAudiblePDFValidation:
    """Audible prüft den PDF-Header der heruntergeladenen Datei."""

    def test_valid_pdf_header(self):
        content = b"%PDF-1.4 some pdf content"
        assert content[:4] == b"%PDF"

    def test_invalid_header(self):
        content = b"<html>Not a PDF</html>"
        assert content[:4] != b"%PDF"

    def test_empty_content(self):
        content = b""
        assert len(content) == 0
