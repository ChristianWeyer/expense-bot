"""Tests for amazon.py — amount-based matching logic and entry filtering."""

import pytest

from src.amazon import _filter_amazon_entries


class TestAmazonEntryFiltering:
    """Test the entry filtering logic — imports REAL function from src/amazon.py."""

    def test_amzn_mktp_matches(self):
        entries = [{"vendor": "AMZN Mktp DE*IT5HF5H85", "amount": 126.03, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 1

    def test_amazon_de_matches(self):
        entries = [{"vendor": "Amazon.de VD9CW3W5", "amount": 21.29, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 1

    def test_amazon_de_star_matches(self):
        entries = [{"vendor": "Amazon.de*BYOT94N15", "amount": 84.00, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 1

    def test_credit_excluded(self):
        entries = [{"vendor": "AMZN Mktp DE", "amount": 10.0, "is_credit": True}]
        assert len(_filter_amazon_entries(entries)) == 0

    def test_non_amazon_excluded(self):
        entries = [{"vendor": "ANTHROPIC", "amount": 100.0, "is_credit": False}]
        assert len(_filter_amazon_entries(entries)) == 0

    def test_multiple_amazon_entries(self):
        entries = [
            {"vendor": "AMZN Mktp DE*IT5HF5H85", "amount": 126.03, "is_credit": False},
            {"vendor": "Amazon.de VD9CW3W5", "amount": 21.29, "is_credit": False},
            {"vendor": "AMZN Mktp DE*FQ6IC9W85", "amount": 8.39, "is_credit": False},
            {"vendor": "Amazon.de*BYOT94N15", "amount": 84.00, "is_credit": False},
            {"vendor": "AMZN Mktp DE*4F6XP6TO5", "amount": 4.99, "is_credit": False},
        ]
        assert len(_filter_amazon_entries(entries)) == 5


class TestAmountMatching:
    """Test the amount-based matching logic (simulated without Playwright)."""

    def test_exact_amount_match(self):
        """Best order is the one with exact amount match."""
        orders = [
            {"order_id": "A", "amount": 21.29, "_used": False},
            {"order_id": "B", "amount": 126.03, "_used": False},
            {"order_id": "C", "amount": 84.00, "_used": False},
        ]
        target_amount = 126.03

        best_order = None
        best_diff = float('inf')
        for order in orders:
            if order.get("_used"):
                continue
            if order["amount"] is not None:
                diff = abs(order["amount"] - target_amount)
                if diff < best_diff:
                    best_diff = diff
                    best_order = order

        assert best_order is not None
        assert best_order["order_id"] == "B"
        assert best_diff < 0.01

    def test_no_amount_uses_fallback(self):
        """When no orders have amounts, fallback to first unused."""
        orders = [
            {"order_id": "A", "amount": None, "_used": False},
            {"order_id": "B", "amount": None, "_used": False},
        ]
        target_amount = 126.03

        best_order = None
        best_diff = float('inf')
        for order in orders:
            if order.get("_used"):
                continue
            if order["amount"] is not None:
                diff = abs(order["amount"] - target_amount)
                if diff < best_diff:
                    best_diff = diff
                    best_order = order

        # No amount match found
        assert best_order is None or best_diff > 5.0

        # Fallback: next unused
        if best_order is None or best_diff > 5.0:
            for order in orders:
                if not order.get("_used"):
                    best_order = order
                    break

        assert best_order is not None
        assert best_order["order_id"] == "A"

    def test_used_orders_skipped(self):
        """Already used orders should not be matched again."""
        orders = [
            {"order_id": "A", "amount": 126.03, "_used": True},
            {"order_id": "B", "amount": 84.00, "_used": False},
        ]
        target_amount = 126.03

        best_order = None
        best_diff = float('inf')
        for order in orders:
            if order.get("_used"):
                continue
            if order["amount"] is not None:
                diff = abs(order["amount"] - target_amount)
                if diff < best_diff:
                    best_diff = diff
                    best_order = order

        # A is used, so should get B even though A has exact match
        assert best_order is not None
        assert best_order["order_id"] == "B"

    def test_closest_match_wins(self):
        """When multiple orders have amounts, closest to target wins."""
        orders = [
            {"order_id": "A", "amount": 100.0, "_used": False},
            {"order_id": "B", "amount": 125.50, "_used": False},
            {"order_id": "C", "amount": 200.0, "_used": False},
        ]
        target_amount = 126.03

        best_order = None
        best_diff = float('inf')
        for order in orders:
            if order.get("_used"):
                continue
            if order["amount"] is not None:
                diff = abs(order["amount"] - target_amount)
                if diff < best_diff:
                    best_diff = diff
                    best_order = order

        assert best_order["order_id"] == "B"
        assert best_diff < 1.0


class TestReturnFormat:
    """Verify the return type contract: list[tuple[dict, Path]]."""

    def test_tuple_unpacking(self):
        """Simulate what expense_bot.py does with amazon results."""
        from pathlib import Path
        from src.result import RunResult

        results = [
            ({"vendor": "AMZN Mktp", "amount": 126.03, "category": "other", "date": "12.03.26", "is_credit": False, "_id": "p2_12"},
             Path("/tmp/Amazon_123_invoice.pdf")),
            ({"vendor": "Amazon.de", "amount": 21.29, "category": "other", "date": "16.03.26", "is_credit": False, "_id": "p3_3"},
             Path("/tmp/Amazon_456_invoice.pdf")),
        ]

        run_result = RunResult()
        entries = [r[0] for r in results]
        run_result.add_entries(entries)

        for entry, filepath in results:
            run_result.mark_matched(entry, [filepath], source="amazon.de")

        assert len(run_result.matched) == 2
        assert run_result.matched[0].source == "amazon.de"
        assert len(run_result.all_files) == 2
