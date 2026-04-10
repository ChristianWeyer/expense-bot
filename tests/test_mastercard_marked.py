"""Tests für mastercard.py — marked_only filtering logic."""

import pytest
from unittest.mock import patch, MagicMock
from pathlib import Path

from src.mastercard import extract_all_entries


def _make_page_result(entries, page_subtotal=None, carry_over=None, final_total=None):
    """Helper: build a page result dict like _call_llm_single_page returns."""
    for e in entries:
        e.setdefault("is_credit", False)
        e.setdefault("category", "other")
        e.setdefault("marked", False)
    return {
        "entries": entries,
        "page_subtotal": page_subtotal,
        "carry_over": carry_over,
        "final_total": final_total,
    }


# Two pages of fake data — page 1 has 3 entries (1 marked), page 2 has 2 entries (1 marked)
PAGE1_ENTRIES = [
    {"vendor": "ANTHROPIC", "amount": 103.36, "date": "01.01.25", "marked": True},
    {"vendor": "Amazon.de", "amount": 22.71, "date": "02.01.25", "marked": False},
    {"vendor": "FX Währungsumrechnung ANTHROPIC", "amount": 2.07, "date": "01.01.25",
     "category": "fx_fee", "marked": False},
]
PAGE2_ENTRIES = [
    {"vendor": "DB Vertrieb GmbH", "amount": 121.00, "date": "05.01.25",
     "category": "db", "marked": True},
    {"vendor": "Hetzner Online", "amount": 45.50, "date": "06.01.25", "marked": False},
]


def _mock_call_side_effect(client, page_image, prompt, page_num, total_pages):
    """Return controlled page results per page number.

    Mimics real _call_llm_single_page by setting _page on each entry.
    """
    if page_num == 1:
        entries = [e.copy() for e in PAGE1_ENTRIES]
        for e in entries:
            e["_page"] = page_num
        return _make_page_result(entries, page_subtotal=128.14)
    elif page_num == 2:
        entries = [e.copy() for e in PAGE2_ENTRIES]
        for e in entries:
            e["_page"] = page_num
        return _make_page_result(entries, final_total=-293.64)
    return _make_page_result([])


@pytest.fixture
def mock_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


@pytest.fixture
def mock_pipeline(mock_env):
    """Patch PDF-to-images and LLM call so no real API calls are made."""
    with (
        patch("src.mastercard._pdf_to_images") as mock_images,
        patch("src.mastercard._call_llm_single_page", side_effect=_mock_call_side_effect) as mock_llm,
        patch("openai.OpenAI") as mock_openai_cls,
    ):
        # Two fake page images
        mock_images.return_value = [
            {"type": "image_url", "image_url": {"url": "fake1"}},
            {"type": "image_url", "image_url": {"url": "fake2"}},
        ]
        # Create a dummy PDF so the FileNotFoundError check passes
        dummy_pdf = Path("/tmp/test_mc_marked.pdf")
        dummy_pdf.write_bytes(b"%PDF-fake")
        yield {
            "mock_images": mock_images,
            "mock_llm": mock_llm,
            "pdf_path": dummy_pdf,
        }
        dummy_pdf.unlink(missing_ok=True)


class TestMarkedOnlyFalse:
    """When marked_only=False, all entries are returned regardless of marked field."""

    def test_returns_all_entries(self, mock_pipeline):
        entries = extract_all_entries(mock_pipeline["pdf_path"], marked_only=False)
        assert len(entries) == 5  # 3 from page1 + 2 from page2

    def test_marked_field_present_but_ignored(self, mock_pipeline):
        entries = extract_all_entries(mock_pipeline["pdf_path"], marked_only=False)
        # marked field exists on entries but doesn't affect which are returned
        vendors = [e["vendor"] for e in entries]
        assert "ANTHROPIC" in vendors
        assert "Amazon.de" in vendors
        assert "DB Vertrieb GmbH" in vendors
        assert "Hetzner Online" in vendors


