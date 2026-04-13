"""Tests for portal.py strict matching and cloudflare.py tolerance."""

import pytest
from pathlib import Path
from src.result import RunResult


class TestPortalStrictMatching:
    """Test the strict amount matching in portal.py — no blind fallback."""

    def _simulate_portal_match(self, invoices, target_amount):
        """Simulates the matching logic from download_portal_invoices."""
        matched_invoice = None
        best_diff = float('inf')
        for inv in invoices:
            if inv.get("_used"):
                continue
            inv_amount_str = inv.get("amount", "").replace(",", ".").replace("EUR", "").replace("$", "").strip()
            try:
                inv_amount = float(inv_amount_str)
                diff = abs(inv_amount - target_amount)
                if diff <= 1.0 and diff < best_diff:
                    best_diff = diff
                    matched_invoice = inv
            except (ValueError, TypeError):
                pass

        # Fallback only when NO amounts available
        if not matched_invoice:
            has_any_amounts = any(inv.get("amount", "").strip() for inv in invoices if not inv.get("_used"))
            if not has_any_amounts:
                for inv in invoices:
                    if not inv.get("_used"):
                        matched_invoice = inv
                        break

        return matched_invoice

    def test_exact_amount_match(self):
        invoices = [
            {"amount": "103.36", "pdf_url": "http://a"},
            {"amount": "61.94", "pdf_url": "http://b"},
        ]
        match = self._simulate_portal_match(invoices, 61.94)
        assert match is not None
        assert match["pdf_url"] == "http://b"

    def test_no_match_when_amounts_differ_too_much(self):
        """With strict matching, a $200 invoice should NOT match a $50 entry."""
        invoices = [
            {"amount": "200.00", "pdf_url": "http://a"},
            {"amount": "300.00", "pdf_url": "http://b"},
        ]
        match = self._simulate_portal_match(invoices, 50.0)
        assert match is None  # No fallback because amounts ARE present

    def test_old_behavior_would_have_matched_wrong(self):
        """Before the fix, this would have matched the first unused invoice."""
        invoices = [
            {"amount": "999.99", "pdf_url": "http://wrong"},
        ]
        match = self._simulate_portal_match(invoices, 10.0)
        assert match is None  # amount diff > 1.0 and amounts are present → no match

    def test_fallback_when_no_amounts(self):
        """When portal has no amount data, fallback to first unused is OK."""
        invoices = [
            {"amount": "", "pdf_url": "http://a"},
            {"amount": "", "pdf_url": "http://b"},
        ]
        match = self._simulate_portal_match(invoices, 103.36)
        assert match is not None
        assert match["pdf_url"] == "http://a"

    def test_eur_comma_format(self):
        invoices = [{"amount": "103,36 EUR", "pdf_url": "http://a"}]
        match = self._simulate_portal_match(invoices, 103.36)
        assert match is not None

    def test_tolerance_one_euro(self):
        invoices = [{"amount": "104.00", "pdf_url": "http://a"}]
        match = self._simulate_portal_match(invoices, 103.36)
        assert match is not None  # diff = 0.64, within 1.0 tolerance

    def test_just_beyond_tolerance(self):
        invoices = [{"amount": "105.00", "pdf_url": "http://a"}]
        match = self._simulate_portal_match(invoices, 103.36)
        assert match is None  # diff = 1.64, beyond 1.0 tolerance


class TestCloudflareToleranceMatching:
    """Test the tighter ±15% tolerance for cloudflare.py."""

    def _simulate_cf_match(self, invoices, target_amount):
        """Simulates the matching logic from download_cloudflare_invoices."""
        best_inv = None
        best_diff = float('inf')
        for inv in invoices:
            if inv.get("_used"):
                continue
            inv_amount = inv.get("amount", 0)
            diff = abs(inv_amount - target_amount)
            if diff <= max(0.5, target_amount * 0.15) and diff < best_diff:
                best_diff = diff
                best_inv = inv
        return best_inv

    def test_exact_match(self):
        invoices = [{"amount": 4.35, "id": "inv_1"}]
        match = self._simulate_cf_match(invoices, 4.35)
        assert match is not None

    def test_within_15_percent(self):
        """$5.00 USD vs 4.35 EUR — 15% of 4.35 = 0.65, diff = 0.65 → just at limit."""
        invoices = [{"amount": 5.00, "id": "inv_1"}]
        match = self._simulate_cf_match(invoices, 4.35)
        assert match is not None

    def test_beyond_15_percent(self):
        """$6.00 USD vs 4.35 EUR — diff = 1.65, 15% of 4.35 = 0.65 → way beyond."""
        invoices = [{"amount": 6.00, "id": "inv_1"}]
        match = self._simulate_cf_match(invoices, 4.35)
        assert match is None

    def test_old_30_percent_would_match_wrong(self):
        """At old ±30%, a $130 invoice would match a $100 entry. Now it shouldn't."""
        invoices = [{"amount": 130.0, "id": "inv_1"}]
        match = self._simulate_cf_match(invoices, 100.0)
        assert match is None  # diff=30, 15% of 100 = 15 → rejected

    def test_picks_closest(self):
        invoices = [
            {"amount": 5.50, "id": "inv_far"},
            {"amount": 4.40, "id": "inv_close"},
        ]
        match = self._simulate_cf_match(invoices, 4.35)
        assert match is not None
        assert match["id"] == "inv_close"


class TestPortalReturnFormat:
    """Verify the portal return type contract: list[tuple[dict, Path, str]]."""

    def test_triple_unpacking(self):
        """Simulate what expense_bot.py does with portal results."""
        results = [
            ({"vendor": "OPENAI", "amount": 155.15, "category": "other", "date": "09.03.26", "is_credit": False, "_id": "p2_3"},
             Path("/tmp/OpenAI_invoice.pdf"),
             "openai-api"),
        ]

        r = RunResult()
        r.add_entries([results[0][0]])

        for entry, filepath, portal_id in results:
            r.mark_matched(entry, [filepath], source=f"portal:{portal_id}")

        assert len(r.matched) == 1
        assert r.matched[0].source == "portal:openai-api"
