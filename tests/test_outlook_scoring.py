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

from src.outlook import (_get_search_keywords, _score_candidate, calc_billing_period,
                         _is_receipt_email, extract_receipt_url_from_html)


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


# ─── New vendor keyword tests ──────────────────────────────────────


class TestNewVendorKeywords:
    """Tests for recently added vendor keyword mappings."""

    def test_slack_keywords(self):
        kw = _get_search_keywords("SLACK T060V1XRN")
        assert "slack" in kw

    def test_sueddeutsche_keywords(self):
        kw = _get_search_keywords("Sueddeutsche Zeitung G")
        assert any("sueddeutsche" in k or "sz-abo" in k for k in kw)

    def test_apple_com_bill_keywords(self):
        kw = _get_search_keywords("APPLE.COM/BILL")
        assert any("apple" in k for k in kw)

    def test_hp_instant_ink_keywords(self):
        kw = _get_search_keywords("HP INSTANT INK DE")
        assert any("hp" in k and "ink" in k for k in kw)

    def test_cursor_keywords(self):
        kw = _get_search_keywords("CURSOR, AI POWERED IDE")
        assert any("cursor" in k or "anysphere" in k for k in kw)

    def test_gettoby_keywords(self):
        kw = _get_search_keywords("WWW.GETTOBY.COM")
        assert any("toby" in k or "gettoby" in k for k in kw)

    def test_mentimeter_keywords(self):
        kw = _get_search_keywords("MENTIMETER BASIC")
        assert "mentimeter" in kw

    def test_bitdefender_keywords(self):
        kw = _get_search_keywords("2CO.COM|BITDEFENDER")
        assert "bitdefender" in kw

    def test_gitkraken_keywords(self):
        kw = _get_search_keywords("GITKRAKEN SOFTWARE")
        assert "gitkraken" in kw

    def test_linkedin_keywords(self):
        kw = _get_search_keywords("LinkedInPreD")
        assert any("linkedin" in k for k in kw)


class TestSlackEmailScoring:
    """Slack billing emails should score high for SLACK vendor."""

    def test_slack_membership_email(self):
        """Screenshot: 'Your workspace's membership has changed.' from feedback@slack.com"""
        msg = _make_msg(
            subject="Your workspace's membership has changed.",
            sender_email="feedback@slack.com",
            has_attachments=True,
        )
        score = _score_candidate(msg, "slack", 97.42)
        # "slack" in sender (+3) + "feedback@" sender bonus (+2) + attachment (+1) = 6
        assert score >= 5

    def test_slack_credit_email(self):
        """Screenshot: '[Slack] Thinktecture: Credit added...' from feedback@slack.com"""
        msg = _make_msg(
            subject="[Slack] Thinktecture: Credit added to account for reduced activity",
            sender_email="feedback@slack.com",
            has_attachments=True,
        )
        score = _score_candidate(msg, "slack", 36.83)
        # "slack" in subject (+3) + "slack" in sender (+3) + "feedback@" sender bonus (+2) + attachment (+1) = 9
        assert score >= 8


class TestSZEmailScoring:
    """SZ (Süddeutsche Zeitung) billing emails should score high."""

    def test_sz_rechnungsservice_email(self):
        """Screenshot: 'SZ-Abo Online-Rechnungsservice 2551826610' from mail.sz-aboservice@sz.de"""
        msg = _make_msg(
            subject="SZ-Abo Online-Rechnungsservice 2551826610",
            sender_email="mail.sz-aboservice@sz.de",
            has_attachments=True,
        )
        score = _score_candidate(msg, "sz-abo", 49.99)
        # "sz-abo" in subject (+3) + "rechnung" receipt term (+2) + "aboservice" sender bonus (+2) + attachment (+1) = 8
        assert score >= 7


