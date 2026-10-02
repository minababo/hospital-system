import pytest
from django.core.exceptions import ValidationError

from common.validators import (
    detect_file_type,
    normalize_nic,
    normalize_phone,
    sri_lanka_phone_validator,
    validate_document_upload,
    validate_sri_lanka_nic,
    validate_upload_size,
)
from conftest import SAMPLE_FILE_BYTES

# --- Phone ------------------------------------------------------------------


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


# --- NIC --------------------------------------------------------------------


@pytest.mark.parametrize("nic", ["123456789V", "123456789x", "200012345678", " 1234 56789 v "])
def test_valid_nic(nic):
    validate_sri_lanka_nic(nic)  # does not raise


@pytest.mark.parametrize("nic", ["12345678V", "1234567890123", "ABCDEFGHIJ", "123456789A", ""])
def test_invalid_nic(nic):
    with pytest.raises(ValidationError):
        validate_sri_lanka_nic(nic)


def test_normalize_nic():
    assert normalize_nic(" 1234 56789v ") == "123456789V"


# --- Uploads ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "content", "expected"),
    [
        ("report.pdf", SAMPLE_FILE_BYTES["pdf"], "pdf"),
        ("scan.png", SAMPLE_FILE_BYTES["png"], "png"),
        ("photo.jpg", SAMPLE_FILE_BYTES["jpg"], "jpg"),
        ("photo.JPEG", SAMPLE_FILE_BYTES["jpg"], "jpg"),
    ],
)
def test_valid_documents_accepted(make_upload, name, content, expected):
    assert validate_document_upload(make_upload(name, content), max_mb=5) == expected


def test_detect_file_type_rewinds_the_file(make_upload):
    upload = make_upload("report.pdf")

    assert detect_file_type(upload) == "pdf"
    assert upload.read() == SAMPLE_FILE_BYTES["pdf"]  # still readable from the start


def test_too_large_rejected(make_upload):
    upload = make_upload("big.pdf", SAMPLE_FILE_BYTES["pdf"] + b"0" * (1024 * 1024))

    with pytest.raises(ValidationError, match="too large"):
        validate_upload_size(upload, max_mb=1)
    with pytest.raises(ValidationError, match="too large"):
        validate_document_upload(upload, max_mb=1)


@pytest.mark.parametrize(
    ("name", "content", "message"),
    [
        ("virus.exe", b"MZ\x90\x00", "Only PDF, JPG and PNG"),
        ("notes.txt", b"hello", "Only PDF, JPG and PNG"),
        ("fake.pdf", b"just some text", "don't match"),
        ("image.pdf", SAMPLE_FILE_BYTES["png"], "don't match"),
        ("noextension", SAMPLE_FILE_BYTES["pdf"], "Only PDF, JPG and PNG"),
    ],
    ids=["exe", "txt", "text-named-pdf", "png-named-pdf", "no-extension"],
)
def test_invalid_documents_rejected(make_upload, name, content, message):
    with pytest.raises(ValidationError, match=message):
        validate_document_upload(make_upload(name, content), max_mb=5)
