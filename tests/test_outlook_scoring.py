"""Tests for Outlook email scoring and keyword extraction.

Covers _get_search_keywords() and _score_candidate() including:
- WL*GOOGLE vendor keyword fix
- Own-domain sender malus
- Noun Project / FIGMA false-positive prevention
- Newsletter penalties
- Attachment bonuses
"""

from unittest.mock import patch

import pytest

from src.outlook import _get_search_keywords, _score_candidate


# ─── _get_search_keywords tests ────────────────────────────────────


class TestGetSearchKeywordsSpecific:
    """Test specific vendor keyword mappings."""

    def test_wl_google_returns_google_keywords(self):
        """Bug fix: WL*GOOGLE should NOT fall through to 'wl' fallback."""
        kw = _get_search_keywords("WL*GOOGLE YouTube Premium")
        assert any("google" in k for k in kw)
        # Must not contain "wl" as a standalone keyword
        assert "wl" not in kw

    def test_wl_google_contains_play_or_youtube(self):
        kw = _get_search_keywords("WL*GOOGLE YouTube Premium")
        assert any("youtube" in k or "play" in k for k in kw)

    def test_figma_returns_figma(self):
        kw = _get_search_keywords("FIGMA, SAN FRANCISCO")
        assert "figma" in kw

    def test_anthropic_returns_anthropic_or_claude(self):
        kw = _get_search_keywords("ANTHROPIC, SAN FRANCISCO")
        assert "anthropic" in kw or "claude" in kw

    def test_openai_chatgpt_contains_chatgpt(self):
        kw = _get_search_keywords("OPENAI *CHATGPT SUBSCR, SAN FRANCISCO")
        assert any("chatgpt" in k for k in kw)

    def test_holiday_inn_contains_hiex(self):
        kw = _get_search_keywords("Holiday Inn Express, Karlsruhe")
        assert any("hiex" in k for k in kw)

    def test_amzn_mktp_returns_amazon(self):
        kw = _get_search_keywords("AMZN Mktp DE*FQ6IC9W85")
        assert "amazon" in kw

    def test_perplexity(self):
        kw = _get_search_keywords("PERPLEXITY.AI, SAN FRANCISCO")
        assert any("perplexity" in k for k in kw)

    def test_nounproject(self):
        kw = _get_search_keywords("THENOUNPROJECT.COM, LOS ANGELES")
        assert any("noun" in k for k in kw)


class TestGetSearchKeywordsFallback:
    """Test the fallback logic for unknown vendors."""

    def test_unknown_vendor_uses_first_word(self):
        kw = _get_search_keywords("SOMECOMPANY GmbH, Berlin")
        assert len(kw) >= 1
        assert kw[0] == "somecompany"

    def test_short_vendor_after_star_uses_split(self):
        """Vendor like 'AB*SOMETHING' where AB is too short after regex."""
        kw = _get_search_keywords("AB*LONGNAME, CITY")
        # Fallback should produce something, not "ab" (too short)
        assert len(kw) >= 1
        assert all(len(k) >= 2 for k in kw)


# ─── _score_candidate tests ───────────────────────────────────────


def _make_msg(subject="", sender_email="", has_attachments=False):
    """Helper to create a minimal message dict."""
    return {
        "subject": subject,
        "from": {"emailAddress": {"address": sender_email}},
        "hasAttachments": has_attachments,
    }


