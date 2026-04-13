"""Tests für src/result.py — RunResult Datenstruktur."""

import pytest
from pathlib import Path
from src.result import RunResult, EntryResult


class TestEntryResult:
    def test_vendor(self):
        er = EntryResult(entry={"vendor": "ANTHROPIC", "amount": 103.36})
        assert er.vendor == "ANTHROPIC"
        assert er.amount == 103.36

    def test_is_db(self):
        er = EntryResult(entry={"category": "db"})
        assert er.is_db
        er2 = EntryResult(entry={"category": "other"})
        assert not er2.is_db

    def test_default_status(self):
        er = EntryResult(entry={})
        assert er.status == "pending"


class TestRunResult:
    def _sample_entries(self):
        return [
            {"vendor": "DB Vertrieb GmbH", "amount": 121.0, "category": "db", "booking_ref": "739359417117", "date": "04.03.26", "is_credit": False},
            {"vendor": "DB Vertrieb GmbH", "amount": 210.7, "category": "db", "booking_ref": "149405279318", "date": "01.04.26", "is_credit": True},
            {"vendor": "ANTHROPIC", "amount": 103.36, "category": "other", "date": "03.03.26", "is_credit": False},
            {"vendor": "Amazon.de", "amount": 21.29, "category": "other", "date": "16.03.26", "is_credit": False},
            {"vendor": "OPENAI", "amount": 61.94, "category": "other", "date": "03.03.26", "is_credit": False},
        ]

    def test_add_entries(self):
        r = RunResult()
        r.add_entries(self._sample_entries())
        assert len(r.entries) == 5

    def test_db_entries(self):
        r = RunResult()
        r.add_entries(self._sample_entries())
        assert len(r.db_entries) == 1  # Only debit DB

    def test_non_db_entries(self):
        r = RunResult()
        r.add_entries(self._sample_entries())
        assert len(r.non_db_entries) == 3

    def test_mark_matched(self):
        r = RunResult()
        entries = self._sample_entries()
        r.add_entries(entries)
        r.mark_matched(entries[2], [Path("/tmp/anthropic.pdf")], source="outlook")
        assert len(r.matched) == 1
        assert r.matched[0].vendor == "ANTHROPIC"
        assert r.matched[0].source == "outlook"

    def test_mark_link_only(self):
        r = RunResult()
        entries = self._sample_entries()
        r.add_entries(entries)
        r.mark_link_only(entries[4], "https://stripe.com/receipt/123", source="outlook")
        assert len(r.link_only) == 1
        assert r.link_only[0].receipt_url == "https://stripe.com/receipt/123"

    def test_unmatched_includes_pending(self):
        r = RunResult()
        r.add_entries(self._sample_entries())
        # Alles bleibt pending → unmatched zählt pending mit
        assert len(r.unmatched) == 4  # 1 DB debit + 3 non-DB debits

    def test_all_files(self):
        r = RunResult()
        entries = self._sample_entries()
        r.add_entries(entries)
        r.mark_matched(entries[0], [Path("/tmp/a.pdf")], source="bahn.de")
        r.mark_matched(entries[2], [Path("/tmp/b.pdf"), Path("/tmp/c.pdf")], source="outlook")
        assert len(r.all_files) == 3

    def test_summary(self):
        r = RunResult()
        entries = self._sample_entries()
        r.add_entries(entries)
        r.mark_matched(entries[0], [Path("/tmp/a.pdf")], source="bahn.de")
        r.mark_matched(entries[2], [Path("/tmp/b.pdf")], source="outlook")
        s = r.summary()
        assert "2/4" in s  # 2 matched out of 4 debits
        assert "2 PDFs" in s

    def test_total_debits_excludes_credits(self):
        r = RunResult()
        r.add_entries(self._sample_entries())
        assert r.total_debits == 4  # 5 entries, 1 credit


class TestEmailBody:
    def test_body_contains_all_sections(self):
        from src.mail import _build_body
        r = RunResult(mc_pdf_name="test.pdf")
        entries = [
            {"vendor": "DB Vertrieb GmbH", "amount": 121.0, "category": "db", "booking_ref": "123", "date": "04.03.26", "is_credit": False},
            {"vendor": "ANTHROPIC", "amount": 103.0, "category": "other", "date": "03.03.26", "is_credit": False},
            {"vendor": "UNKNOWN", "amount": 50.0, "category": "other", "date": "01.03.26", "is_credit": False},
        ]
        r.add_entries(entries)
        r.mark_matched(entries[0], [Path("/tmp/db.pdf")], source="bahn.de")
        r.mark_matched(entries[1], [Path("/tmp/anthropic.pdf")], source="outlook")
        for er in r.entries:
            if er.status == "pending":
                er.status = "unmatched"

        body = _build_body(r)
        assert "DB-Rechnungen" in body
        assert "Belege" in body
        assert "ANTHROPIC" in body
        assert "UNKNOWN" in body
        assert "Kein Beleg" in body
        assert "db.pdf" in body
        assert "anthropic.pdf" in body

    def test_subject_format(self):
        from src.mail import _build_subject
        r = RunResult(mc_pdf_name="abrechnung.pdf")
        entries = [
            {"vendor": "TEST", "amount": 10.0, "category": "other", "date": "01.03.26", "is_credit": False},
        ]
        r.add_entries(entries)
        r.mark_matched(entries[0], [Path("/tmp/test.pdf")], source="test")

        subject = _build_subject(r)
        assert "1/1" in subject
        assert "1 PDFs" in subject
        assert "abrechnung.pdf" in subject
        assert "⚠️" not in subject  # No unmatched

    def test_subject_warning_when_unmatched(self):
        from src.mail import _build_subject
        r = RunResult()
        entries = [
            {"vendor": "TEST", "amount": 10.0, "category": "other", "date": "01.03.26", "is_credit": False},
        ]
        r.add_entries(entries)
        for er in r.entries:
            er.status = "unmatched"

        subject = _build_subject(r)
        assert "UNVOLLSTÄNDIG" in subject
