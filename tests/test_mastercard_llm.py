"""Integration tests: LLM extraction from real MC PDF.

These tests call the actual GPT Vision API with the real MC PDF.
They verify full extraction, page subtotals, total balance, and yellow-mark detection.

Skipped by default. Run with:
    RUN_LLM_TESTS=1 pytest tests/test_mastercard_llm.py -v
"""

import os
import pytest
from pathlib import Path

MC_PDF = Path(__file__).parent.parent / "beispiel-pdfs" / "CW MC 03-2026_orig.pdf"

# Skip unless RUN_LLM_TESTS=1 is set
llm = pytest.mark.skipif(
    not os.environ.get("RUN_LLM_TESTS"),
    reason="Set RUN_LLM_TESTS=1 to run (costs ~$0.50, needs OPENAI_API_KEY)"
)


# ─── Expected values from visual PDF inspection ────────────────

EXPECTED_TOTAL = {
    "debits": 84,
    "credits": 4,
    "fx_fees": 46,
    "db_entries": 13,
    "final_total": 6867.25,
}

# All 25 yellow-marked entries across all 6 pages (vendor_substring, amount)
ALL_MARKED_ENTRIES = [
    # Page 1
    ("CHATGPT", 61.94),
    ("DB Vertrieb", 121.00),
    ("PAYPAL", 10.00),       # PAYPAL *FRAENK
    ("Audible", 9.95),
    # Page 2
    ("Heise", 12.95),
    ("AI FOR DEVS", 19.00),
    ("YOUTUBE MEMBER", 4.99),
    ("FIGMA", 134.05),
    ("AMZN", 126.03),
    ("NEW YORK TIMES", 2.00),
    # Page 3
    ("Amazon", 21.29),
    ("AMZN", 8.39),
    ("Amazon", 84.00),
    ("AMZN", 4.99),
    ("WSJ", 9.99),
    ("CHATGPT", 20.68),
    # Page 4
    ("PERPLEXITY", 20.68),
    ("ADOBE", 66.45),
    ("GOOGLE", 4.99),      # WL*GOOGLE YouTube Mem
    ("DB Vertrieb", 6.90),
    ("DB Vertrieb", 137.05),
    # Page 5
    ("CHATGPT", 20.69),
    ("AMZN", 239.00),
    # Page 6
    ("DB Vertrieb", 105.35),
    ("DB Vertrieb", 67.40),
]

# Entries that must NOT be marked
NOT_MARKED_SAMPLES = [
    ("ANTHROPIC", 103.36),
    ("ELEVENLABS", 22.71),
    ("RENDER.COM", 6.05),
    ("GITHUB", 173.25),
    ("CLOUDFLARE", 4.35),
    ("SPIEGEL", 24.99),
    ("Holiday Inn", 382.00),
    ("LANGCHAIN", 34.08),
    ("HUGGINGFACE", 7.87),
]


# ─── Fixtures ────────────────────────────────────────────────


@pytest.fixture(scope="module")
def full_result():
    """Run full extraction on the entire MC PDF (all 6 pages)."""
    if not MC_PDF.exists():
        pytest.skip(f"MC PDF not found: {MC_PDF}")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    from src.mastercard import extract_all_entries
    entries = extract_all_entries(str(MC_PDF), marked_only=False)
    return entries


@pytest.fixture(scope="module")
def marked_result():
    """Run marked extraction on the entire MC PDF (all 6 pages)."""
    if not MC_PDF.exists():
        pytest.skip(f"MC PDF not found: {MC_PDF}")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set")

    from src.mastercard import extract_all_entries
    entries = extract_all_entries(str(MC_PDF), marked_only=True)
    return entries


def _find_entry(entries, vendor_substr, amount, tolerance=0.05):
    """Find an entry by vendor substring and amount."""
    for e in entries:
        if (vendor_substr.upper() in e.get("vendor", "").upper()
                and abs(e.get("amount", 0) - amount) < tolerance
                and e.get("category") != "fx_fee"):
            return e
    return None


# ─── Full Extraction Tests ──────────────────────────────────


