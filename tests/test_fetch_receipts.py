"""
Tests für fetch_receipts.py
===========================
Testet Keyword-Extraktion, Scoring und Outlook-Suche einzeln.

Standalone:
    pytest tests/test_fetch_receipts.py -v
    pytest tests/test_fetch_receipts.py -v -k "test_search" --live  # mit echtem Graph API
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fetch_receipts import _get_search_keywords, _score_candidate


# ─── Keyword-Extraktion ────────────────────────────────────

class TestKeywords:
    def test_anthropic(self):
        assert "anthropic" in _get_search_keywords("ANTHROPIC")

    def test_openai(self):
        assert "openai" in _get_search_keywords("OPENAI")

    def test_amazon(self):
        assert "amazon" in _get_search_keywords("AMZN Mktp DE")

    def test_amazon_de(self):
        assert "amazon" in _get_search_keywords("Amazon.de*VD9CW3WR5")

    def test_nyt(self):
        kw = _get_search_keywords("THE NEW YORK TIMES")
        assert "nytimes" in kw

    def test_wsj_dj(self):
        kw = _get_search_keywords("DJ*WSJ-EMEA")
        assert "wsj" in kw

    def test_wsj_d_j(self):
        kw = _get_search_keywords("D J")
        assert "wsj" in kw

    def test_paypal_fraenk(self):
        kw = _get_search_keywords("PAYPAL *FRAENK")
        assert "fraenk" in kw

    def test_figma(self):
        assert "figma" in _get_search_keywords("FIGMA")

    def test_heise(self):
        assert "heise" in _get_search_keywords("Heise Medien GmbH & Co")

    def test_unknown_vendor_fallback(self):
        kw = _get_search_keywords("SOME RANDOM VENDOR")
        assert len(kw) > 0
        assert kw[0] != ""


# ─── Scoring ───────────────────────────────────────────────

def _msg(subject, sender="test@example.com"):
    return {"subject": subject, "from": {"emailAddress": {"address": sender}}}


class TestScoring:
    def test_receipt_email_scores_high(self):
        score = _score_candidate(
            _msg("Your receipt from Anthropic, PBC #2578", "receipts@anthropic.com"),
            "anthropic", 103.21)
        assert score >= 5

    def test_invoice_email_scores_high(self):
        score = _score_candidate(
            _msg("Hetzner Online GmbH - Rechnung 087000843541", "billing@hetzner.com"),
            "hetzner", 35.56)
        assert score >= 5

    def test_newsletter_scores_negative(self):
        score = _score_candidate(
            _msg("FN-Watchlist: ALPHABET INC CL C", "noreply@news.finanznachrichten.de"),
            "paypal", 10.0)
        assert score < 0

    def test_medium_article_scores_negative(self):
        score = _score_candidate(
            _msg("I Quit ChatGPT for Claude. Here's Why", "noreply@medium.com"),
            "chatgpt", 20.68)
        assert score < 0

    def test_bitkom_event_scores_low(self):
        score = _score_candidate(
            _msg("Bitkom & Payment & Banking laden am 10. November zur DigiFin", "x@bitkom-events.de"),
            "adobe", 66.45)
        assert score < 2

    def test_paypal_zahlung_scores_high(self):
        score = _score_candidate(
            _msg("Sie haben eine Zahlung an fraenk", "service@paypal.de"),
            "fraenk", 10.0)
        assert score >= 5

    def test_heise_newsletter_scores_negative(self):
        score = _score_candidate(
            _msg("heise+ Update: Apple bringt Neo", "newsletter@heise.de"),
            "heise", 12.95)
        assert score < 2

    def test_amount_match_bonus(self):
        score_with = _score_candidate(_msg("Invoice for 103,21 EUR"), "test", 103.21)
        score_without = _score_candidate(_msg("Invoice for something"), "test", 103.21)
        assert score_with > score_without

    def test_billing_sender_bonus(self):
        score_billing = _score_candidate(_msg("Your invoice", "billing@vendor.com"), "vendor", 10.0)
        score_generic = _score_candidate(_msg("Your invoice", "info@vendor.com"), "vendor", 10.0)
        assert score_billing > score_generic


# ─── Live-Tests (nur mit --live Flag) ──────────────────────

@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


@pytest.fixture
def graph_token(live):
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from expense_bot import get_graph_token
    return get_graph_token()


class TestLiveSearch:
    def test_find_belege_folder(self, graph_token):
        from fetch_receipts import find_mail_folder
        folder_id = find_mail_folder(graph_token, "Belege")
        assert folder_id is not None

    def test_search_anthropic(self, graph_token):
        from fetch_receipts import find_mail_folder, search_receipts_for_entry
        folder_id = find_mail_folder(graph_token, "Belege")
        entry = {"vendor": "ANTHROPIC", "amount": 103.21, "date": "06.03.26"}
        candidates = search_receipts_for_entry(graph_token, [folder_id], entry)
        assert len(candidates) > 0
        assert candidates[0].get("_score", 0) >= 2

    def test_search_no_result(self, graph_token):
        from fetch_receipts import find_mail_folder, search_receipts_for_entry
        folder_id = find_mail_folder(graph_token, "Belege")
        entry = {"vendor": "NONEXISTENT_VENDOR_XYZ", "amount": 99999.99, "date": "01.01.20"}
        candidates = search_receipts_for_entry(graph_token, [folder_id], entry)
        assert len(candidates) == 0
