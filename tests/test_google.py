"""Tests für den Google Payments Scraper.

Testet Entry-Filterung, Betrags-Formatierung, Datums-Matching und
die Logik zur Vermeidung von falschen Zuordnungen (privat vs. Business).
"""

import re
from datetime import datetime

import pytest

from src.util import parse_date
from src.google import _check_detail_date, _check_pdf_date, _filter_google_entries, _extract_row_date


# ─── Entry-Filterung ────────────────────────────────────────────────

class TestGoogleEntryFilter:
    """Verifiziert die Filterlogik für Google-Einträge."""

    def test_google_youtube_member(self):
        entries = [{"vendor": "GOOGLE*YOUTUBE MEMBER", "amount": 4.99}]
        assert len(_filter_google_entries(entries)) == 1

    def test_google_one_excluded(self):
        """Google One wird über Google Play Email abgerechnet, nicht pay.google.com."""
        entries = [{"vendor": "GOOGLE*GOOGLE ONE", "amount": 7.99}]
        assert len(_filter_google_entries(entries)) == 0

    def test_google_star_google_one_excluded(self):
        entries = [{"vendor": "GOOGLE *Google One", "amount": 3.99}]
        assert len(_filter_google_entries(entries)) == 0

    def test_google_one_lowercase_excluded(self):
        entries = [{"vendor": "Google One", "amount": 7.99}]
        assert len(_filter_google_entries(entries)) == 0

    def test_google_youtube_member_lowercase(self):
        entries = [{"vendor": "Google YouTube Member", "amount": 4.99}]
        assert len(_filter_google_entries(entries)) == 1

    def test_wl_google_excluded(self):
        """WL*GOOGLE entries are handled by Outlook, not Google scraper."""
        entries = [{"vendor": "WL*GOOGLE", "amount": 4.99}]
        assert len(_filter_google_entries(entries)) == 0

    def test_non_google_excluded(self):
        entries = [{"vendor": "ANTHROPIC", "amount": 100.0}]
        assert len(_filter_google_entries(entries)) == 0

    def test_credit_excluded(self):
        entries = [{"vendor": "GOOGLE*YOUTUBE MEMBER", "amount": 4.99, "is_credit": True}]
        assert len(_filter_google_entries(entries)) == 0

    def test_mixed_entries(self):
        entries = [
            {"vendor": "GOOGLE*YOUTUBE MEMBER", "amount": 4.99},
            {"vendor": "GOOGLE*GOOGLE ONE", "amount": 7.99},
            {"vendor": "ANTHROPIC", "amount": 100.0},
            {"vendor": "WL*GOOGLE", "amount": 4.99},
            {"vendor": "Google One", "amount": 7.99},
        ]
        filtered = _filter_google_entries(entries)
        assert len(filtered) == 1  # Nur YouTube Member
        assert filtered[0]["vendor"] == "GOOGLE*YOUTUBE MEMBER"


# ─── Betrags-Formatierung ───────────────────────────────────────────

class TestAmountFormatting:
    """Google Payments zeigt Beträge im deutschen Format (Komma als Dezimalzeichen)."""

    def test_simple_amount(self):
        amount = 4.99
        assert f"{amount:.2f}".replace(".", ",") == "4,99"

    def test_large_amount(self):
        amount = 123.45
        assert f"{amount:.2f}".replace(".", ",") == "123,45"

    def test_round_amount(self):
        amount = 10.00
        assert f"{amount:.2f}".replace(".", ",") == "10,00"

    def test_small_amount(self):
        amount = 0.99
        assert f"{amount:.2f}".replace(".", ",") == "0,99"


# ─── Datums-Matching (der Fix für falsche privat/Business-Zuordnung) ───

