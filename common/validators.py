import re

from django.core.validators import RegexValidator


def normalize_phone(value):
    """Remove spaces and dashes so "077 123-4567" is stored as "0771234567"."""
    return re.sub(r"[\s-]", "", value or "")


# Expects a value already passed through normalize_phone().
sri_lanka_phone_validator = RegexValidator(
    regex=r"^(0|\+94)\d{9}$",
    message="Enter a Sri Lankan phone number, e.g. 0771234567 or +94771234567.",
)