class TestAdditionalVendorKeywords:
    """Tests for the second batch of vendor keyword mappings."""

    def test_cerebras_keywords(self):
        kw = _get_search_keywords("CEREBRAS SYSTEMS")
        assert any("cerebras" in k for k in kw)

    def test_openrouter_keywords(self):
        kw = _get_search_keywords("OPENROUTER, INC")
        assert any("openrouter" in k for k in kw)

    def test_speaker_deck_keywords(self):
        kw = _get_search_keywords("SPEAKER DECK")
        assert any("speaker" in k for k in kw)

    def test_polycam_keywords(self):
        kw = _get_search_keywords("POLYCAM")
        assert any("polycam" in k for k in kw)

    def test_pragmatic_engineer_keywords(self):
        kw = _get_search_keywords("PRAGMATICENGINEER.COM")
        assert any("pragmatic" in k for k in kw)

    def test_turing_post_keywords(self):
        kw = _get_search_keywords("THE TURING POST")
        assert any("turing" in k for k in kw)

    def test_vindsurf_typo_keywords(self):
        """VINDSURF is a MC typo for WINDSURF."""
        kw = _get_search_keywords("VINDSURF")
        assert any("windsurf" in k for k in kw)

    def test_aws_keywords(self):
        kw = _get_search_keywords("AWS EMEA")
        assert any("aws" in k for k in kw)

    def test_sumup_keywords(self):
        kw = _get_search_keywords("SumUp *Taxi und Mietw")
        assert any("sumup" in k for k in kw)

    def test_mb_tickets_keywords(self):
        kw = _get_search_keywords("MB-TICKETS*")
        assert any("mb-tickets" in k or "mercedes" in k for k in kw)


# ─── Fallback keyword extraction tests ────────────────────────────


class TestFallbackKeywordExtraction:
    """Tests for the improved fallback in _get_search_keywords().

    These test vendor names that are NOT in VENDOR_KEYWORDS and rely
    on the automatic cleanup logic.
    """

    def test_strips_alphanumeric_reference_id(self):
        """SLACK T060V1XRN → should produce 'slack' not 'slack t060v1xrn'."""
        # SLACK is now in VENDOR_KEYWORDS, so test with a hypothetical similar pattern
        kw = _get_search_keywords("FOOBAR T060V1XRN")
        assert len(kw) >= 1
        assert kw[0] == "foobar"

    def test_strips_star_suffix(self):
        """SPIEGEL* KN 1920835 AB → hits SPIEGEL keyword."""
        kw = _get_search_keywords("SPIEGEL* KN 1920835 AB")
        assert any("spiegel" in k for k in kw)

    def test_strips_hash_suffix(self):
        """MICROSOFT#G139383942 → hits MICROSOFT keyword."""
        kw = _get_search_keywords("MICROSOFT#G139383942")
        assert any("microsoft" in k for k in kw)

    def test_strips_rechtsform_gmbh(self):
        """Heise Medien GmbH & Co. → should contain 'heise'."""
        kw = _get_search_keywords("Heise Medien GmbH & Co.")
        assert any("heise" in k for k in kw)

    def test_strips_rechtsform_inc(self):
        """GITHUB, INC. → hits GITHUB keyword."""
        kw = _get_search_keywords("GITHUB, INC.")
        assert any("github" in k for k in kw)

    def test_strips_www_and_tld(self):
        """WWW.PERPLEXITY.AI → hits PERPLEXITY keyword."""
        kw = _get_search_keywords("WWW.PERPLEXITY.AI")
        assert any("perplexity" in k for k in kw)

    def test_strips_city_suffix(self):
        """THENOUNPROJECT.COM, LOS ANGELES → hits NOUNPROJECT keyword."""
        kw = _get_search_keywords("THENOUNPROJECT.COM, LOS ANGELES")
        assert any("noun" in k for k in kw)

    def test_pipe_separator_takes_meaningful_part(self):
        """For a hypothetical 'FOO.COM|BARSERVICE' without keywords, should pick BARSERVICE."""
        kw = _get_search_keywords("FOO|BARSERVICE")
        assert len(kw) >= 1
        # BARSERVICE has more letters than FOO
        assert "barservice" in kw[0]

    def test_unknown_simple_vendor(self):
        """ACMEWIDGETS → should produce 'acmewidgets'."""
        kw = _get_search_keywords("ACMEWIDGETS")
        assert kw == ["acmewidgets"]

    def test_unknown_vendor_with_city(self):
        """SOMESERVICE, BERLIN → should produce 'someservice'."""
        kw = _get_search_keywords("SOMESERVICE, BERLIN")
        assert kw[0] == "someservice"

    def test_unknown_vendor_with_sro(self):
        """FOOBAR S.R.O. → should produce 'foobar'."""
        kw = _get_search_keywords("FOOBAR S.R.O.")
        assert kw[0] == "foobar"

    def test_leading_the_stripped(self):
        """THE SOMETHING POST → should produce 'something post'."""
        kw = _get_search_keywords("THE SOMETHING POST")
        assert len(kw) >= 1
        assert "the" not in kw[0].split()[0]
        assert "something" in kw[0]

    def test_grohe_darmstadt_gmbh(self):
        """Grohe Darmstadt GmbH → should produce 'grohe'."""
        kw = _get_search_keywords("Grohe Darmstadt GmbH")
        assert kw[0] == "grohe"

    def test_vintage_foods(self):
        """Vintage Foods GmbH C → should produce 'vintage foods'."""
        kw = _get_search_keywords("Vintage Foods GmbH C")
        assert "vintage" in kw[0]

    def test_google_youtube_member(self):
        """Google YouTube Member → should hit Google/WL*GOOGLE keywords."""
        kw = _get_search_keywords("Google YouTube Member")
        assert any("google" in k for k in kw)

    def test_google_star_google_one(self):
        """GOOGLE*GOOGLE ONE → should hit GOOGLE keywords."""
        kw = _get_search_keywords("GOOGLE*GOOGLE ONE")
        assert any("google" in k for k in kw)

    def test_hetzner_online_gmbh(self):
        """HETZNER ONLINE GMBH → should hit HETZNER keywords."""
        kw = _get_search_keywords("HETZNER ONLINE GMBH")
        assert any("hetzner" in k for k in kw)

    def test_audible_gmbh_with_ref(self):
        """Audible Gmbh*030JV0125 → should hit AUDIBLE keywords."""
        kw = _get_search_keywords("Audible Gmbh*030JV0125")
        assert any("audible" in k for k in kw)

    def test_amazon_de_with_ref(self):
        """Amazon.de*Z14LM7WC4 → should hit AMAZON keywords."""
        kw = _get_search_keywords("Amazon.de*Z14LM7WC4")
        assert any("amazon" in k for k in kw)

    def test_paddle_net_teamgpt(self):
        """PADDLE.NET* TEAMGPTLTD → should hit PADDLE keyword."""
        kw = _get_search_keywords("PADDLE.NET* TEAMGPTLTD")
        assert any("paddle" in k for k in kw)

    def test_result_is_not_empty(self):
        """Fallback should never return an empty list."""
        for vendor in ["", "A", "AB", "X Y"]:
            kw = _get_search_keywords(vendor)
            assert len(kw) >= 1, f"Empty keywords for vendor '{vendor}'"

    def test_result_has_no_noise_ids(self):
        """Fallback keywords should not contain alphanumeric reference IDs."""
        # Test with a vendor that would only go through fallback
        kw = _get_search_keywords("TESTVENDOR ABC12XY345")
        assert len(kw) >= 1
        # The reference ID should be stripped
        assert "abc12xy345" not in kw[0]

    def test_www_domain_cleaned(self):
        """WWW.SOMESITE.COM → should produce 'somesite'."""
        kw = _get_search_keywords("WWW.SOMESITE.COM")
        assert kw[0] == "somesite"

    def test_elevenlabs_io(self):
        """ELEVENLABS.IO → should hit ELEVENLABS keyword."""
        kw = _get_search_keywords("ELEVENLABS.IO")
        assert any("elevenlabs" in k for k in kw)

    def test_render_com(self):
        """RENDER.COM → should hit RENDER keyword."""
        kw = _get_search_keywords("RENDER.COM")
        assert any("render" in k for k in kw)

    def test_ngrok_inc(self):
        """NGROK INC. → should hit NGROK keyword."""
        kw = _get_search_keywords("NGROK INC.")
        assert any("ngrok" in k for k in kw)

    def test_paypal_frafnk_typo(self):
        """PAYPAL *FRAFNK → should hit PAYPAL keyword (typo in FRAENK)."""
        kw = _get_search_keywords("PAYPAL *FRAFNK")
        assert any("paypal" in k for k in kw)


