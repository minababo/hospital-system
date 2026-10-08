"""The compiled stylesheet (static/css/app.css, built by scripts/build_css.sh) and the
inline SVG icons, which need explicit sizes so they never render huge before CSS loads."""

import re
from pathlib import Path

import pytest
from django.urls import reverse

from accounts.models import Role

ROOT = Path(__file__).resolve().parent.parent
APP_CSS = ROOT / "static/css/app.css"
INPUT_CSS = ROOT / "assets/css/input.css"

pytestmark = pytest.mark.django_db


def css():
    return APP_CSS.read_text(encoding="utf-8")


def selector(class_name):
    """How a class name appears in CSS: ':' and '/' are escaped (sm:grid-cols-2 ->
    .sm\\:grid-cols-2)."""
    return "." + class_name.replace(":", "\\:").replace("/", "\\/")


def template_files():
    return [
        path
        for path in ROOT.rglob("*.html")
        if ".venv" not in path.parts and "templates" in path.parts
    ]


# --- The stylesheet -----------------------------------------------------------------------


def test_app_css_is_built_and_non_trivial():
    text = css()
    assert text.startswith("/*! tailwindcss v4.3.3")
    assert len(text) > 20_000
    assert "\r" not in text  # LF only, so CI's byte-for-byte check is the same everywhere


COMPONENTS = [
    "btn",
    "btn-primary",
    "btn-secondary",
    "btn-danger",
    "btn-ghost",
    "btn-sm",
    "card",
    "card-body",
    "table-wrap",
    "table",
    "form-input",
    "form-select",
    "form-textarea",
    "form-checkbox",
    "form-file",
    "form-label",
    "form-help",
    "form-error",
    "badge",
    "badge-success",
    "badge-warning",
    "badge-danger",
    "badge-info",
    "badge-neutral",
    "alert",
    "alert-info",
    "alert-success",
    "alert-warning",
    "alert-danger",
    "page-title",
    "page-subtitle",
    "section-anchor",
]


@pytest.mark.parametrize("name", COMPONENTS)
def test_component_classes_are_in_app_css(name):
    assert re.search(re.escape(selector(name)) + r"[\s{,:.>\[]", css()), name


@pytest.mark.parametrize(
    "name",
    [
        "md:sticky",  # the sidebar becomes a fixed column from md up
        "md:translate-x-0",
        "md:hidden",  # the menu button
        "sm:grid-cols-4",
        "text-slate-500",
        "bg-primary-50",  # a design token used as a utility (active sidebar item)
        "before:bg-primary-600",
        "focus:not-sr-only",  # the skip link
    ],
)
def test_utilities_used_in_templates_are_compiled(name):
    assert selector(name) in css(), name


def test_file_variant_styles_the_file_input_button():
    # .form-file uses file:... utilities, compiled to ::file-selector-button rules.
    assert "::file-selector-button" in css()


def test_design_tokens_and_unlayered_rules_moved_over():
    text = css()
    assert "--color-primary-700" in text
    assert ".page-content>:first-child" in text
    assert "background-attachment:local,local,scroll,scroll" in text


def safelisted_classes():
    names = []
    for chunk in re.findall(r'@source inline\("([^"]+)"\)', INPUT_CSS.read_text(encoding="utf-8")):
        names.extend(chunk.split())
    return names


def test_safelist_is_not_empty():
    assert {"size-8", "sm:grid-cols-2", "-translate-x-full"} <= set(safelisted_classes())


@pytest.mark.parametrize("name", safelisted_classes())
def test_every_safelisted_class_is_in_app_css(name):
    assert selector(name) in css(), name


def test_print_pages_do_not_load_app_css(client_for_role, make_invoice):
    print_layout = (ROOT / "templates/print/base.html").read_text(encoding="utf-8")
    assert "app.css" not in print_layout
    for path in template_files():
        text = path.read_text(encoding="utf-8")
        if 'extends "print/base.html"' in text:
            assert "app.css" not in text, path
    invoice = make_invoice()
    html = (
        client_for_role(Role.ADMIN)
        .get(reverse("billing:invoice_print", args=[invoice.pk]))
        .content.decode()
    )
    assert "app.css" not in html


# --- Inline SVG sizes ---------------------------------------------------------------------


SVG_TAG = re.compile(r"<svg\b[^>]*>", re.S)


def test_every_svg_in_the_templates_has_width_and_height():
    found = 0
    for path in template_files():
        for tag in SVG_TAG.findall(path.read_text(encoding="utf-8")):
            found += 1
            assert re.search(r"\swidth=", tag) and re.search(r"\sheight=", tag), (path, tag[:80])
    assert found >= 1


def test_rendered_svgs_have_pixel_sizes(client_for_role, make_patient):
    client = client_for_role(Role.ADMIN)
    html = client.get(reverse("dashboard")).content.decode()
    # An empty list renders the larger empty-state icon too.
    html += client.get(reverse("audit:log_list") + "?q=nothing-matches").content.decode()
    tags = SVG_TAG.findall(html)
    assert tags
    sizes = set()
    for tag in tags:
        width = re.search(r'\swidth="(\d+)"', tag)
        height = re.search(r'\sheight="(\d+)"', tag)
        assert width and height and width.group(1) == height.group(1), tag[:120]
        sizes.add(int(width.group(1)))
    assert 20 in sizes  # sidebar icons
    assert 32 in sizes  # empty-state icon
    assert "<svg " not in re.sub(r"<svg\b[^>]*\swidth=\"\d+\"[^>]*>", "", html)
