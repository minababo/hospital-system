"""The shared UI (layout shell, navigation, shared partials) and the UI fixes from #45."""

import re
from decimal import Decimal
from html.parser import HTMLParser
from pathlib import Path

import pytest
from django.urls import reverse
from pytest_django.asserts import assertTemplateUsed

from accounts.models import Role
from accounts.navigation import MY_LEAVE, NAV_GROUPS, NAV_ITEMS, NAV_META

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.django_db


# --- Helpers ----------------------------------------------------------------------------


class FormNesting(HTMLParser):
    """Fails the page if a <form> opens while another one is still open."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nested = False
        self.forms = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.forms += 1
            self.nested = self.nested or self.depth > 0
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


def assert_no_nested_forms(html):
    parser = FormNesting()
    parser.feed(html)
    assert parser.forms > 0, "expected the page to have forms"
    assert not parser.nested, "a <form> is nested inside another <form>"
    assert parser.depth == 0


class FileInputs(HTMLParser):
    def __init__(self):
        super().__init__()
        self.classes = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "input" and attrs.get("type") == "file":
            self.classes.append(attrs.get("class", ""))


def file_input_classes(html):
    parser = FileInputs()
    parser.feed(html)
    return parser.classes


def page(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return response.content.decode()


# --- Layout shell -------------------------------------------------------------------------


def test_logged_in_page_has_skip_link_and_accessible_menu_button(client_for_role):
    html = page(client_for_role(Role.RECEPTIONIST), reverse("dashboard"))

    assert '<a href="#main"' in html and "Skip to content" in html
    assert 'id="main"' in html
    button = re.search(r'<button[^>]*id="nav-open"[^>]*>', html).group()
    assert 'aria-controls="sidebar"' in button
    assert 'aria-expanded="false"' in button
    assert 'id="sidebar"' in html


@pytest.mark.parametrize(
    ("role", "present", "absent"),
    [
        (Role.ADMIN, ["Overview", "Administration", "Finance", "Account"], []),
        (Role.PHARMACIST, ["Overview", "Pharmacy & Lab", "Account"], ["Finance", "Administration"]),
        (Role.ACCOUNTANT, ["Overview", "Finance", "Account"], ["Clinical", "Administration"]),
    ],
)
def test_nav_group_headings_per_role(client_for_role, role, present, absent):
    response = client_for_role(role).get(reverse("dashboard"))
    names = [group["name"] for group in response.context["nav_groups"]]
    html = response.content.decode()

    for name in present:
        assert name in names
        assert f">{name.replace('&', '&amp;')}</p>" in html
    for name in absent:
        assert name not in names


def test_groups_keep_every_item_and_its_order(client_for_role):
    response = client_for_role(Role.ADMIN).get(reverse("dashboard"))
    flat = [item["label"] for item in response.context["nav_items"]]
    grouped = [item["label"] for g in response.context["nav_groups"] for item in g["items"]]

    assert sorted(grouped) == sorted(flat)
    for group in response.context["nav_groups"]:
        labels = [item["label"] for item in group["items"]]
        assert labels == [label for label in flat if label in labels]  # same relative order


def test_every_nav_item_has_a_group_and_an_existing_icon():
    icons = (ROOT / "templates/partials/icon.html").read_text(encoding="utf-8")
    url_names = {url for items in NAV_ITEMS.values() for _, url in items} | {MY_LEAVE[1]}
    for url_name in url_names:
        group, icon = NAV_META[url_name]
        assert group in NAV_GROUPS
        assert f'name == "{icon}"' in icons


def test_active_link_has_aria_current_page(client_for_role):
    html = page(client_for_role(Role.RECEPTIONIST), reverse("patients:patient_list"))
    url = reverse("patients:patient_list")

    assert re.search(rf'<a href="{url}"[^>]*aria-current="page"', html)
    assert html.count('aria-current="page"') == 1


def test_page_header_blocks_render_title_and_actions(client_for_role):
    html = page(client_for_role(Role.ADMIN), reverse("accounts:user_list"))

    assert '<h1 class="page-title">Users</h1>' in html
    assert "New user" in html


# --- Fix 1: doctor role on the user edit form ------------------------------------------


def test_doctor_role_is_disabled_on_the_user_edit_form(client, admin_user_obj, make_doctor):
    doctor_user = make_doctor().user
    client.force_login(admin_user_obj)
    url = reverse("accounts:user_update", args=[doctor_user.pk])

    html = page(client, url)
    select = re.search(r'<select name="role"[^>]*>', html).group()
    assert "disabled" in select
    assert "form-select" in select
    assert "Doctor role is managed under Doctors." in html

    response = client.post(
        url,
        {
            "first_name": doctor_user.first_name,
            "last_name": doctor_user.last_name,
            "email": doctor_user.email,
            "role": Role.ADMIN,
            "is_active": "on",
        },
    )
    assert response.status_code == 302
    doctor_user.refresh_from_db()
    assert doctor_user.role == Role.DOCTOR


def test_other_users_role_is_still_editable(client, admin_user_obj, make_user):
    nurse = make_user(role=Role.NURSE)
    client.force_login(admin_user_obj)
    html = page(client, reverse("accounts:user_update", args=[nurse.pk]))

    select = re.search(r'<select name="role"[^>]*>', html).group()
    assert "disabled" not in select


# --- Fix 3: file inputs --------------------------------------------------------------


def test_patient_document_upload_uses_form_file(client_for_role, make_patient):
    html = page(
        client_for_role(Role.RECEPTIONIST),
        reverse("patients:patient_detail", args=[make_patient().pk]),
    )
    assert file_input_classes(html) == ["form-file"]
    assert "Accepted file types: PDF, JPG, JPEG, PNG" in html


def test_consultation_report_upload_uses_form_file(client, make_record):
    record = make_record()
    client.force_login(record.doctor.user)
    html = page(client, reverse("records:record_detail", args=[record.pk]))

    assert "form-file" in file_input_classes(html)
    assert "Accepted file types: PDF, JPG, JPEG, PNG" in html


def test_lab_report_upload_uses_form_file(client_for_role, make_lab_order):
    order = make_lab_order()
    html = page(
        client_for_role(Role.LAB_STAFF), reverse("laboratory:order_detail", args=[order.pk])
    )
    assert file_input_classes(html) == ["form-file"]
    assert "Accepted file types: PDF, JPG, JPEG, PNG" in html


# --- No nested forms ----------------------------------------------------------------


def test_no_nested_forms_on_busy_pages(
    client,
    make_user,
    make_patient,
    make_record,
    make_invoice,
    make_charge,
    make_lab_order,
    make_issued_prescription,
    make_batch,
    make_medicine,
):
    patient = make_patient()
    record = make_record()
    invoice = make_invoice(patient=patient, amounts=("100", "200"))
    make_charge(patient=patient)
    order = make_lab_order()
    medicine = make_medicine(unit_price=Decimal("10.00"))
    make_batch(medicine=medicine, qty=50)
    prescription = make_issued_prescription(items=[(medicine, 5)])

    pages = [
        (make_user(role=Role.ADMIN), reverse("patients:patient_detail", args=[patient.pk])),
        (record.doctor.user, reverse("records:record_detail", args=[record.pk])),
        (make_user(role=Role.ACCOUNTANT), reverse("billing:invoice_detail", args=[invoice.pk])),
        (make_user(role=Role.ACCOUNTANT), reverse("billing:patient_billing", args=[patient.pk])),
        (make_user(role=Role.LAB_STAFF), reverse("laboratory:order_detail", args=[order.pk])),
        (
            make_user(role=Role.PHARMACIST),
            reverse("pharmacy:dispense_page", args=[prescription.pk]),
        ),
    ]
    for user, url in pages:
        client.force_login(user)
        assert_no_nested_forms(page(client, url))


# --- Shared partials ----------------------------------------------------------------


def test_user_list_uses_the_shared_pagination_partial(client_for_role):
    response = client_for_role(Role.ADMIN).get(reverse("accounts:user_list"))
    assertTemplateUsed(response, "partials/pagination.html")


def test_empty_list_uses_the_empty_state_partial(client_for_role):
    response = client_for_role(Role.ADMIN).get(reverse("staff:employee_list"))
    assertTemplateUsed(response, "partials/empty_state.html")
    assert "No employees found." in response.content.decode()


def test_form_fields_get_component_classes(client_for_role):
    html = page(client_for_role(Role.ADMIN), reverse("accounts:user_create"))

    assert re.search(r'<input type="text" name="username"[^>]*class="form-input"', html)
    assert re.search(r'<select name="role"[^>]*class="form-select"', html)


def test_messages_render_as_dismissible_alerts(client_for_role, make_charge):
    charge = make_charge()
    client = client_for_role(Role.ACCOUNTANT)
    response = client.post(
        reverse("billing:charge_void", args=[charge.pk]), {"reason": "Duplicate"}, follow=True
    )
    html = response.content.decode()
    assert "alert-success" in html and "Charge voided." in html
    assert "data-dismiss" in html


# --- Print pages and the CDN pin ----------------------------------------------------


PRINT_TEMPLATES = [
    "admissions/templates/admissions/discharge_summary.html",
    "billing/templates/billing/invoice_print.html",
    "billing/templates/billing/receipt.html",
    "laboratory/templates/laboratory/report_print.html",
    "records/templates/records/print_prescription.html",
    "records/templates/records/print_record.html",
    "reports/templates/reports/reports/print.html",
]


@pytest.mark.parametrize("path", PRINT_TEMPLATES)
def test_print_templates_still_extend_the_print_layout(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    assert text.startswith('{% extends "print/base.html" %}')
    assert "tailwindcss" not in text


def test_print_layout_has_no_tailwind():
    assert "tailwindcss" not in (ROOT / "templates/print/base.html").read_text(encoding="utf-8")


def test_rendered_print_page_has_no_tailwind_or_sidebar(client_for_role, make_invoice):
    invoice = make_invoice()
    html = page(client_for_role(Role.ADMIN), reverse("billing:invoice_print", args=[invoice.pk]))
    assert "tailwindcss" not in html
    assert 'id="sidebar"' not in html


def test_tailwind_cdn_is_pinned_to_an_exact_version():
    base = (ROOT / "templates/base.html").read_text(encoding="utf-8")
    urls = re.findall(r'src="(https://cdn\.jsdelivr\.net/npm/@tailwindcss/browser@[^"]+)"', base)
    assert len(urls) == 1
    assert re.fullmatch(r".*@tailwindcss/browser@4\.\d+\.\d+", urls[0])
    assert "@4/" not in base and 'browser@4"' not in base
