"""Unit tests for app.exceptions."""
import pytest

from app.exceptions import (
    MemberNotFoundError,
    RateLimitError,
    ScrapingError,
    ValidationError,
)


class TestMemberNotFoundError:
    def test_stores_member_number(self):
        err = MemberNotFoundError("A12345")
        assert err.member_number == "A12345"

    def test_message_contains_member_number(self):
        err = MemberNotFoundError("XYZ99")
        assert "XYZ99" in str(err)

    def test_is_exception(self):
        assert isinstance(MemberNotFoundError("A"), Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(MemberNotFoundError) as exc_info:
            raise MemberNotFoundError("M99999")
        assert exc_info.value.member_number == "M99999"


class TestScrapingError:
    def test_basic_message(self):
        err = ScrapingError("timeout")
        assert "timeout" in str(err)

    def test_status_code_stored(self):
        err = ScrapingError("not found", status_code=404)
        assert err.status_code == 404

    def test_status_code_defaults_to_none(self):
        err = ScrapingError("oops")
        assert err.status_code is None

    def test_is_exception(self):
        assert isinstance(ScrapingError("x"), Exception)


class TestRateLimitError:
    def test_can_be_instantiated(self):
        err = RateLimitError()
        assert isinstance(err, Exception)

    def test_can_be_raised(self):
        with pytest.raises(RateLimitError):
            raise RateLimitError()


class TestValidationError:
    def test_stores_field(self):
        err = ValidationError("email", "invalid format")
        assert err.field == "email"

    def test_message_contains_field_and_message(self):
        err = ValidationError("member_number", "must be alphanumeric")
        assert "member_number" in str(err)
        assert "alphanumeric" in str(err)

    def test_is_exception(self):
        assert isinstance(ValidationError("f", "m"), Exception)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(ValidationError) as exc_info:
            raise ValidationError("division", "not allowed")
        assert exc_info.value.field == "division"