class TestScoreCandidateVendorMatch:
    """Vendor keyword in subject or sender should boost score."""

    def test_vendor_in_subject_gives_high_score(self):
        msg = _make_msg(subject="Your Anthropic invoice for March", sender_email="billing@anthropic.com")
        score = _score_candidate(msg, "anthropic", 50.0)
        # vendor in subject (+3) + vendor in sender (+3) + receipt term (+2) + billing sender (+2) = 10
        assert score >= 8

    def test_vendor_in_sender_only(self):
        msg = _make_msg(subject="Payment confirmation", sender_email="receipts@anthropic.com")
        score = _score_candidate(msg, "anthropic", 50.0)
        # vendor in sender (+3), no vendor in subject, no receipt term match for "payment confirmation"
        # Actually "payment" is not directly in RECEIPT_TERMS but let's check...
        assert score >= 3

    def test_vendor_in_subject_only(self):
        msg = _make_msg(subject="Anthropic subscription renewed", sender_email="noreply@stripe.com")
        score = _score_candidate(msg, "anthropic", 50.0)
        # vendor in subject (+3) + "subscription" receipt term (+2)
        assert score >= 5

    def test_no_vendor_match_low_score(self):
        msg = _make_msg(subject="Your order confirmation", sender_email="noreply@somestore.com")
        score = _score_candidate(msg, "anthropic", 50.0)
        # No vendor match, "order" is a receipt term (+2)
        assert score <= 3


class TestScoreCandidateReceiptTerms:
    """Receipt/billing terms in subject should add moderate score."""

    def test_invoice_in_subject(self):
        msg = _make_msg(subject="Invoice #12345", sender_email="billing@example.com")
        score = _score_candidate(msg, "unknown_vendor", 50.0)
        # "invoice" receipt term (+2) + billing sender (+2)
        assert score >= 4

    def test_receipt_in_subject(self):
        msg = _make_msg(subject="Receipt for your payment", sender_email="noreply@example.com")
        score = _score_candidate(msg, "unknown_vendor", 50.0)
        assert score >= 2

    def test_amount_in_subject(self):
        msg = _make_msg(subject="Payment of 49,99 EUR", sender_email="noreply@example.com")
        score = _score_candidate(msg, "unknown_vendor", 49.99)
        # "payment for" won't match "payment of", but amount (+2)
        assert score >= 2


class TestScoreCandidateOwnDomain:
    """Bug fix: emails from own domain should get heavy penalty."""

    @patch("src.outlook.OWN_EMAIL_DOMAIN", "thinktecture.com")
    def test_own_domain_sender_gets_malus(self):
        msg = _make_msg(
            subject="Re: Perplexity subscription issue",
            sender_email="christian.weyer@thinktecture.com",
        )
        score = _score_candidate(msg, "perplexity", 20.0)
        # vendor in subject (+3) + own domain malus (-8) = -5
        assert score < 0

    @patch("src.outlook.OWN_EMAIL_DOMAIN", "thinktecture.com")
    def test_own_domain_outgoing_to_perplexity(self):
        """The exact Perplexity bug: user's OWN email to Perplexity support."""
        msg = _make_msg(
            subject="Perplexity Pro subscription billing question",
            sender_email="christian.weyer@thinktecture.com",
            has_attachments=False,
        )
        score = _score_candidate(msg, "perplexity", 20.0)
        # vendor in subject (+3), "subscription" receipt term (+2),
        # own domain (-8) → total should be negative
        assert score < 0

    @patch("src.outlook.OWN_EMAIL_DOMAIN", "thinktecture.com")
    def test_external_sender_no_malus(self):
        msg = _make_msg(
            subject="Your Perplexity Pro receipt",
            sender_email="billing@perplexity.ai",
        )
        score = _score_candidate(msg, "perplexity", 20.0)
        # vendor in subject (+3) + vendor in sender (+3) + receipt term (+2) + billing sender (+2) = 10
        assert score >= 8

    @patch("src.outlook.OWN_EMAIL_DOMAIN", "")
    def test_empty_domain_config_no_crash(self):
        """OWN_EMAIL_DOMAIN empty should not crash or penalize."""
        msg = _make_msg(subject="Test", sender_email="user@thinktecture.com")
        score = _score_candidate(msg, "test", 10.0)
        # Should not get penalized when config is empty
        assert score >= 0


