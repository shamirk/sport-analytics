"""Unit tests for app.validation."""
from datetime import date

import pytest

from app.exceptions import ValidationError
from app.validation import (
    ALLOWED_DIVISIONS,
    validate_date_range,
    validate_division,
    validate_member_number,
)


# ---------------------------------------------------------------------------
# validate_member_number
# ---------------------------------------------------------------------------


class TestValidateMemberNumber:
    def test_valid_5_char(self):
        assert validate_member_number("A1234") == "A1234"

    def test_valid_12_char(self):
        result = validate_member_number("A" * 12)
        assert result == "A" * 12

    def test_strips_whitespace(self):
        assert validate_member_number("  A1234  ") == "A1234"

    def test_mixed_case_accepted(self):
        assert validate_member_number("aAbBcC") == "aAbBcC"

    def test_all_digits(self):
        assert validate_member_number("12345") == "12345"

    def test_all_letters(self):
        assert validate_member_number("ABCDE") == "ABCDE"

    def test_too_short_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_member_number("ABC")
        assert exc_info.value.field == "member_number"

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            validate_member_number("")

    def test_too_long_raises(self):
        with pytest.raises(ValidationError):
            validate_member_number("A" * 13)

    def test_special_chars_raises(self):
        with pytest.raises(ValidationError):
            validate_member_number("A123-5")

    def test_spaces_only_raises(self):
        with pytest.raises(ValidationError):
            validate_member_number("     ")

    def test_hyphen_raises(self):
        with pytest.raises(ValidationError):
            validate_member_number("A1234-5")

    def test_exactly_12_valid(self):
        result = validate_member_number("123456789012")
        assert result == "123456789012"

    def test_exactly_5_valid(self):
        result = validate_member_number("AAAAA")
        assert result == "AAAAA"


# ---------------------------------------------------------------------------
# validate_date_range
# ---------------------------------------------------------------------------


class TestValidateDateRange:
    def test_valid_start_before_end(self):
        # Should not raise
        validate_date_range(date(2024, 1, 1), date(2024, 12, 31))

    def test_valid_same_date(self):
        # Start == end is allowed (not start > end)
        validate_date_range(date(2024, 6, 15), date(2024, 6, 15))

    def test_start_after_end_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_date_range(date(2024, 12, 31), date(2024, 1, 1))
        assert exc_info.value.field == "date_range"

    def test_error_message_meaningful(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_date_range(date(2025, 1, 1), date(2024, 1, 1))
        assert "start" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# validate_division
# ---------------------------------------------------------------------------


class TestValidateDivision:
    @pytest.mark.parametrize("division", sorted(ALLOWED_DIVISIONS))
    def test_all_allowed_divisions_pass(self, division):
        assert validate_division(division) == division

    def test_invalid_division_raises(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_division("InvalidDiv")
        assert exc_info.value.field == "division"

    def test_case_sensitive(self):
        with pytest.raises(ValidationError):
            validate_division("open")

    def test_empty_raises(self):
        with pytest.raises(ValidationError):
            validate_division("")

    def test_error_lists_allowed(self):
        with pytest.raises(ValidationError) as exc_info:
            validate_division("Bogus")
        assert "Open" in str(exc_info.value) or "must be one of" in str(exc_info.value)
