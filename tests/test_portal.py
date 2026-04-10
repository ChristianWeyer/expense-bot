"""Tests für src/portal.py — JSON-Config Laden, Vendor-Matching, Invoice-Extraktion."""

import json
import pytest
from pathlib import Path
from src.portal import load_portal_configs, _match_vendor, PORTALS_DIR


class TestLoadConfigs:
    def test_loads_all_json_files(self):
        configs = load_portal_configs()
        assert len(configs) >= 3  # openai-api, adobe, cloudflare
        ids = {c["id"] for c in configs}
        assert "openai-api" in ids
        assert "adobe" in ids

    def test_each_config_has_required_fields(self):
        configs = load_portal_configs()
        for c in configs:
            assert "id" in c, f"Missing 'id' in {c.get('_file')}"
            assert "name" in c, f"Missing 'name' in {c.get('_file')}"
            assert "billing_url" in c, f"Missing 'billing_url' in {c.get('_file')}"
            assert "match_keywords" in c, f"Missing 'match_keywords' in {c.get('_file')}"

    def test_all_json_files_are_valid(self):
        for f in sorted(PORTALS_DIR.glob("*.json")):
            with open(f) as fh:
                data = json.load(fh)
            assert isinstance(data, dict), f"{f.name} is not a JSON object"

    def test_no_duplicate_ids(self):
        configs = load_portal_configs()
        ids = [c["id"] for c in configs]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"


class TestVendorMatching:
    def test_openai_matches(self):
        config = {"id": "openai-api", "match_keywords": ["OPENAI"]}
        assert _match_vendor(config, "OPENAI *CHATGPT SUBSCR")
        assert _match_vendor(config, "OPENAI")
        assert not _match_vendor(config, "ANTHROPIC")

    def test_figma_matches(self):
        config = {"id": "figma", "match_keywords": ["FIGMA"]}
        assert _match_vendor(config, "FIGMA")
        assert _match_vendor(config, "FIGMA, SAN FRANCISCO")
        assert not _match_vendor(config, "ADOBE")

    def test_google_matches_multiple_keywords(self):
        config = {"id": "google-payments", "match_keywords": ["GOOGLE", "YOUTUBE", "WL*GOOGLE"]}
        assert _match_vendor(config, "Google One")
        assert _match_vendor(config, "GOOGLE*YOUTUBE MEMBER")
        assert _match_vendor(config, "WL*GOOGLE YouTube Mem")
        assert not _match_vendor(config, "AMAZON")

    def test_paypal_matches_fraenk(self):
        config = {"id": "paypal", "match_keywords": ["PAYPAL", "FRAENK"]}
        assert _match_vendor(config, "PAYPAL *FRAENK")
        assert _match_vendor(config, "PAYPAL")
        assert not _match_vendor(config, "STRIPE")

    def test_case_insensitive(self):
        config = {"id": "heise", "match_keywords": ["HEISE"]}
        assert _match_vendor(config, "Heise Medien GmbH & Co")
        assert _match_vendor(config, "heise")
        assert _match_vendor(config, "HEISE ONLINE")

    def test_empty_keywords_uses_id_and_name(self):
        config = {"id": "spiegel", "name": "Der Spiegel", "match_keywords": []}
        # Fallback to id and name
        assert _match_vendor(config, "SPIEGEL")

    def test_all_configs_match_expected_vendors(self):
        """Prüft dass jede Config mindestens einen typischen MC-Vendor-Namen matcht."""
        expected = {
            "openai-api": ["OPENAI", "OPENAI, SAN FRANCISCO"],
            "adobe": ["ADOBE *ADOBE", "ADOBE"],
            "cloudflare": ["CLOUDFLARE"],
        }
        configs = load_portal_configs()
        config_map = {c["id"]: c for c in configs}

        for portal_id, vendors in expected.items():
            config = config_map.get(portal_id)
            assert config is not None, f"Config {portal_id} not found"
            for vendor in vendors:
                assert _match_vendor(config, vendor), \
                    f"Config '{portal_id}' should match vendor '{vendor}'"


class TestDownloadMethods:
    def test_each_config_has_valid_download_method(self):
        valid_methods = {"stripe_url", "direct_link", "click_button", "print_page"}
        configs = load_portal_configs()
        for c in configs:
            method = c.get("download", {}).get("method")
            assert method in valid_methods, \
                f"Config '{c['id']}' has invalid download method: {method}"


# ─── Live-Tests ────────────────────────────────────────────

@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLivePortal:
    def test_openai_auth_check_via_cdp(self, live):
        """Prüft ob OpenAI über CDP erreichbar ist (braucht Chrome Canary mit Login)."""
        from src.portal import _is_authenticated
        from playwright.sync_api import sync_playwright

        config = {
            "auth_check_url": "https://platform.openai.com/settings",
            "auth_check_selector": "nav",
        }

        with sync_playwright() as p:
            try:
                browser = p.chromium.connect_over_cdp("http://localhost:9222")
                context = browser.contexts[0] if browser.contexts else browser.new_context()
                page = context.new_page()
                result = _is_authenticated(page, config)
                print(f"OpenAI authenticated: {result}")
                page.close()
                browser.close()
            except Exception as e:
                pytest.skip(f"CDP nicht verfügbar: {e}")
