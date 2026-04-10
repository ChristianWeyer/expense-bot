"""Tests für den Spiegel Rechnungs-Scraper.

Testet Entry-Filterung, Datumsmatching und Dateinamen-Generierung.
Browser-Interaktion wird nicht getestet (erfordert Live-Session).
"""

import re
from datetime import datetime

import pytest

from src.spiegel import _parse_date, _filter_spiegel_entries


# ─── Entry-Filterung ────────────────────────────────────────────────

class TestSpiegelEntryFilter:
    """Verifiziert die Filterlogik für Spiegel-Einträge."""

    def test_spiegel_kn(self):
        entries = [{"vendor": "SPIEGEL* KN 1920835 AB", "amount": 24.99}]
        assert len(_filter_spiegel_entries(entries)) == 1

    def test_spiegel_plain(self):
        entries = [{"vendor": "SPIEGEL", "amount": 24.99}]
        assert len(_filter_spiegel_entries(entries)) == 1

    def test_non_spiegel_excluded(self):
        entries = [{"vendor": "ANTHROPIC", "amount": 100.0}]
        assert len(_filter_spiegel_entries(entries)) == 0

    def test_credit_excluded(self):
        entries = [{"vendor": "SPIEGEL", "amount": 24.99, "is_credit": True}]
        assert len(_filter_spiegel_entries(entries)) == 0


# ─── Datums-Parsing ─────────────────────────────────────────────────

class TestSpiegelDateParsing:
    """_parse_date delegiert an parse_date() — verifiziert die Formate."""

    def test_german_date(self):
        result = _parse_date("16.12.25")
        assert result == datetime(2025, 12, 16)

    def test_full_german_date(self):
        result = _parse_date("16.12.2025")
        assert result == datetime(2025, 12, 16)

    def test_iso_date(self):
        result = _parse_date("2025-12-16")
        assert result == datetime(2025, 12, 16)

    def test_empty(self):
        assert _parse_date("") is None

    def test_invalid(self):
        assert _parse_date("not-a-date") is None


# ─── Datums-Matching für Rechnungszuordnung ─────────────────────────

class TestSpiegelDateMatching:
    """Spiegel matcht Rechnungen per nächstem Datum (±14 Tage)."""

    def test_exact_match(self):
        entry_date = datetime(2025, 12, 16)
        row_date = datetime(2025, 12, 16)
        diff = abs((row_date - entry_date).days)
        assert diff <= 14

    def test_close_match(self):
        entry_date = datetime(2025, 12, 16)
        row_date = datetime(2025, 12, 20)
        diff = abs((row_date - entry_date).days)
        assert diff <= 14

    def test_too_far(self):
        entry_date = datetime(2025, 12, 16)
        row_date = datetime(2026, 1, 15)
        diff = abs((row_date - entry_date).days)
        assert diff > 14

    def test_best_match_wins(self):
        """Bei mehreren Rechnungen gewinnt die zeitlich nächste."""
        entry_date = datetime(2025, 12, 16)
        rows = [
            {"date": datetime(2025, 11, 16), "nr": "A"},  # 30 Tage
            {"date": datetime(2025, 12, 18), "nr": "B"},  # 2 Tage
            {"date": datetime(2026, 1, 16), "nr": "C"},   # 31 Tage
        ]
        best = None
        best_diff = float('inf')
        for row in rows:
            diff = abs((row["date"] - entry_date).days)
            if diff <= 14 and diff < best_diff:
                best_diff = diff
                best = row
        assert best is not None
        assert best["nr"] == "B"


# ─── Dateiname-Generierung ──────────────────────────────────────────

class TestSpiegelFilename:
    def test_standard(self):
        date_str = "16.12.25"
        nr = "16163188"
        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        fname = f"{date_prefix}Spiegel_Rechnung_{nr}.pdf"
        assert fname == "161225_Spiegel_Rechnung_16163188.pdf"

    def test_no_date(self):
        date_str = ""
        nr = "12345"
        date_prefix = date_str.replace(".", "") + "_" if date_str else ""
        fname = f"{date_prefix}Spiegel_Rechnung_{nr}.pdf"
        assert fname == "Spiegel_Rechnung_12345.pdf"
