"""
Tests für 1Password CLI Integration
====================================
Testet dass Credentials aus 1Password gelesen werden können.

Standalone:
    pytest tests/test_1password.py -v
    pytest tests/test_1password.py -v --live  # mit echtem 1Password
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from expense_bot import _op_read, _get_secret


# ─── Unit Tests ────────────────────────────────────────────

class TestOpRead:
    def test_invalid_ref_returns_none(self):
        result = _op_read("op://NonExistent/VaultThatDoesNotExist/field")
        assert result is None

    def test_empty_ref_returns_none(self):
        result = _op_read("")
        assert result is None


class TestGetSecret:
    def test_env_takes_priority(self):
        import os
        os.environ["_TEST_SECRET"] = "env_value"
        try:
            assert _get_secret("_TEST_SECRET", "op://x/y/z") == "env_value"
        finally:
            del os.environ["_TEST_SECRET"]

    def test_empty_env_falls_back_to_op(self):
        import os
        os.environ["_TEST_SECRET_EMPTY"] = ""
        try:
            # Empty .env value should try 1Password (which returns None for fake ref)
            result = _get_secret("_TEST_SECRET_EMPTY", "op://NonExistent/Item/field")
            assert result is None
        finally:
            del os.environ["_TEST_SECRET_EMPTY"]


# ─── Live-Tests ────────────────────────────────────────────

@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLive1Password:
    def test_read_bahn_username(self, live):
        val = _op_read("op://Private/Bahn/username")
        assert val is not None
        assert len(val) > 3

    def test_read_amazon_email(self, live):
        val = _op_read("op://Private/Amazon - Thinktecture/email")
        assert val is not None
        assert "@" in val