class TestMarkedOnlyFiltering:
    """When marked_only=True and LLM returns marked: true/false, filtering works."""

    def test_returns_only_marked_entries(self, mock_pipeline):
        entries = extract_all_entries(mock_pipeline["pdf_path"], marked_only=True)
        assert len(entries) == 2
        vendors = {e["vendor"] for e in entries}
        assert vendors == {"ANTHROPIC", "DB Vertrieb GmbH"}

    def test_unmarked_entries_excluded(self, mock_pipeline):
        entries = extract_all_entries(mock_pipeline["pdf_path"], marked_only=True)
        vendors = {e["vendor"] for e in entries}
        assert "Amazon.de" not in vendors
        assert "Hetzner Online" not in vendors
        assert "FX Währungsumrechnung ANTHROPIC" not in vendors

    def test_unique_ids_assigned_before_filtering(self, mock_pipeline):
        entries = extract_all_entries(mock_pipeline["pdf_path"], marked_only=True)
        # IDs should be from the full list indexing, not re-indexed after filtering
        ids = [e["_id"] for e in entries]
        assert all(ids)
        # ANTHROPIC is index 0 (page 1), DB Vertrieb is index 3 (page 2)
        assert "p1_0" in ids
        assert "p2_3" in ids


class TestMarkedFallback:
    """When marked_only=True but NO entries are marked, fall back to all entries."""

    def test_fallback_to_all_when_none_marked(self, mock_env):
        no_marked_entries = [
            {"vendor": "Amazon.de", "amount": 22.71, "marked": False, "is_credit": False, "category": "other"},
            {"vendor": "Hetzner", "amount": 45.50, "marked": False, "is_credit": False, "category": "other"},
        ]
        page_result = _make_page_result(
            [e.copy() for e in no_marked_entries],
            final_total=-68.21,
        )

        with (
            patch("src.mastercard._pdf_to_images") as mock_images,
            patch("src.mastercard._call_llm_single_page", return_value=page_result),
            patch("openai.OpenAI"),
        ):
            mock_images.return_value = [{"type": "image_url", "image_url": {"url": "fake"}}]
            dummy = Path("/tmp/test_mc_fallback.pdf")
            dummy.write_bytes(b"%PDF-fake")
            try:
                entries = extract_all_entries(dummy, marked_only=True)
                # No entries marked → fallback returns all
                assert len(entries) == 2
                vendors = {e["vendor"] for e in entries}
                assert vendors == {"Amazon.de", "Hetzner"}
            finally:
                dummy.unlink(missing_ok=True)


class TestVerificationRunsOnAllEntries:
    """Verification uses ALL entries even when marked_only=True."""

    def test_verify_total_called_with_all_entries(self, mock_env):
        """_verify_total is called before filtering, so it sees all entries."""
        marked_entries = [
            {"vendor": "ANTHROPIC", "amount": 103.36, "marked": True, "is_credit": False, "category": "other"},
        ]
        unmarked_entries = [
            {"vendor": "Amazon.de", "amount": 22.71, "marked": False, "is_credit": False, "category": "other"},
        ]
        all_entries_for_page = marked_entries + unmarked_entries
        page_result = _make_page_result(
            [e.copy() for e in all_entries_for_page],
            final_total=-126.07,
        )

        with (
            patch("src.mastercard._pdf_to_images") as mock_images,
            patch("src.mastercard._call_llm_single_page", return_value=page_result),
            patch("openai.OpenAI"),
            patch("src.mastercard._verify_total", wraps=__import__("src.mastercard", fromlist=["_verify_total"])._verify_total) as spy_total,
        ):
            mock_images.return_value = [{"type": "image_url", "image_url": {"url": "fake"}}]
            dummy = Path("/tmp/test_mc_verify_all.pdf")
            dummy.write_bytes(b"%PDF-fake")
            try:
                entries = extract_all_entries(dummy, marked_only=True)
                # Only the marked entry is returned
                assert len(entries) == 1
                assert entries[0]["vendor"] == "ANTHROPIC"
                # But _verify_total was called with ALL entries (2)
                spy_total.assert_called_once()
                call_args = spy_total.call_args[0]
                assert len(call_args[0]) == 2  # all_entries passed to verify
            finally:
                dummy.unlink(missing_ok=True)


class TestPromptContent:
    """The yellow_instruction in the prompt changes based on marked_only."""

    def test_marked_only_prompt_mentions_yellow(self, mock_pipeline):
        extract_all_entries(mock_pipeline["pdf_path"], marked_only=True)
        call_args = mock_pipeline["mock_llm"].call_args_list[0]
        prompt = call_args[0][2]  # 3rd positional arg is prompt
        assert "YELLOW" in prompt
        assert "cream" in prompt
        assert "false positives" in prompt

    def test_not_marked_prompt_no_yellow_detection(self, mock_pipeline):
        extract_all_entries(mock_pipeline["pdf_path"], marked_only=False)
        call_args = mock_pipeline["mock_llm"].call_args_list[0]
        prompt = call_args[0][2]
        assert "no filtering requested" in prompt
