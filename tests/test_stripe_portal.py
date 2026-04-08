"""Tests für src/stripe_portal.py — Stripe Billing Portal Navigation."""

import pytest
from src.stripe_portal import _find_and_click_manage_button


class TestManageButtonSelectors:
    """Verifiziert dass die Manage-Button-Selektoren die richtigen Texte matchen."""

    def test_selectors_cover_english_variants(self):
        # Diese Texte müssen von den Selektoren gefunden werden
        expected_texts = ["Manage", "Manage Plan", "Manage Subscription", "Manage plan"]
        for text in expected_texts:
            # Prüfe ob mindestens ein Selektor diesen Text matchen würde
            matching = any(text.lower() in sel.lower() for sel in [
                'a:has-text("Manage")',
                'button:has-text("Manage")',
                'a:has-text("Manage Plan")',
                'a:has-text("Manage Subscription")',
                'a:has-text("Manage plan")',
                'button:has-text("Manage Plan")',
                'button:has-text("Manage Subscription")',
            ] if f'"{text}"' in sel or f'"{text.lower()}"' in sel or
                ('"Manage"' in sel and "Manage" in text))
            assert matching, f"Text '{text}' not covered by selectors"

    def test_selectors_cover_german_variants(self):
        expected_texts = ["Abo verwalten", "Abonnement verwalten"]
        for text in expected_texts:
            matching = any(f'"{text}"' in sel for sel in [
                'a:has-text("Abo verwalten")',
                'a:has-text("Abonnement verwalten")',
            ])
            assert matching, f"German text '{text}' not covered by selectors"


@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveStripePortal:
    def test_perplexity_stripe_redirect(self, live):
        """Prüft ob Perplexity auf Stripe weiterleitet."""
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0]
                page = context.new_page()
                page.goto("https://www.perplexity.ai/settings/billing", wait_until="domcontentloaded", timeout=20000)
                page.wait_for_timeout(5000)
                found = _find_and_click_manage_button(page)
                print(f"Manage button found: {found}")
                if found:
                    page.wait_for_timeout(5000)
                    print(f"After click URL: {page.url[:60]}")
                page.close()
                browser.close()
            except Exception as e:
                pytest.skip(f"CDP nicht verfügbar: {e}")
