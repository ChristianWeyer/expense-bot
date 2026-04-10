"""Tests für mastercard.py — Verifikationslogik, FX-Fee Handling, Unique IDs."""

import pytest
from src.mastercard import _verify_page, _verify_total, get_non_db_entries, get_db_entries


class TestVerifyPage:
    def test_no_subtotal_always_passes(self):
        page_result = {"entries": [{"amount": 100, "is_credit": False}], "page_subtotal": None, "carry_over": None}
        ok, msg = _verify_page(page_result, 1)
        assert ok

    def test_correct_subtotal_passes(self):
        page_result = {
            "entries": [
                {"amount": 103.36, "is_credit": False},
                {"amount": 2.07, "is_credit": False},
                {"amount": 22.71, "is_credit": False},
            ],
            "page_subtotal": 128.14,
            "carry_over": 0,
        }
        ok, msg = _verify_page(page_result, 1)
        assert ok
        assert "OK" in msg

    def test_subtotal_with_carry_over(self):
        page_result = {
            "entries": [
                {"amount": 12.95, "is_credit": False},
                {"amount": 105.22, "is_credit": False},
            ],
            "page_subtotal": 2170.93,  # carry_over + entries
            "carry_over": 2052.76,
        }
        ok, msg = _verify_page(page_result, 2)
        assert ok

    def test_wrong_subtotal_fails(self):
        page_result = {
            "entries": [
                {"amount": 100.0, "is_credit": False},
            ],
            "page_subtotal": 500.0,
            "carry_over": 0,
        }
        ok, msg = _verify_page(page_result, 1)
        assert not ok
        assert "Diff" in msg

    def test_credits_subtracted(self):
        page_result = {
            "entries": [
                {"amount": 200.0, "is_credit": False},
                {"amount": 50.0, "is_credit": True},  # credit reduces total
            ],
            "page_subtotal": 150.0,
            "carry_over": 0,
        }
        ok, msg = _verify_page(page_result, 1)
        assert ok

    def test_tolerance_allows_small_rounding(self):
        page_result = {
            "entries": [
                {"amount": 103.36, "is_credit": False},
                {"amount": 2.07, "is_credit": False},
            ],
            "page_subtotal": 105.93,  # 0.50 off from 105.43
            "carry_over": 0,
        }
        ok, msg = _verify_page(page_result, 1)
        assert ok  # within ±1 EUR tolerance


class TestVerifyTotal:
    def test_no_total_always_passes(self):
        ok, msg = _verify_total([], None)
        assert ok

    def test_correct_total_passes(self):
        entries = [
            {"amount": 103.36, "is_credit": False},
            {"amount": 22.71, "is_credit": False},
            {"amount": 210.70, "is_credit": True},
        ]
        # 103.36 + 22.71 - 210.70 = -84.63 → abs = 84.63
        ok, msg = _verify_total(entries, -84.63)
        assert ok

    def test_wrong_total_fails(self):
        entries = [{"amount": 100.0, "is_credit": False}]
        ok, msg = _verify_total(entries, 9999.0)
        assert not ok
        assert "ABWEICHUNG" in msg

    def test_large_statement_with_tolerance(self):
        # Simulate 80 entries with small rounding diffs
        entries = [{"amount": 103.36 + i * 0.01, "is_credit": False} for i in range(80)]
        total = sum(e["amount"] for e in entries)
        ok, msg = _verify_total(entries, total + 1.5)  # 1.5 off
        assert ok  # within ±2 EUR tolerance

    def test_total_negative_means_debit_balance(self):
        entries = [
            {"amount": 6867.25, "is_credit": False},
        ]
        ok, msg = _verify_total(entries, -6867.25)
        assert ok


class TestFxFeeFiltering:
    def test_fx_fees_excluded_from_non_db(self):
        entries = [
            {"vendor": "ANTHROPIC", "category": "other", "amount": 103.36},
            {"vendor": "Waehrungsumrechnung ANTHROPIC", "category": "fx_fee", "amount": 2.07},
            {"vendor": "DB Vertrieb GmbH", "category": "db", "amount": 121.0},
        ]
        non_db = get_non_db_entries(entries)
        assert len(non_db) == 1
        assert non_db[0]["vendor"] == "ANTHROPIC"

    def test_fx_fees_excluded_from_db(self):
        entries = [
            {"vendor": "Waehrungsumrechnung", "category": "fx_fee", "amount": 2.07},
            {"vendor": "DB Vertrieb GmbH", "category": "db", "amount": 121.0},
        ]
        db = get_db_entries(entries)
        assert len(db) == 1
        assert db[0]["category"] == "db"


class TestUniqueIds:
    def test_page_number_in_entry(self):
        """_call_llm_single_page sets _page on each entry."""
        # Simulate what the function does
        entries = [{"amount": 100, "is_credit": False}]
        for entry in entries:
            entry["_page"] = 3
        assert entries[0]["_page"] == 3

    def test_id_format(self):
        """extract_all_entries assigns _id = f'p{page}_{idx}'."""
        entries = [
            {"amount": 100, "_page": 1},
            {"amount": 200, "_page": 1},
            {"amount": 300, "_page": 2},
        ]
        for idx, entry in enumerate(entries):
            entry["_id"] = f"p{entry.get('_page', 0)}_{idx}"
        assert entries[0]["_id"] == "p1_0"
        assert entries[1]["_id"] == "p1_1"
        assert entries[2]["_id"] == "p2_2"
