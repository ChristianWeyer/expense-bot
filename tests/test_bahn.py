"""
Tests für bahn.de Rechnungs-Download (expense_bot.py)
=====================================================
Testet Login, Invoice-Download und Hilfsfunktionen.

Standalone:
    pytest tests/test_bahn.py -v
    pytest tests/test_bahn.py -v --live  # mit echtem bahn.de Login
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


# ─── Unit Tests ────────────────────────────────────────────

class TestTimer:
    def test_timer_format_seconds(self):
        from expense_bot import Timer
        assert Timer._fmt(5.3) == "5.3s"
        assert Timer._fmt(59.9) == "59.9s"

    def test_timer_format_minutes(self):
        from expense_bot import Timer
        assert Timer._fmt(65.0) == "1m 5.0s"
        assert Timer._fmt(130.5) == "2m 10.5s"


class TestFileHash:
    def test_hash_consistency(self, tmp_path):
        from expense_bot import file_hash
        f = tmp_path / "test.pdf"
        f.write_text("test content")
        h1 = file_hash(f)
        h2 = file_hash(f)
        assert h1 == h2
        assert len(h1) == 64  # SHA256

    def test_different_files_different_hash(self, tmp_path):
        from expense_bot import file_hash
        f1 = tmp_path / "a.pdf"
        f2 = tmp_path / "b.pdf"
        f1.write_text("content a")
        f2.write_text("content b")
        assert file_hash(f1) != file_hash(f2)


class TestHistory:
    def test_is_known_file_sha256(self, tmp_path):
        from expense_bot import file_hash, is_known_file
        f = tmp_path / "test.pdf"
        f.write_text("test")
        h = file_hash(f)
        history = {h}
        assert is_known_file(f, history)

    def test_is_known_file_md5_compat(self, tmp_path):
        from expense_bot import _file_hash_md5, is_known_file
        f = tmp_path / "test.pdf"
        f.write_text("test")
        old_hash = _file_hash_md5(f)
        history = {old_hash}
        assert is_known_file(f, history)

    def test_unknown_file(self, tmp_path):
        from expense_bot import is_known_file
        f = tmp_path / "test.pdf"
        f.write_text("test")
        assert not is_known_file(f, set())


class TestCleanup:
    def test_cleanup_old_files(self, tmp_path):
        import os, time
        from expense_bot import cleanup_old_invoices, DOWNLOAD_DIR

        # Create old and new files
        old = tmp_path / "old.pdf"
        new = tmp_path / "new.pdf"
        old.write_text("old")
        new.write_text("new")

        # Make old file 60 days old
        old_time = time.time() - (60 * 86400)
        os.utime(old, (old_time, old_time))

        # Monkey-patch DOWNLOAD_DIR
        import expense_bot
        orig = expense_bot.DOWNLOAD_DIR
        expense_bot.DOWNLOAD_DIR = tmp_path
        try:
            cleanup_old_invoices(30)
            assert not old.exists()
            assert new.exists()
        finally:
            expense_bot.DOWNLOAD_DIR = orig


class TestCredentials:
    def test_op_read_nonexistent(self):
        from expense_bot import _op_read
        result = _op_read("op://NonExistent/Item/field")
        # Should return None, not crash
        assert result is None

    def test_get_secret_env_priority(self):
        import os
        from expense_bot import _get_secret
        os.environ["TEST_SECRET_XYZ"] = "from_env"
        try:
            val = _get_secret("TEST_SECRET_XYZ", "op://doesnt/matter")
            assert val == "from_env"
        finally:
            del os.environ["TEST_SECRET_XYZ"]


# ─── Live-Tests ────────────────────────────────────────────

@pytest.fixture
def live(request):
    if not request.config.getoption("--live"):
        pytest.skip("Live-Test übersprungen (nutze --live)")


class TestLiveBahn:
    def test_bahn_login(self, live):
        """Testet bahn.de Login (braucht Credentials in .env oder 1Password)."""
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).parent.parent / ".env")
        from expense_bot import BAHN_EMAIL, BAHN_PASSWORD, login, Timer
        from playwright.sync_api import sync_playwright

        if not BAHN_EMAIL or not BAHN_PASSWORD:
            pytest.skip("BAHN_EMAIL/BAHN_PASSWORD nicht konfiguriert")

        timer = Timer()
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(Path(__file__).parent.parent / ".browser-data"),
                headless=True,
                accept_downloads=True,
                locale="de-DE",
            )
            page = context.new_page()
            try:
                login(page, timer)
                # Wenn wir hier ankommen, war der Login erfolgreich
            finally:
                context.close()