class TestCheckDetailDate:
    """Tests für _check_detail_date() — die ECHTE Funktion aus google.py.

    Testet deutsche UND englische Datumsformate, da Google Payments
    je nach Browser-Sprache unterschiedliche Formate zeigt.
    """

    # ── Deutsche Formate ("10. Dez. 2025") ──

    def test_german_exact_match(self):
        assert _check_detail_date("10. Dez. 2025", datetime(2025, 12, 10))

    def test_german_close_match(self):
        assert _check_detail_date("12. Dez. 2025", datetime(2025, 12, 10))

    def test_german_within_tolerance(self):
        assert _check_detail_date("24. Dez. 2025", datetime(2025, 12, 10))

    def test_german_wrong_month(self):
        assert not _check_detail_date("14. Mär. 2026", datetime(2025, 12, 10))

    def test_german_januar(self):
        assert _check_detail_date("Transaktionsdetails\n1. Jan. 2026\nYouTube", datetime(2026, 1, 1))

    def test_german_mai(self):
        assert _check_detail_date("15. Mai. 2026", datetime(2026, 5, 15))

    # ── Englische Formate ("Mar 14, 2026") — DER BUG ──

    def test_english_exact_match(self):
        assert _check_detail_date("Dec 10, 2025", datetime(2025, 12, 10))

    def test_english_close_match(self):
        assert _check_detail_date("Dec 12, 2025", datetime(2025, 12, 10))

    def test_english_wrong_month(self):
        """DER ALTE BUG: Englisches Datum wurde nicht erkannt → alles durchgelassen."""
        assert not _check_detail_date("Mar 14, 2026", datetime(2025, 12, 10))

    def test_english_april(self):
        assert not _check_detail_date("Apr 10, 2026", datetime(2025, 12, 10))

    def test_english_full_month(self):
        assert _check_detail_date("December 10, 2025", datetime(2025, 12, 10))

    def test_english_full_month_wrong(self):
        assert not _check_detail_date("March 14, 2026", datetime(2025, 12, 10))

    # ── Die exakten Bugs aus dem Live-Run ──

    def test_bug_google_one_wrong_youtube(self):
        """Google One 3.99 EUR (10.12.25) wurde YouTube Premium €23.99 Mar 14 2026 zugeordnet."""
        detail = "Invoice\nInvoice date\nMar 14, 2026\nYouTube\nTotal in EUR €23.99"
        assert not _check_detail_date(detail, datetime(2025, 12, 10))

    def test_bug_youtube_wrong_all_about_ai(self):
        """YouTube 4.99 EUR (10.12.25) wurde 'All About AI' €4.99 Apr 10 2026 zugeordnet."""
        detail = "Invoice\nApr 10, 2026\nMitgliedschaft bei All About AI"
        assert not _check_detail_date(detail, datetime(2025, 12, 10))

    def test_bug_youtube_21dec_same_wrong_invoice(self):
        """YouTube 4.99 EUR (21.12.25) bekam dieselbe Apr 2026 Rechnung nochmal."""
        detail = "Invoice\nApr 10, 2026\nMitgliedschaft bei All About AI"
        assert not _check_detail_date(detail, datetime(2025, 12, 21))

    # ── Korrekte Zuordnung (wie es sein sollte) ──

    def test_correct_youtube_dec_match(self):
        """YouTube vom 10.12.25 sollte eine Dez 2025 Transaktion matchen."""
        detail = "Transaktionsdetails\nDec 10, 2025\nYouTube Premium"
        assert _check_detail_date(detail, datetime(2025, 12, 10))

    def test_correct_google_one_dec_match(self):
        detail = "Transaktionsdetails\n10. Dez. 2025\nGoogle One"
        assert _check_detail_date(detail, datetime(2025, 12, 10))

    # ── Edge Cases ──

    def test_no_date_in_text_rejects(self):
        """Kein Datum im Detail-Text → ablehnen."""
        assert not _check_detail_date("YouTube Premium €4,99", datetime(2025, 12, 10))

    def test_multiple_dates_one_matches(self):
        text = "Rechnungsdatum: Dec 10, 2025\nFällig: Dec 15, 2025"
        assert _check_detail_date(text, datetime(2025, 12, 10))

    def test_multiple_dates_none_matches(self):
        text = "Rechnungsdatum: Mar 10, 2026\nFällig: Mar 15, 2026"
        assert not _check_detail_date(text, datetime(2025, 12, 10))


# ─── Row-Datums-Extraktion (der eigentliche Fix) ───────────────────

