from decimal import Decimal

import pytest
from django.urls import reverse

from accounts.models import Role
from billing import services
from billing.models import Charge, Invoice, InvoiceStatus
from billing.permissions import VIEW_BILLING
from patients.views import BILLING_VIEW_ROLES

VIEW = {Role.ADMIN, Role.ACCOUNTANT, Role.RECEPTIONIST}
CASHIER = {Role.ACCOUNTANT, Role.RECEPTIONIST}
ACCOUNTS = {Role.ACCOUNTANT}
VOID = {Role.ADMIN}
VOID_CHARGE = {Role.ACCOUNTANT, Role.ADMIN}


@pytest.fixture
def objects(make_patient, make_charge, make_invoice, make_user):
    """A patient with an unbilled charge, a draft invoice, and an issued invoice with
    one payment."""
    patient = make_patient()
    cashier = make_user()
    unbilled = make_charge(patient=patient, unit_price="300")
    draft = make_invoice(patient=patient, amounts=["500"])
    issued = make_invoice(patient=patient, amounts=["1000"])
    services.issue_invoice(issued, acting_user=cashier)
    payment = services.record_payment(
        invoice=issued, amount="400", method="CASH", reference="", acting_user=cashier
    )
    return {
        "patient": patient,
        "unbilled": unbilled,
        "draft": draft,
        "draft_charge": draft.charges.get(),
        "issued": issued,
        "payment": payment,
    }


# (url name, args builder, method, POST data, allowed roles, status for allowed role)
URLS = [
    ("billing:invoice_list", lambda o: [], "get", None, VIEW, 200),
    ("billing:patient_search", lambda o: [], "get", None, VIEW, 200),
    ("billing:patient_billing", lambda o: [o["patient"].pk], "get", None, VIEW, 200),
    ("billing:invoice_detail", lambda o: [o["issued"].pk], "get", None, VIEW, 200),
    ("billing:invoice_print", lambda o: [o["issued"].pk], "get", None, VIEW, 200),
    ("billing:receipt", lambda o: [o["payment"].pk], "get", None, VIEW, 200),
    (
        "billing:patient_charge_add",
        lambda o: [o["patient"].pk],
        "post",
        {"charge_type": "OTHER", "description": "Dressing", "quantity": 1, "unit_price": "250"},
        CASHIER,
        302,
    ),
    (
        "billing:invoice_create",
        lambda o: [o["patient"].pk],
        "post",
        {"charges": []},
        CASHIER,
        302,
    ),
    (
        "billing:charge_void",
        lambda o: [o["unbilled"].pk],
        "post",
        {"reason": "x"},
        VOID_CHARGE,
        302,
    ),
    (
        "billing:invoice_charge_add",
        lambda o: [o["draft"].pk],
        "post",
        {"charge_type": "OTHER", "description": "Extra", "quantity": 1, "unit_price": "50"},
        ACCOUNTS,
        302,
    ),
    (
        "billing:invoice_charge_remove",
        lambda o: [o["draft"].pk, o["draft_charge"].pk],
        "post",
        {},
        ACCOUNTS,
        302,
    ),
    ("billing:invoice_discount", lambda o: [o["draft"].pk], "post", {"amount": "0"}, ACCOUNTS, 302),
    ("billing:invoice_issue", lambda o: [o["draft"].pk], "post", {}, CASHIER, 302),
    (
        "billing:payment_add",
        lambda o: [o["issued"].pk],
        "post",
        {"amount": "100", "method": "CASH"},
        CASHIER,
        302,
    ),
    ("billing:invoice_void", lambda o: [o["draft"].pk], "post", {"reason": "x"}, VOID, 302),
    ("billing:payment_void", lambda o: [o["payment"].pk], "post", {"reason": "x"}, VOID, 302),
    # Charge correction pages: GET shows the form, POST saves (no JavaScript needed).
    ("billing:charge_void", lambda o: [o["unbilled"].pk], "get", None, VOID_CHARGE, 200),
    ("billing:charge_edit", lambda o: [o["unbilled"].pk], "get", None, ACCOUNTS, 200),
    (
        "billing:charge_edit",
        lambda o: [o["unbilled"].pk],
        "post",
        {
            "description": "Dressing (large)",
            "charge_type": "OTHER",
            "quantity": 2,
            "unit_price": "300",
            "reason": "Wrong size entered",
        },
        ACCOUNTS,
        302,
    ),
]
# These answer GET with a form page; every other POST action rejects GET.
GET_AND_POST_PAGES = {"billing:charge_void", "billing:charge_edit"}
POST_ONLY = [url for url in URLS if url[2] == "post" and url[0] not in GET_AND_POST_PAGES]


@pytest.mark.parametrize(("name", "args", "method", "data", "_roles", "_status"), URLS)
def test_anonymous_redirected(client, objects, name, args, method, data, _roles, _status):
    response = getattr(client, method)(reverse(name, args=args(objects)), data)

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "args", "method", "data", "roles", "status"), URLS)
def test_rbac_matrix(client_for_role, objects, role, name, args, method, data, roles, status):
    response = getattr(client_for_role(role), method)(reverse(name, args=args(objects)), data)

    assert response.status_code == (status if role in roles else 403)