@llm
class TestFullExtraction:
    """Verify full extraction of ALL entries from the complete MC PDF."""

    def test_correct_debit_count(self, full_result):
        debits = [e for e in full_result if not e.get("is_credit") and e.get("category") != "fx_fee"]
        assert len(debits) == EXPECTED_TOTAL["debits"], (
            f"Expected {EXPECTED_TOTAL['debits']} debits, got {len(debits)}"
        )

    def test_correct_credit_count(self, full_result):
        credits = [e for e in full_result if e.get("is_credit")]
        assert len(credits) == EXPECTED_TOTAL["credits"], (
            f"Expected {EXPECTED_TOTAL['credits']} credits, got {len(credits)}"
        )

    def test_correct_fx_fee_count(self, full_result):
        fx = [e for e in full_result if e.get("category") == "fx_fee"]
        assert len(fx) == EXPECTED_TOTAL["fx_fees"], (
            f"Expected {EXPECTED_TOTAL['fx_fees']} FX fees, got {len(fx)}"
        )

    def test_correct_db_count(self, full_result):
        db = [e for e in full_result if e.get("category") == "db"]
        assert len(db) == EXPECTED_TOTAL["db_entries"], (
            f"Expected {EXPECTED_TOTAL['db_entries']} DB entries, got {len(db)}"
        )

    def test_total_balance(self, full_result):
        """Sum of all debits - credits must match the final total."""
        total = 0
        for e in full_result:
            if e.get("is_credit"):
                total += e.get("amount", 0)
            else:
                total -= e.get("amount", 0)
        assert abs(abs(total) - EXPECTED_TOTAL["final_total"]) < 0.10, (
            f"Balance: expected {EXPECTED_TOTAL['final_total']}, got {abs(total)}"
        )

    def test_all_marked_entries_present(self, full_result):
        """Every yellow-marked entry must be present in full extraction."""
        for vendor, amount in ALL_MARKED_ENTRIES:
            entry = _find_entry(full_result, vendor, amount)
            assert entry is not None, (
                f"Missing entry: {vendor} {amount} EUR"
            )

    def test_db_entries_have_booking_refs(self, full_result):
        """All DB entries must have booking references."""
        db = [e for e in full_result if e.get("category") == "db"]
        for e in db:
            ref = e.get("booking_ref")
            assert ref and len(str(ref)) >= 10, (
                f"DB entry {e.get('amount')} missing booking_ref: {ref}"
            )

    def test_unique_ids_assigned(self, full_result):
        """Every entry must have a unique _id."""
        ids = [e.get("_id") for e in full_result]
        assert all(ids), "Some entries missing _id"
        assert len(ids) == len(set(ids)), "Duplicate _ids found"


# ─── Marked Extraction Tests ──────────────────────────────────


@llm
class TestMarkedExtraction:
    """Verify yellow-mark detection across the complete MC PDF."""

    def test_returns_only_marked(self, marked_result):
        """Result should contain only the ~25 marked entries, not all 134."""
        # marked_only=True filters to only marked entries
        assert len(marked_result) < 50, (
            f"Too many entries ({len(marked_result)}), expected ~25 marked only"
        )

    def test_approximately_25_marked(self, marked_result):
        """Should find approximately 25 marked entries (allow some tolerance)."""
        count = len(marked_result)
        assert 20 <= count <= 35, (
            f"Expected ~25 marked entries, got {count}"
        )

    def test_all_expected_marked_entries_found(self, marked_result):
        """Every known yellow-marked entry must be in the result."""
        missing = []
        for vendor, amount in ALL_MARKED_ENTRIES:
            entry = _find_entry(marked_result, vendor, amount)
            if not entry:
                missing.append(f"{vendor} {amount}")
        assert len(missing) == 0, (
            f"Missing {len(missing)} marked entries: {missing}"
        )

    def test_non_marked_entries_excluded(self, marked_result):
        """Entries known to NOT be marked must NOT appear."""
        false_positives = []
        for vendor, amount in NOT_MARKED_SAMPLES:
            entry = _find_entry(marked_result, vendor, amount)
            if entry:
                false_positives.append(f"{vendor} {amount}")
        assert len(false_positives) == 0, (
            f"False positives (should NOT be marked): {false_positives}"
        )

    def test_chatgpt_entries_marked(self, marked_result):
        """All 3 ChatGPT entries are yellow-marked."""
        chatgpt = [e for e in marked_result
                   if "CHATGPT" in e.get("vendor", "").upper()]
        assert len(chatgpt) == 3, (
            f"Expected 3 ChatGPT entries, got {len(chatgpt)}: "
            f"{[(e['vendor'][:25], e['amount']) for e in chatgpt]}"
        )

    def test_db_entries_marked_count(self, marked_result):
        """5 of 13 DB entries are yellow-marked."""
        db = [e for e in marked_result if e.get("category") == "db"]
        assert len(db) == 5, (
            f"Expected 5 marked DB entries, got {len(db)}: "
            f"{[(e.get('booking_ref', '?'), e['amount']) for e in db]}"
        )

    def test_amazon_entries_marked_count(self, marked_result):
        """6 Amazon/AMZN entries are yellow-marked."""
        amzn = [e for e in marked_result
                if any(k in e.get("vendor", "").upper() for k in ["AMAZON", "AMZN"])]
        assert len(amzn) == 6, (
            f"Expected 5 Amazon entries, got {len(amzn)}: "
            f"{[(e['vendor'][:25], e['amount']) for e in amzn]}"
        )
