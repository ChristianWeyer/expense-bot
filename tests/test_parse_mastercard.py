"""
Tests für parse_mastercard.py
=============================
Testet PDF-Parsing und Entry-Filterung.

Standalone:
    pytest tests/test_parse_mastercard.py -v
    pytest tests/test_parse_mastercard.py -v --live  # mit echtem GPT Vision API
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from parse_mastercard import get_net_bookings, get_db_entries, get_non_db_entries


# ─── Filterung ─────────────────────────────────────────────

SAMPLE_ENTRIES = [
    {"vendor": "DB Vertrieb GmbH", "amount": 121.0, "category": "db", "booking_ref": "739359417117", "is_credit": False},
    {"vendor": "DB Vertrieb GmbH", "amount": 210.7, "category": "db", "booking_ref": "149405279318", "is_credit": True},
    {"vendor": "ANTHROPIC", "amount": 103.36, "category": "other", "is_credit": False},
    {"vendor": "OPENAI", "amount": 61.94, "category": "other", "is_credit": False},
]


class TestFiltering:
    def test_get_db_entries(self):
        db = get_db_entries(SAMPLE_ENTRIES)
        assert len(db) == 2
        assert all(e["category"] == "db" for e in db)

    def test_get_non_db_entries(self):
        non_db = get_non_db_entries(SAMPLE_ENTRIES)
        assert len(non_db) == 2
        assert all(e["category"] != "db" for e in non_db)

    def test_get_net_bookings_filters_credits(self):
        net = get_net_bookings(SAMPLE_ENTRIES)
        assert len(net) == 3
        assert all(not e.get("is_credit") for e in net)

    def test_empty_list(self):
        assert get_db_entries([]) == []
        assert get_non_db_entries([]) == []
        assert get_net_bookings([]) == []


# ─── Live-Tests ────────────────────────────────────────────

@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


SAMPLE_PDF = Path(__file__).parent.parent / "beispiel-pdfs" / "5584xxxxxxxx4244_Abrechnung_vom_2026-04-02_Weyer_Christian.PDF"


class TestLiveParsing:
    def test_extract_db_bookings(self, live):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        from parse_mastercard import extract_db_bookings
        if not SAMPLE_PDF.exists():
            pytest.skip(f"PDF nicht vorhanden: {SAMPLE_PDF}")
        bookings = extract_db_bookings(str(SAMPLE_PDF))
        assert len(bookings) == 13
        refs = {b["booking_ref"] for b in bookings}
        assert "739359417117" in refs
        assert "818562863721" in refs

    def test_extract_all_entries(self, live):
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        from parse_mastercard import extract_all_entries
        if not SAMPLE_PDF.exists():
            pytest.skip(f"PDF nicht vorhanden: {SAMPLE_PDF}")
        entries = extract_all_entries(str(SAMPLE_PDF))
        assert len(entries) >= 80  # ~88 Einträge
        db = [e for e in entries if e.get("category") == "db"]
        assert len(db) == 13