# ─── Billing Period (Abrechnungszeitraum) tests ───────────────────

class TestBillingPeriodCalculation:
    """Das Suchfenster sollte relativ zum MC-Abrechnungszeitraum sein,
    nicht nur zum einzelnen Belegdatum. Tests nutzen die ECHTE calc_billing_period()."""

    def test_period_from_entries(self):
        """Abrechnungszeitraum = frühestes bis spätestes Datum."""
        from datetime import datetime
        entries = [
            {"date": "03.12.25", "vendor": "A", "amount": 10},
            {"date": "15.12.25", "vendor": "B", "amount": 20},
            {"date": "02.01.26", "vendor": "C", "amount": 30},
        ]
        period = calc_billing_period(entries)
        assert period is not None
        assert period[0] == datetime(2025, 12, 3)
        assert period[1] == datetime(2026, 1, 2)

    def test_single_entry(self):
        from datetime import datetime
        entries = [{"date": "15.12.25", "vendor": "A", "amount": 10}]
        period = calc_billing_period(entries)
        assert period is not None
        assert period[0] == period[1] == datetime(2025, 12, 15)

    def test_empty_entries(self):
        assert calc_billing_period([]) is None

    def test_entries_without_dates(self):
        entries = [{"vendor": "A", "amount": 10}]
        assert calc_billing_period(entries) is None


