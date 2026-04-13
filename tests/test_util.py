"""Tests for src.util — consolidated date parsing."""

from datetime import datetime

from src.util import parse_date


class TestParseDate:
    """Test all supported date formats."""

    def test_german_full(self):
        assert parse_date("21.03.2026") == datetime(2026, 3, 21)

    def test_german_short(self):
        assert parse_date("21.03.26") == datetime(2026, 3, 21)

    def test_iso(self):
        assert parse_date("2026-03-21") == datetime(2026, 3, 21)

    def test_english_abbrev(self):
        assert parse_date("Mar 21, 2026") == datetime(2026, 3, 21)

    def test_english_full_month(self):
        assert parse_date("March 21, 2026") == datetime(2026, 3, 21)

    def test_short_dd_mm_dot(self):
        result = parse_date("21.03.")
        assert result is not None
        assert result.month == 3
        assert result.day == 21
        assert result.year == datetime.now().year

    def test_strips_trailing_time(self):
        assert parse_date("Mar 9, 2026, 1:31 PM") == datetime(2026, 3, 9)

    def test_whitespace(self):
        assert parse_date("  21.03.2026  ") == datetime(2026, 3, 21)

    def test_empty_string(self):
        assert parse_date("") is None

    def test_none_input(self):
        # Expect None — we pass falsy value
        assert parse_date("") is None

    def test_garbage(self):
        assert parse_date("not a date") is None

    def test_german_leading_zero(self):
        assert parse_date("01.01.2026") == datetime(2026, 1, 1)

    def test_iso_single_digit_month(self):
        # strptime handles this fine
        assert parse_date("2026-03-09") == datetime(2026, 3, 9)
