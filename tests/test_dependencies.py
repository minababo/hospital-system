"""One-way app dependencies: lower-level apps must never import the apps built on them."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# app -> apps it must not import
FORBIDDEN = {
    "records": ["laboratory", "billing"],
    "patients": ["laboratory", "billing"],
    "appointments": ["laboratory", "billing"],
    "billing": ["laboratory"],
}


@pytest.mark.parametrize(("app", "forbidden"), FORBIDDEN.items())
def test_app_does_not_import(app, forbidden):
    pattern = re.compile(rf"^\s*(from|import)\s+({'|'.join(forbidden)})\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / app).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# Layering inside the pharmacy app: catalog and stock code must not depend on records;
# only the dispensing modules connect pharmacy to records and billing.
FILE_RULES = [
    ("pharmacy/models.py", r"records"),
    ("pharmacy/selectors.py", r"records"),
    ("pharmacy/services.py", r"records"),
    ("records/**/*.py", r"pharmacy\.(dispensing|dispensing_selectors)"),
]


@pytest.mark.parametrize(("pattern_glob", "forbidden"), FILE_RULES)
def test_file_does_not_import(pattern_glob, forbidden):
    pattern = re.compile(
        rf"^\s*(from\s+{forbidden}\b|import\s+{forbidden}\b|from\s+pharmacy\s+import\s+"
        rf"(dispensing|dispensing_selectors)\b)",
        re.MULTILINE,
    )
    offenders = [
        str(path.relative_to(ROOT))
        for path in ROOT.glob(pattern_glob)
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# Nothing imports admissions: other pages show its data through its template tags.
NOT_ADMISSIONS = [
    "accounts",
    "appointments",
    "billing",
    "common",
    "config",
    "doctors",
    "laboratory",
    "patients",
    "pharmacy",
    "records",
    "reports",
]


@pytest.mark.parametrize("app", NOT_ADMISSIONS)
def test_no_app_imports_admissions(app):
    pattern = re.compile(r"^\s*(from|import)\s+admissions\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / app).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# Nothing imports staff. The "My leave" sidebar link uses the user.employee_profile
# reverse relation, so accounts doesn't need to import staff either.
NOT_STAFF = [
    "accounts",
    "admissions",
    "appointments",
    "billing",
    "common",
    "config",
    "doctors",
    "laboratory",
    "patients",
    "pharmacy",
    "records",
    "reports",
]


@pytest.mark.parametrize("app", NOT_STAFF)
def test_no_app_imports_staff(app):
    pattern = re.compile(r"^\s*(from|import)\s+staff\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / app).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
