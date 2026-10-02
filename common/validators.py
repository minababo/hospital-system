import os
import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

# --- Phone ------------------------------------------------------------------


def normalize_phone(value):
    """Remove spaces and dashes so "077 123-4567" is stored as "0771234567"."""
    return re.sub(r"[\s-]", "", value or "")


# Expects a value already passed through normalize_phone().
sri_lanka_phone_validator = RegexValidator(
    regex=r"^(0|\+94)\d{9}$",
    message="Enter a Sri Lankan phone number, e.g. 0771234567 or +94771234567.",
)


# --- NIC (National Identity Card) -------------------------------------------


def normalize_nic(value):
    """Strip spaces and uppercase, so " 123456789v " is stored as "123456789V"."""
    return re.sub(r"\s", "", value or "").upper()


def validate_sri_lanka_nic(value):
    """Old format: 9 digits + V or X (e.g. 123456789V). New format: 12 digits."""
    if not re.fullmatch(r"\d{9}[VX]|\d{12}", normalize_nic(value)):
        raise ValidationError(
            "Enter a valid NIC: 9 digits followed by V or X, or 12 digits.", code="invalid_nic"
        )


# --- Uploaded documents -----------------------------------------------------

# Allowed extensions and the file type each one must contain.
DOCUMENT_EXTENSIONS = {"pdf": "pdf", "jpg": "jpg", "jpeg": "jpg", "png": "png"}

# The first bytes ("magic numbers") that identify each file type.
FILE_SIGNATURES = [
    (b"%PDF-", "pdf"),
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
]

MIME_TYPES = {"pdf": "application/pdf", "jpg": "image/jpeg", "png": "image/png"}


def file_extension(filename):
    """Lowercase extension without the dot: "Scan.JPEG" -> "jpeg"."""
    return os.path.splitext(filename or "")[1].lower().lstrip(".")


def validate_upload_size(file, max_mb):
    if file.size > max_mb * 1024 * 1024:
        raise ValidationError(f"The file is too large. The maximum size is {max_mb} MB.")


def detect_file_type(file):
    """Return "pdf", "jpg", "png" or None from the file's first bytes."""
    file.seek(0)
    head = file.read(8)
    file.seek(0)
    for signature, file_type in FILE_SIGNATURES:
        if head.startswith(signature):
            return file_type
    return None


def validate_document_upload(file, max_mb):
    """Check size, extension and actual contents. Returns the detected type.

    The browser-sent filename and content type can't be trusted, so the file's
    real bytes must match its extension (a renamed .exe won't pass as .pdf).
    """
    validate_upload_size(file, max_mb)
    expected = DOCUMENT_EXTENSIONS.get(file_extension(file.name))
    if expected is None:
        raise ValidationError("Only PDF, JPG and PNG files can be uploaded.")
    if detect_file_type(file) != expected:
        raise ValidationError(
            f"The file's contents don't match its .{file_extension(file.name)} extension."
        )
    return expected
