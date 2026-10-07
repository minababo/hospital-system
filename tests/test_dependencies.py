"""One-way app dependencies: lower-level apps must never import the apps built on them."""

import re
import sys
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


# reports is the top-level aggregator: it reads every app, so nothing may import it
# (config/urls.py only includes its URLs).
NOT_REPORTS = [
    "accounts",
    "admissions",
    "appointments",
    "billing",
    "common",
    "doctors",
    "laboratory",
    "patients",
    "pharmacy",
    "records",
    "staff",
]


@pytest.mark.parametrize("app", NOT_REPORTS)
def test_no_app_imports_reports(app):
    pattern = re.compile(r"^\s*(from|import)\s+reports\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / app).rglob("*.py")
        if pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# audit sits underneath everything: every app may call audit.services.log_action, so
# audit itself may only use accounts (roles), common and Django. Its tests are left
# out: they exercise other apps' services to check what gets logged.
AUDIT_ALLOWED_IMPORTS = {"accounts", "audit", "common", "django"}


def _imported_roots(text):
    names = re.findall(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", text, re.MULTILINE)
    return {(a or b).split(".")[0] for a, b in names}


def test_audit_imports_only_accounts_common_and_django():
    offenders = {}
    for path in (ROOT / "audit").rglob("*.py"):
        if "tests" in path.relative_to(ROOT).parts:
            continue
        roots = _imported_roots(path.read_text(encoding="utf-8"))
        bad = roots - AUDIT_ALLOWED_IMPORTS - set(sys.stdlib_module_names)
        if bad:
            offenders[str(path.relative_to(ROOT))] = sorted(bad)

    assert offenders == {}


def test_other_apps_use_only_audit_services():
    pattern = re.compile(r"^\s*(from|import)\s+audit(?!\.services\b)", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in ROOT.glob("*/**/*.py")
        if path.relative_to(ROOT).parts[0] not in ("audit", ".venv", "tests")
        and "tests" not in path.relative_to(ROOT).parts
        and pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []


# demo (manage.py seed_demo) may import any app to build its data; nothing imports demo.
def test_no_app_imports_demo():
    pattern = re.compile(r"^\s*(from|import)\s+demo\b", re.MULTILINE)
    offenders = [
        str(path.relative_to(ROOT))
        for path in ROOT.glob("*/**/*.py")
        if path.relative_to(ROOT).parts[0] not in ("demo", ".venv")
        and pattern.search(path.read_text(encoding="utf-8"))
    ]

    assert offenders == []