@pytest.mark.parametrize(("name", "args", "_method", "_data", "_roles", "_status"), POST_ONLY)
def test_post_only_reject_get(
    client_for_role, objects, name, args, _method, _data, _roles, _status
):
    response = client_for_role(Role.ACCOUNTANT).get(reverse(name, args=args(objects)))

    assert response.status_code == 405


# --- Separation of duties -----------------------------------------------------


def test_only_admin_voids_invoices_and_payments(client_for_role, objects):
    payment_url = reverse("billing:payment_void", args=[objects["payment"].pk])
    for role in (Role.RECEPTIONIST, Role.ACCOUNTANT):
        assert client_for_role(role).post(payment_url, {"reason": "x"}).status_code == 403

    client_for_role(Role.ADMIN).post(payment_url, {"reason": "Refund"})
    objects["payment"].refresh_from_db()
    assert objects["payment"].is_voided


def test_receptionist_cannot_void_charges(client_for_role, objects):
    url = reverse("billing:charge_void", args=[objects["unbilled"].pk])

    assert client_for_role(Role.RECEPTIONIST).post(url, {"reason": "x"}).status_code == 403


# --- Flows through the pages ---------------------------------------------------


def test_bill_a_completed_consultation_end_to_end(client_for_role, make_completed_appointment):
    appointment = make_completed_appointment()
    client = client_for_role(Role.RECEPTIONIST)

    page = client.get(reverse("billing:patient_billing", args=[appointment.patient.pk]))
    assert "1 completed consultation not billed yet" in page.content.decode()

    response = client.post(reverse("billing:invoice_create", args=[appointment.patient.pk]))
    invoice = Invoice.objects.get()
    assert response.url == reverse("billing:invoice_detail", args=[invoice.pk])

    client.post(reverse("billing:invoice_issue", args=[invoice.pk]))
    client.post(
        reverse("billing:payment_add", args=[invoice.pk]),
        {"amount": "1500.00", "method": "CASH", "reference": ""},
    )
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID


def test_payment_errors_shown_as_messages(client_for_role, objects):
    response = client_for_role(Role.RECEPTIONIST).post(
        reverse("billing:payment_add", args=[objects["issued"].pk]),
        {"amount": "5000", "method": "CASH"},
        follow=True,
    )

    assert "more than the balance" in response.content.decode()


def test_invoice_list_shows_annotated_numbers(client_for_role, objects):
    response = client_for_role(Role.ACCOUNTANT).get(
        reverse("billing:invoice_list"), {"q": objects["issued"].number}
    )
    content = response.content.decode()

    assert "Rs. 1,000.00" in content and "Rs. 400.00" in content and "Rs. 600.00" in content


def test_invoice_print_and_receipt(client_for_role, objects):
    client = client_for_role(Role.ACCOUNTANT)

    invoice_page = client.get(reverse("billing:invoice_print", args=[objects["issued"].pk]))
    receipt_page = client.get(reverse("billing:receipt", args=[objects["payment"].pk]))

    invoice_html = invoice_page.content.decode()
    receipt_html = receipt_page.content.decode()
    assert objects["issued"].number in invoice_html and "Rs. 600.00" in invoice_html
    assert objects["payment"].receipt_number in receipt_html
    assert "Rs. 400.00" in receipt_html
    assert receipt_page.context["balance_after"] == Decimal("600.00")
    assert objects["patient"].mrn in receipt_html


def test_detail_actions_per_role(client_for_role, objects):
    url = reverse("billing:invoice_detail", args=[objects["draft"].pk])

    accountant = client_for_role(Role.ACCOUNTANT).get(url).context
    receptionist = client_for_role(Role.RECEPTIONIST).get(url).context
    admin = client_for_role(Role.ADMIN).get(url).context

    assert accountant["can_edit_draft"] and accountant["can_issue"]
    assert not receptionist["can_edit_draft"] and receptionist["can_issue"]
    assert admin["can_void"] and not admin["can_issue"]


def test_manual_charge_via_view(client_for_role, objects):
    client_for_role(Role.RECEPTIONIST).post(
        reverse("billing:patient_charge_add", args=[objects["patient"].pk]),
        {
            "charge_type": "OTHER",
            "description": "Wound dressing",
            "quantity": 2,
            "unit_price": "150",
        },
    )

    assert Charge.objects.get(description="Wound dressing").amount == Decimal("300.00")


# --- Integration --------------------------------------------------------------------


def test_patients_copy_of_billing_roles_matches():
    assert set(BILLING_VIEW_ROLES) == set(VIEW_BILLING)


@pytest.mark.parametrize("role", [Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR, Role.NURSE])
def test_patient_detail_billing_link(client_for_role, make_patient, role):
    patient = make_patient()

    content = (
        client_for_role(role)
        .get(reverse("patients:patient_detail", args=[patient.pk]))
        .content.decode()
    )

    assert (reverse("billing:patient_billing", args=[patient.pk]) in content) == (role in VIEW)


@pytest.mark.parametrize("role", list(Role))
def test_billing_nav_link(client_for_role, role):
    labels = [
        item["label"]
        for item in client_for_role(role).get(reverse("dashboard")).context["nav_items"]
    ]

    assert ("Billing" in labels) == (role in VIEW)


@pytest.mark.parametrize("role", [Role.ACCOUNTANT, Role.RECEPTIONIST])
def test_dashboard_billing_quick_action(client_for_role, role):
    content = client_for_role(role).get(reverse("dashboard")).content.decode()

    assert "/billing/" in content