class TestScoreCandidateNewsletter:
    """Newsletter emails should be penalized."""

    def test_newsletter_sender(self):
        msg = _make_msg(subject="Tech Weekly", sender_email="newsletter@example.com")
        score = _score_candidate(msg, "example", 10.0)
        assert score < 0

    def test_substack_sender(self):
        msg = _make_msg(subject="New post: AI updates", sender_email="noreply@substack.com")
        score = _score_candidate(msg, "substack", 10.0)
        assert score < 0

    def test_breaking_news_subject(self):
        msg = _make_msg(subject="Breaking News: Market Update", sender_email="news@wsj.com")
        score = _score_candidate(msg, "wsj", 10.0)
        assert score < 0


class TestScoreCandidateAttachment:
    """Attachment bonus should only apply when there's already a match."""

    def test_attachment_with_vendor_match_gives_bonus(self):
        msg_without = _make_msg(subject="Figma invoice", sender_email="billing@figma.com")
        msg_with = _make_msg(subject="Figma invoice", sender_email="billing@figma.com", has_attachments=True)
        score_without = _score_candidate(msg_without, "figma", 15.0)
        score_with = _score_candidate(msg_with, "figma", 15.0)
        assert score_with > score_without

    def test_attachment_alone_no_bonus(self):
        """Attachment without any keyword match should NOT add bonus."""
        msg = _make_msg(subject="Random email", sender_email="someone@random.com", has_attachments=True)
        score = _score_candidate(msg, "figma", 15.0)
        # No vendor match, no receipt terms → score 0, attachment check requires score > 0
        assert score == 0


class TestNounProjectFigmaFalsePositive:
    """Bug fix: Noun Project invoice should NOT match for FIGMA vendor."""

    def test_noun_project_email_for_figma_low_score(self):
        """Noun Project email found via Graph $search for 'figma' (body mention).
        Subject and sender don't contain 'figma', so score should be low."""
        msg = _make_msg(
            subject="Your Noun Project Invoice - March 2026",
            sender_email="billing@thenounproject.com",
            has_attachments=True,
        )
        score = _score_candidate(msg, "figma", 9.99)
        # "invoice" in subject (+2) + billing sender (+2) + attachment (+1, since score>0) = 5
        # But "figma" NOT in subject and NOT in sender → no vendor bonus
        assert score <= 5
        # This email should NOT be selected for FIGMA since vendor doesn't match in subject/sender

    def test_real_figma_email_high_score(self):
        """The real Figma receipt email should score much higher."""
        msg = _make_msg(
            subject="Receipt for subscription payment Mar 11, 2026 - Figma",
            sender_email="noreply@figma.com",
            has_attachments=True,
        )
        score = _score_candidate(msg, "figma", 9.99)
        # vendor in subject (+3) + vendor in sender (+3) + receipt term (+2) + attachment (+1) = 9
        assert score >= 8

    def test_figma_beats_noun_project(self):
        """Figma receipt should always outscore Noun Project invoice for 'figma' search."""
        noun_msg = _make_msg(
            subject="Your Noun Project Invoice - March 2026",
            sender_email="billing@thenounproject.com",
            has_attachments=True,
        )
        figma_msg = _make_msg(
            subject="Receipt for subscription payment Mar 11, 2026 - Figma",
            sender_email="noreply@figma.com",
            has_attachments=True,
        )
        noun_score = _score_candidate(noun_msg, "figma", 9.99)
        figma_score = _score_candidate(figma_msg, "figma", 9.99)
        assert figma_score > noun_score
        assert figma_score - noun_score >= 4  # Clear gap


class TestScoreCandidateBotEmail:
    """Bot-generated emails should be heavily penalized."""

    def test_automatisch_tag(self):
        msg = _make_msg(subject="[automatisch] Belege 5/10", sender_email="bot@example.com")
        score = _score_candidate(msg, "anthropic", 100.0)
        assert score < 0

    def test_expense_bot(self):
        msg = _make_msg(subject="expense bot: new receipt", sender_email="bot@example.com")
        score = _score_candidate(msg, "anthropic", 100.0)
        assert score < 0
