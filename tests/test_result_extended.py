"""Extended tests for result.py — ID matching, FX/credit auto-skip, ambiguous fallback."""

import pytest
from pathlib import Path
from src.result import RunResult, EntryResult


class TestFindEntry:
    def test_find_by_id(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "A", "amount": 10, "category": "other", "is_credit": False, "_id": "p1_0"},
            {"vendor": "B", "amount": 20, "category": "other", "is_credit": False, "_id": "p1_1"},
        ])
        found = r.find_entry("p1_1")
        assert found is not None
        assert found.vendor == "B"

    def test_find_nonexistent_returns_none(self):
        r = RunResult()
        r.add_entries([{"vendor": "A", "amount": 10, "category": "other", "is_credit": False, "_id": "p1_0"}])
        assert r.find_entry("p99_99") is None

    def test_find_empty_id_returns_none(self):
        r = RunResult()
        r.add_entries([{"vendor": "A", "amount": 10, "category": "other", "is_credit": False}])
        assert r.find_entry("") is None


class TestIdBasedMatching:
    def test_mark_matched_by_id(self):
        r = RunResult()
        entries = [
            {"vendor": "ANTHROPIC", "amount": 103.36, "category": "other", "date": "03.03.26", "is_credit": False, "_id": "p1_0"},
            {"vendor": "ANTHROPIC", "amount": 103.60, "category": "other", "date": "04.03.26", "is_credit": False, "_id": "p1_3"},
        ]
        r.add_entries(entries)
        # Match the second one by ID
        r.mark_matched(entries[1], [Path("/tmp/a.pdf")], source="outlook")
        assert r.entries[0].status == "pending"
        assert r.entries[1].status == "matched"

    def test_mark_link_only_by_id(self):
        r = RunResult()
        entries = [
            {"vendor": "X", "amount": 10, "category": "other", "date": "01.03.26", "is_credit": False, "_id": "p1_0"},
        ]
        r.add_entries(entries)
        r.mark_link_only(entries[0], "https://example.com", source="outlook")
        assert r.entries[0].status == "link_only"
        assert r.entries[0].receipt_url == "https://example.com"

    def test_mark_unmatched_by_id(self):
        r = RunResult()
        entries = [
            {"vendor": "X", "amount": 10, "category": "other", "date": "01.03.26", "is_credit": False, "_id": "p1_0"},
        ]
        r.add_entries(entries)
        r.mark_unmatched(entries[0], note="Download failed")
        assert r.entries[0].status == "unmatched"
        assert r.entries[0].note == "Download failed"


class TestAmbiguousFallback:
    def test_ambiguous_vendor_amount_date_does_not_match(self):
        """When multiple entries have same vendor+amount+date, fallback should NOT match."""
        r = RunResult()
        entries = [
            {"vendor": "ANTHROPIC", "amount": 103.36, "category": "other", "date": "03.03.26", "is_credit": False},
            {"vendor": "ANTHROPIC", "amount": 103.36, "category": "other", "date": "03.03.26", "is_credit": False},
        ]
        r.add_entries(entries)
        # Try to match without ID — should NOT match because ambiguous
        fake_entry = {"vendor": "ANTHROPIC", "amount": 103.36, "date": "03.03.26"}
        r.mark_matched(fake_entry, [Path("/tmp/a.pdf")], source="test")
        # Both should remain pending since the fallback finds 2 candidates
        assert r.entries[0].status == "pending"
        assert r.entries[1].status == "pending"

    def test_unique_vendor_amount_date_matches(self):
        """When only one entry matches vendor+amount+date, fallback works."""
        r = RunResult()
        entries = [
            {"vendor": "ANTHROPIC", "amount": 103.36, "category": "other", "date": "03.03.26", "is_credit": False},
            {"vendor": "OPENAI", "amount": 61.94, "category": "other", "date": "03.03.26", "is_credit": False},
        ]
        r.add_entries(entries)
        fake_entry = {"vendor": "ANTHROPIC", "amount": 103.36, "date": "03.03.26"}
        r.mark_matched(fake_entry, [Path("/tmp/a.pdf")], source="test")
        assert r.entries[0].status == "matched"
        assert r.entries[1].status == "pending"


class TestAutoSkip:
    def test_fx_fee_auto_skipped(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "Waehrungsumrechnung", "amount": 2.07, "category": "fx_fee", "date": "03.03.26", "is_credit": False},
        ])
        assert r.entries[0].status == "skipped"
        assert "FX" in r.entries[0].note

    def test_credit_auto_skipped(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "DB Vertrieb GmbH", "amount": 210.70, "category": "db", "date": "01.04.26", "is_credit": True},
        ])
        assert r.entries[0].status == "skipped"
        assert "Gutschrift" in r.entries[0].note

    def test_normal_debit_stays_pending(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "ANTHROPIC", "amount": 103.36, "category": "other", "date": "03.03.26", "is_credit": False},
        ])
        assert r.entries[0].status == "pending"


class TestFxFeeProperties:
    def test_fx_fee_entries_property(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "A", "amount": 100, "category": "other", "is_credit": False},
            {"vendor": "FX", "amount": 2, "category": "fx_fee", "is_credit": False},
            {"vendor": "FX2", "amount": 3, "category": "fx_fee", "is_credit": False},
        ])
        assert len(r.fx_fee_entries) == 2

    def test_skipped_property(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "A", "amount": 100, "category": "other", "is_credit": False},
            {"vendor": "FX", "amount": 2, "category": "fx_fee", "is_credit": False},
            {"vendor": "C", "amount": 50, "category": "db", "is_credit": True},
        ])
        assert len(r.skipped) == 2  # FX + credit

    def test_total_debits_excludes_fx(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "A", "amount": 100, "category": "other", "is_credit": False},
            {"vendor": "FX", "amount": 2, "category": "fx_fee", "is_credit": False},
        ])
        assert r.total_debits == 1

    def test_non_db_excludes_fx(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "A", "amount": 100, "category": "other", "is_credit": False},
            {"vendor": "FX", "amount": 2, "category": "fx_fee", "is_credit": False},
        ])
        assert len(r.non_db_entries) == 1

    def test_summary_includes_fx_count(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "A", "amount": 100, "category": "other", "is_credit": False},
            {"vendor": "FX", "amount": 2, "category": "fx_fee", "is_credit": False},
        ])
        s = r.summary()
        assert "FX" in s
        assert "1" in s  # 1 FX fee

    def test_unmatched_excludes_fx_and_credits(self):
        r = RunResult()
        r.add_entries([
            {"vendor": "A", "amount": 100, "category": "other", "is_credit": False},
            {"vendor": "FX", "amount": 2, "category": "fx_fee", "is_credit": False},
            {"vendor": "C", "amount": 50, "category": "db", "is_credit": True},
        ])
        # Only A should be in unmatched (FX and credit are excluded)
        assert len(r.unmatched) == 1
        assert r.unmatched[0].vendor == "A"


class TestIsFxFee:
    def test_is_fx_fee_true(self):
        er = EntryResult(entry={"category": "fx_fee"})
        assert er.is_fx_fee

    def test_is_fx_fee_false(self):
        er = EntryResult(entry={"category": "other"})
        assert not er.is_fx_fee

    def test_entry_id_property(self):
        er = EntryResult(entry={"_id": "p3_12"})
        assert er.entry_id == "p3_12"

    def test_entry_id_empty_when_missing(self):
        er = EntryResult(entry={})
        assert er.entry_id == ""
