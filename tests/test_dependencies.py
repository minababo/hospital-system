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
