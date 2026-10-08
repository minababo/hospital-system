"""The favicon: an SVG for modern browsers and /favicon.ico (served at the site root by
WhiteNoise from public/) for the rest."""

from pathlib import Path

import pytest
from django.contrib.staticfiles import finders
from django.urls import reverse

from accounts.models import Role

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.django_db


def test_favicon_ico_is_served_at_the_root_without_login(client):
    response = client.get("/favicon.ico")

    assert response.status_code == 200  # WhiteNoise answers before the login check
    assert response["Content-Type"].startswith("image/")
    body = b"".join(response.streaming_content)
    assert body[:4] == b"\x00\x00\x01\x00"  # ICO header: reserved 0, type 1 (icon)
    assert b"\x89PNG" in body  # a 32x32 PNG inside


def test_favicon_svg_is_a_static_file():
    path = finders.find("favicon.svg")
    assert path
    text = Path(path).read_text(encoding="utf-8")
    assert text.startswith("<svg") and 'viewBox="0 0 32 32"' in text


@pytest.mark.parametrize("logged_in", [True, False])
def test_pages_link_both_icons(client, client_for_role, logged_in):
    if logged_in:
        html = client_for_role(Role.NURSE).get(reverse("dashboard")).content.decode()
    else:
        html = client.get(reverse("accounts:login")).content.decode()  # centered layout

    assert '<link rel="icon" href="/favicon.ico" sizes="32x32">' in html
    assert '<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">' in html


def test_only_the_favicon_is_public_at_the_root():
    # Everything in public/ is served at the site root without a login check.
    assert sorted(p.name for p in (ROOT / "public").iterdir()) == ["favicon.ico"]