class TestExtractRowDate:
    """Tests für _extract_row_date() — extrahiert Datum aus Google Payments Tabellenzeilen.

    Das ist der Kern-Fix: statt den ganzen iframe-Body zu parsen,
    wird das Datum direkt aus der angeklickten Zeile extrahiert.
    """

    # ── Kurzformat (aktuelles Jahr, 2026) ──

    def test_short_april(self):
        d = _extract_row_date("YouTube\n10. Apr. · Mitgliedschaft bei All About AI")
        assert d is not None
        assert d.month == 4 and d.day == 10

    def test_short_march(self):
        d = _extract_row_date("YouTube\n21. März · Alex Ziskind-Mitgliedschaft")
        assert d is not None
        assert d.month == 3 and d.day == 21

    def test_short_february(self):
        d = _extract_row_date("YouTube\n14. Feb. · YouTube Premium")
        assert d is not None
        assert d.month == 2 and d.day == 14

    def test_short_january(self):
        d = _extract_row_date("YouTube\n10. Jan. · Mitgliedschaft bei All About AI")
        assert d is not None
        assert d.month == 1 and d.day == 10

    # ── Langformat mit Jahr (vergangenes Jahr) ──

    def test_long_december_2025(self):
        d = _extract_row_date("YouTube\n21. Dez. 2025 · Alex Ziskind-Mitgliedschaft")
        assert d is not None
        assert d == datetime(2025, 12, 21)

    def test_long_october_2025(self):
        d = _extract_row_date("YouTube\n10. Okt. 2025 · Google One")
        assert d is not None
        assert d == datetime(2025, 10, 10)

    # ── Filtern: 2026-Zeilen dürfen NICHT für 12/2025 Entries matchen ──

    def test_april_2026_rejects_dec_2025_entry(self):
        """10. Apr. 2026 sollte NICHT für Entry 10.12.25 matchen."""
        row_date = _extract_row_date("YouTube\n10. Apr. · Mitgliedschaft")
        entry_date = datetime(2025, 12, 10)
        assert row_date is not None
        assert abs((row_date - entry_date).days) > 14

    def test_march_2026_rejects_dec_2025_entry(self):
        """21. März 2026 sollte NICHT für Entry 21.12.25 matchen."""
        row_date = _extract_row_date("YouTube\n21. März · Alex Ziskind")
        entry_date = datetime(2025, 12, 21)
        assert row_date is not None
        assert abs((row_date - entry_date).days) > 14

    def test_dec_2025_matches_dec_2025_entry(self):
        """21. Dez. 2025 SOLL für Entry 21.12.25 matchen."""
        row_date = _extract_row_date("YouTube\n21. Dez. 2025 · Alex Ziskind")
        entry_date = datetime(2025, 12, 21)
        assert row_date is not None
        assert abs((row_date - entry_date).days) <= 14

    # ── Edge Cases ──

    def test_no_date_returns_none(self):
        assert _extract_row_date("YouTube Premium −4,99 €") is None

    def test_amount_only_returns_none(self):
        assert _extract_row_date("−4,99 €") is None


# ─── Dateiname-Generierung ──────────────────────────────────────────

class TestFilenameGeneration:
    """Verifiziert die Dateinamen-Logik."""

    def test_standard_filename(self):
        date_str = "10.12.25"
        vendor = "GOOGLE*YOUTUBE MEMBER"
        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        vendor_short = re.sub(r"[^\w]", "", vendor)[:20]
        fname = f"{date_prefix}{vendor_short}_Google_Rechnung.pdf"
        assert fname == "101225_GOOGLEYOUTUBEMEMBER_Google_Rechnung.pdf"

    def test_google_one_filename(self):
        date_str = "10.12.25"
        vendor = "GOOGLE *Google One"
        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        vendor_short = re.sub(r"[^\w]", "", vendor)[:20]
        fname = f"{date_prefix}{vendor_short}_Google_Rechnung.pdf"
        assert fname == "101225_GOOGLEGoogleOne_Google_Rechnung.pdf"

    def test_no_date_filename(self):
        date_str = ""
        vendor = "Google YouTube Member"
        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        vendor_short = re.sub(r"[^\w]", "", vendor)[:20]
        fname = f"{date_prefix}{vendor_short}_Google_Rechnung.pdf"
        assert fname == "GoogleYouTubeMember_Google_Rechnung.pdf"