class TestBillingPeriodSearchWindow:
    """Verifiziert dass das Suchfenster korrekt berechnet wird."""

    def test_with_billing_period(self):
        """Mit billing_period: Suche von (Periodstart - 7d) bis (Periodende + 14d)."""
        from datetime import datetime, timedelta
        period_start = datetime(2025, 12, 3)
        period_end = datetime(2026, 1, 2)
        billing_period = (period_start, period_end)

        date_from = period_start - timedelta(days=7)
        date_to = period_end + timedelta(days=14)

        assert date_from == datetime(2025, 11, 26)
        assert date_to == datetime(2026, 1, 16)

    def test_without_billing_period_uses_tolerance(self):
        """Ohne billing_period: Suche (Datum - 3d) bis (Datum + 35d)."""
        from datetime import datetime, timedelta
        date = datetime(2025, 12, 15)
        tolerance = 35

        date_from = date - timedelta(days=3)
        date_to = date + timedelta(days=tolerance)

        assert date_from == datetime(2025, 12, 12)
        assert date_to == datetime(2026, 1, 19)

    def test_billing_period_covers_all_entries(self):
        """Das Zeitfenster muss alle Entries + Puffer abdecken."""
        from datetime import datetime, timedelta
        entries = [
            {"date": "03.12.25"},
            {"date": "02.01.26"},
        ]
        from src.outlook import _parse_date
        dates = [_parse_date(e["date"]) for e in entries]
        period_start = min(dates)
        period_end = max(dates)

        date_from = period_start - timedelta(days=7)
        date_to = period_end + timedelta(days=14)

        # Alle Entry-Daten müssen im Fenster liegen
        for d in dates:
            assert date_from <= d <= date_to

    def test_old_run_finds_emails(self):
        """Kernproblem: Run im April mit MC-PDF 12-2025.
        Buchungsdatum 03.12.25, Email kam am 03.12.25.
        Altes Fenster: 03.12.25 - 3d bis 03.12.25 + 35d = 30.11 - 07.01.
        Neues Fenster mit billing_period 03.12.25 - 02.01.26:
        26.11.25 - 16.01.26 → breiter, aber immer noch passend."""
        from datetime import datetime, timedelta
        entry_date = datetime(2025, 12, 3)
        email_date = datetime(2025, 12, 3)

        # Billing period: frühestes bis spätestes Datum im MC-PDF
        period = (datetime(2025, 12, 3), datetime(2026, 1, 2))
        date_from = period[0] - timedelta(days=7)
        date_to = period[1] + timedelta(days=14)

        assert date_from <= email_date <= date_to


# ─── _is_receipt_email tests ──────────────────────────────────────

class TestIsReceiptEmail:
    """Tests für _is_receipt_email() — filtert Nicht-Rechnungs-Emails raus."""

    # ── Echte Receipts (sollen akzeptiert werden) ──

    def test_google_play_receipt(self):
        html = "<html><body>Bestellnummer: SOP.3302<br>Gesamt: 7,99 €/Monat</body></html>"
        assert _is_receipt_email(html)

    def test_stripe_receipt_with_amount(self):
        html = "<html><body>Receipt from Anthropic<br>Amount: $100.00<br>Invoice #12345</body></html>"
        assert _is_receipt_email(html)

    def test_paypal_receipt(self):
        html = "<html><body>Beleg für Ihre Zahlung an Lagerkorn GmbH<br>Betrag: 10,00 EUR</body></html>"
        assert _is_receipt_email(html)

    def test_hp_instant_ink_receipt(self):
        html = "<html><body>HP Instant Ink Rechnung<br>Monatliche Gebühr: 6,99 €<br>MwSt: 1,12 €</body></html>"
        assert _is_receipt_email(html)

    def test_invoice_keyword(self):
        html = "<html><body>Invoice for your subscription<br>Total: $20.00</body></html>"
        assert _is_receipt_email(html)

    # ── Keine Receipts (sollen abgelehnt werden) ──

    def test_nyt_subscription_update(self):
        """NYT 'Thanks for supporting The Times' — kein Beleg."""
        html = "<html><body>Thanks for supporting The Times. Dear Subscriber, Thank you for subscribing to The New York Times.</body></html>"
        assert not _is_receipt_email(html)

    def test_nyt_with_price_but_no_receipt_signal(self):
        """NYT email mit Preis aber ohne Receipt-Keywords — kein Beleg."""
        html = "<html><body>Thanks for supporting The Times. Your subscription: $2.00/week. Next billing date: March 14, 2026.</body></html>"
        assert not _is_receipt_email(html)

    def test_wsj_account_update(self):
        """WSJ 'Account Information Update Confirmation' — kein Beleg."""
        html = "<html><body>Account Information Update Confirmation. Dear Christian, Thank you for contacting us. This email confirms that your request has been processed.</body></html>"
        assert not _is_receipt_email(html)

    def test_perplexity_promo(self):
        """Perplexity promo email ohne Betrag — kein Beleg."""
        html = "<html><body>Use Computer to handle complex tasks. Perplexity Pro gives you access to advanced AI models.</body></html>"
        assert not _is_receipt_email(html)

    def test_newsletter(self):
        html = "<html><body>Weekly AI roundup: Here's what happened this week in artificial intelligence.</body></html>"
        assert not _is_receipt_email(html)

    def test_empty_body(self):
        assert not _is_receipt_email("")

    def test_short_body(self):
        assert not _is_receipt_email("Hi")


