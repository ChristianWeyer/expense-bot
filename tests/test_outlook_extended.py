"""Extended tests for outlook.py — new vendor keywords, multi-folder search."""

import pytest
from src.outlook import _get_search_keywords, _score_candidate


class TestNewVendorKeywords:
    """Test the vendor keywords added in the reliability overhaul."""

    def test_latent_space(self):
        kw = _get_search_keywords("LATENT.SPACE/SWYX, SINGAPORE")
        assert any("latent" in k for k in kw) or any("swyx" in k for k in kw)

    def test_holiday_inn(self):
        kw = _get_search_keywords("Holiday Inn Express, Karlsruhe")
        assert any("holiday" in k.lower() for k in kw) or any("ihg" in k for k in kw)

    def test_claude_ai_subscription(self):
        kw = _get_search_keywords("CLAUDE.AI SUBSCRIPTION, SAN FRANCISCO")
        assert any("anthropic" in k or "claude" in k for k in kw)

    def test_auth0(self):
        kw = _get_search_keywords("AUTHO.COM, SAN FRANCISCO")
        assert any("auth0" in k for k in kw)

    def test_auth0_direct(self):
        kw = _get_search_keywords("AUTH0.COM, SAN FRANCISCO")
        assert any("auth0" in k for k in kw)

    def test_remove_bg(self):
        kw = _get_search_keywords("PADDLE.NET* REMOVE.BG, Lisboa")
        assert any("remove" in k or "kaleido" in k for k in kw)

    def test_lordicon(self):
        kw = _get_search_keywords("PADDLE.NET* LORDICON, LISBOA")
        assert any("lordicon" in k for k in kw)

    def test_voila(self):
        kw = _get_search_keywords("PADDLE.NET* SPD2 VOILA, LISBOA")
        assert any("voila" in k or "spd2" in k for k in kw)

    def test_wachete_fixed(self):
        """VACHETE was a typo, should now match WACHETE."""
        kw = _get_search_keywords("WACHETE S.R.O., PRAGUE")
        assert any("wachete" in k for k in kw)

    def test_windsurf(self):
        kw = _get_search_keywords("WINDSURF, MOUNTAIN VIEW")
        assert any("windsurf" in k or "exafunction" in k for k in kw)

    def test_perplexity(self):
        kw = _get_search_keywords("www.PERPLEXITY.AI, SAN FRANCISCO")
        assert any("perplexity" in k for k in kw)

    def test_langchain(self):
        kw = _get_search_keywords("LANGCHAIN LANGSMITH, SAN FRANCISCO")
        assert any("langchain" in k or "langsmith" in k for k in kw)

    def test_huggingface(self):
        kw = _get_search_keywords("HUGGINGFACE, BROOKLYN")
        assert any("huggingface" in k or "hugging" in k for k in kw)

    def test_tailscale(self):
        kw = _get_search_keywords("TAILSCALE US INC., SAN FRANCISCO")
        assert any("tailscale" in k for k in kw)


class TestExistingVendorKeywords:
    """Regression tests for vendors that should still work."""

    def test_anthropic(self):
        kw = _get_search_keywords("ANTHROPIC, SAN FRANCISCO")
        assert "anthropic" in kw or "claude" in kw

    def test_openai_chatgpt(self):
        kw = _get_search_keywords("OPENAI *CHATGPT SUBSCR, SAN")
        assert "openai" in kw or "chatgpt" in kw

    def test_github(self):
        kw = _get_search_keywords("GITHUB, INC., SAN FRANCISCO")
        assert "github" in kw

    def test_microsoft(self):
        kw = _get_search_keywords("Microsoft-G145134122, msbill.info")
        assert any("microsoft" in k or "msbill" in k for k in kw)

    def test_msft(self):
        kw = _get_search_keywords("MSFT * E0600YXIF2, MSBILL. INFO")
        assert any("microsoft" in k or "msbill" in k for k in kw)

    def test_hetzner(self):
        kw = _get_search_keywords("HETZNER ONLINE GMBH, GUNZENHAUSEN")
        assert any("hetzner" in k for k in kw)

    def test_adobe(self):
        kw = _get_search_keywords("ADOBE *ADOBE, DUBLIN")
        assert any("adobe" in k for k in kw)

    def test_ngrok(self):
        kw = _get_search_keywords("NGROK INC., SAN FRANCISCO")
        assert any("ngrok" in k for k in kw)

    def test_nounproject(self):
        kw = _get_search_keywords("THENOUNPROJECT.COM, LOS ANGELES")
        assert any("noun" in k for k in kw)

    def test_elevenlabs(self):
        kw = _get_search_keywords("ELEVENLABS.IO, NEW YORK")
        assert any("elevenlabs" in k for k in kw)

    def test_handelsblatt(self):
        kw = _get_search_keywords("HANDELSBL* KD 82137639, DUSSELDORF")
        assert any("handelsblatt" in k for k in kw)

    def test_render(self):
        kw = _get_search_keywords("RENDER.COM, SAN FRANCISCO")
        assert any("render" in k for k in kw)

    def test_x_corp(self):
        kw = _get_search_keywords("X CORP. PAID FEATURES, BASTROP")
        assert any("x" in k.lower() or "twitter" in k for k in kw)


class TestScoringEdgeCases:
    def test_billing_sender_with_no_vendor_match(self):
        """A billing sender with score=2 should pass the threshold."""
        msg = {
            "subject": "Your invoice for March",
            "from": {"emailAddress": {"address": "billing@example.com"}},
        }
        score = _score_candidate(msg, "UNKNOWN_VENDOR", 50.0)
        # "invoice" in subject = +2, billing@ sender = +2 → total 4
        assert score >= 4

    def test_bot_email_heavily_penalized(self):
        msg = {
            "subject": "[automatisch] Belege 5/10",
            "from": {"emailAddress": {"address": "me@example.com"}},
        }
        score = _score_candidate(msg, "anthropic", 100.0)
        assert score < 0

    def test_vendor_in_sender_gives_bonus(self):
        msg = {
            "subject": "Payment confirmation",
            "from": {"emailAddress": {"address": "receipts@anthropic.com"}},
        }
        score = _score_candidate(msg, "anthropic", 100.0)
        assert score >= 3  # vendor in sender
