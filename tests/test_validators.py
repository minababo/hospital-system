import pytest
from django.core.exceptions import ValidationError

from common.validators import normalize_phone, sri_lanka_phone_validator


@pytest.mark.parametrize("raw", ["0771234567", "+94771234567", "077 123 4567", "077-123-4567"])
def test_valid_sri_lanka_phone_numbers(raw):
    sri_lanka_phone_validator(normalize_phone(raw))  # does not raise


@pytest.mark.parametrize(
    "raw",
    ["077123456", "07712345678", "+9477123456", "077123456a", "1771234567", "94771234567", ""],
    ids=["short", "long", "short-intl", "letters", "no-leading-zero", "no-plus", "empty"],
)
def test_invalid_sri_lanka_phone_numbers(raw):
    with pytest.raises(ValidationError):
        sri_lanka_phone_validator(normalize_phone(raw))


def test_normalize_phone_strips_spaces_and_dashes():
    assert normalize_phone(" +94 77-123 4567 ") == "+94771234567"