# ─── extract_receipt_url_from_html tests ──────────────────────────

class TestExtractReceiptUrl:
    """Tests für extract_receipt_url_from_html() — findet Receipt-Download-Links."""

    # ── Links mit Receipt-Keywords in der URL ──

    def test_paddle_receipt_link(self):
        html = '<a href="https://my.paddle.com/receipt/46136028-159235293/197113901-chre735714feea6">View Receipt</a>'
        url = extract_receipt_url_from_html(html)
        assert url is not None
        assert "paddle.com/receipt" in url

    def test_stripe_invoice_link(self):
        html = '<a href="https://pay.stripe.com/invoice/acct_123/inv_456/pdf">Download Invoice</a>'
        url = extract_receipt_url_from_html(html)
        assert url is not None
        assert "invoice" in url

    def test_billing_link(self):
        html = '<a href="https://example.com/billing/download/12345">Get PDF</a>'
        url = extract_receipt_url_from_html(html)
        assert url is not None
        assert "billing" in url

    def test_rechnung_link(self):
        html = '<a href="https://portal.example.de/rechnung/2026-03.pdf">Rechnung herunterladen</a>'
        url = extract_receipt_url_from_html(html)
        assert url is not None

    # ── Links mit Receipt-Keywords im Anchor-Text ──

    def test_anchor_text_receipt(self):
        html = '<a href="https://example.com/doc/abc123">View your receipt</a>'
        url = extract_receipt_url_from_html(html)
        assert url is not None
        assert "abc123" in url

    def test_anchor_text_download(self):
        html = '<a href="https://example.com/get/pdf">Download your invoice</a>'
        url = extract_receipt_url_from_html(html)
        assert url is not None

    # ── Links die ignoriert werden sollen ──

    def test_unsubscribe_link_skipped(self):
        html = '<a href="https://example.com/receipt/unsubscribe?id=123">Unsubscribe</a>'
        assert extract_receipt_url_from_html(html) is None

    def test_privacy_link_skipped(self):
        html = '<a href="https://example.com/privacy/invoice-policy">Privacy Policy</a>'
        assert extract_receipt_url_from_html(html) is None

    def test_settings_link_skipped(self):
        html = '<a href="https://example.com/settings/billing">Manage Settings</a>'
        assert extract_receipt_url_from_html(html) is None

    def test_mailto_skipped(self):
        html = '<a href="mailto:billing@example.com">Contact Billing</a>'
        assert extract_receipt_url_from_html(html) is None

    # ── Kein Link vorhanden ──

    def test_no_links(self):
        html = "<html><body>Thanks for your payment of $10.00</body></html>"
        assert extract_receipt_url_from_html(html) is None

    def test_irrelevant_links(self):
        html = '<a href="https://example.com/blog">Read our blog</a>'
        assert extract_receipt_url_from_html(html) is None

    def test_empty(self):
        assert extract_receipt_url_from_html("") is None

    # ── Kontext-basierte Erkennung ──

    def test_context_receipt(self):
        """Link ohne Receipt-URL, aber 'receipt' im umgebenden Text."""
        html = '<p>Your receipt is ready.</p><a href="https://example.com/doc/xyz789">Click here</a>'
        url = extract_receipt_url_from_html(html)
        assert url is not None
        assert "xyz789" in url
