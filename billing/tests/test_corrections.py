"""Billing workflow corrections (#42): editing manual charges, the duplicate-charge
warning, discount accountability, and the no-JavaScript edit/void pages."""

from datetime import datetime, timedelta
from decimal import Decimal
from html.parser import HTMLParser

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from audit.models import AuditLog
from billing import services
from billing.models import Charge, ChargeType, InvoiceStatus

pytestmark = pytest.mark.django_db

NOON = timezone.make_aware(datetime(2026, 10, 7, 12, 0))


@pytest.fixture
def accountant(make_user):
    return make_user(role=Role.ACCOUNTANT)


def edit(charge, user, **overrides):
    values = {
        "description": charge.description,
        "charge_type": charge.charge_type,
        "quantity": charge.quantity,
        "unit_price": charge.unit_price,
        "reason": "Price was wrong",
    }
    values.update(overrides)
    return services.edit_charge(charge, acting_user=user, **values)


def add(patient, user, **overrides):
    values = {
        "charge_type": ChargeType.OTHER,
        "description": "Wound dressing",
        "quantity": 1,
        "unit_price": Decimal("500.00"),
    }
    values.update(overrides)
    return services.add_manual_charge(patient=patient, acting_user=user, **values)


# --- edit_charge ----------------------------------------------------------------------


def test_manual_unbilled_charge_is_edited_and_amount_recalculated(make_charge, accountant):
    charge = make_charge(unit_price="1000.00")
    assert charge.is_editable

    edit(charge, accountant, quantity=3, unit_price="250.50", description="  Dressing  ")

    charge.refresh_from_db()
    assert (charge.quantity, charge.unit_price, charge.amount) == (
        3,
        Decimal("250.50"),
        Decimal("751.50"),
    )
    assert charge.description == "Dressing"


def test_edit_is_audited_with_a_diff_and_the_reason(make_charge, accountant):
    charge = make_charge(unit_price="1000.00")
    edit(charge, accountant, unit_price="800.00", reason="Discounted rate agreed")

    entry = AuditLog.objects.get(event="billing.charge.edited")
    assert entry.patient == charge.patient
    assert entry.message == "Correction: Discounted rate agreed"
    assert entry.changes == {
        "amount": ["1000.00", "800.00"],
        "unit_price": ["1000.00", "800.00"],
    }


def test_charge_on_a_draft_invoice_is_editable_and_totals_follow(make_invoice, accountant):
    invoice = make_invoice(amounts=("1000.00", "200.00"))
    charge = invoice.charges.order_by("pk").first()
    assert charge.is_editable

    edit(charge, accountant, unit_price="600.00")

    invoice.refresh_from_db()
    assert invoice.subtotal == Decimal("800.00")
    assert invoice.total == Decimal("800.00")


@pytest.mark.parametrize("paid", [False, True])
def test_charge_on_an_issued_or_paid_invoice_is_refused(make_invoice, accountant, paid):
    invoice = make_invoice(amounts=("1000.00",))
    services.issue_invoice(invoice, acting_user=accountant)
    if paid:
        services.record_payment(
            invoice=invoice, amount="1000.00", method="CASH", reference="", acting_user=accountant
        )
    # Read fresh: the charge must see the invoice's current status, not this stale copy.
    charge = Charge.objects.get(invoice=invoice)
    assert not charge.is_editable

    with pytest.raises(ValidationError, match="issued invoice"):
        edit(charge, accountant, unit_price="1.00")
    charge.refresh_from_db()
    assert charge.unit_price == Decimal("1000.00")


def test_voided_charge_is_refused(make_charge, accountant):
    charge = make_charge()
    services.void_charge(charge, reason="Entered twice", acting_user=accountant)
    with pytest.raises(ValidationError, match="Voided charges"):
        edit(charge, accountant, unit_price="1.00")


@pytest.mark.parametrize(
    ("source_type", "charge_type"),
    [
        ("appointment", ChargeType.CONSULTATION),
        ("lab_order_item", ChargeType.LABORATORY),
        ("dispense_item", ChargeType.PHARMACY),
        ("bed_assignment", ChargeType.ADMISSION),
    ],
)
def test_system_charges_are_never_edited(make_patient, accountant, source_type, charge_type):
    charge = services.post_charge(
        patient=make_patient(),
        charge_type=charge_type,
        description="From another module",
        quantity=1,
        unit_price="1500.00",
        source_type=source_type,
        source_id=7,
        acting_user=accountant,
    )
    assert not charge.is_manual and not charge.is_editable

    with pytest.raises(ValidationError) as error:
        edit(charge, accountant, unit_price="1.00", charge_type=ChargeType.OTHER)
    assert error.value.messages == [
        "System charges can't be edited — void the charge or apply a discount instead."
    ]


def test_consultation_type_is_refused(make_charge, accountant):
    with pytest.raises(ValidationError) as error:
        edit(make_charge(), accountant, charge_type=ChargeType.CONSULTATION)
    assert "charge_type" in error.value.error_dict


def test_reason_is_required(make_charge, accountant):
    with pytest.raises(ValidationError) as error:
        edit(make_charge(), accountant, unit_price="5.00", reason="   ")
    assert "reason" in error.value.error_dict


def test_edit_that_changes_nothing_is_refused(make_charge, accountant):
    charge = make_charge()
    with pytest.raises(ValidationError, match="Nothing to change"):
        edit(charge, accountant, description=f"  {charge.description} ")
    assert not AuditLog.objects.filter(event="billing.charge.edited").exists()


# --- Possible duplicates ----------------------------------------------------------------


def test_same_charge_with_different_case_and_spacing_is_blocked(make_patient, accountant):
    patient = make_patient()
    add(patient, accountant)

    with pytest.raises(ValidationError) as error:
        add(patient, accountant, description="  WOUND   dressing ")
    assert error.value.code == "possible_duplicate"
    assert "Wound dressing × 1, Rs. 500.00" in error.value.messages[0]
    assert "(unbilled)" in error.value.messages[0]
    assert Charge.objects.filter(patient=patient).count() == 1


def test_confirmed_duplicate_is_added_and_noted_in_the_audit_log(make_patient, accountant):
    patient = make_patient()
    add(patient, accountant)

    add(patient, accountant, confirm_duplicate=True)

    assert Charge.objects.filter(patient=patient).count() == 2
    messages = list(
        AuditLog.objects.filter(event="billing.charge.posted")
        .order_by("id")
        .values_list("message", flat=True)
    )
    assert "(added despite possible duplicate)" not in messages[0]
    assert messages[1].endswith("(added despite possible duplicate)")


@pytest.mark.parametrize(
    "change",
    [
        {"unit_price": Decimal("450.00")},
        {"charge_type": ChargeType.LABORATORY},
        {"description": "Wound dressing (large)"},
    ],
)
def test_different_price_type_or_description_is_not_a_duplicate(make_patient, accountant, change):
    patient = make_patient()
    add(patient, accountant)
    add(patient, accountant, **change)
    assert Charge.objects.filter(patient=patient).count() == 2


def test_other_patients_and_voided_charges_are_not_duplicates(make_patient, accountant):
    first, second = make_patient(), make_patient()
    voided = add(first, accountant)
    services.void_charge(voided, reason="Wrong patient", acting_user=accountant)

    add(second, accountant)  # same charge, other patient
    add(first, accountant)  # the earlier one was voided
    assert Charge.objects.filter(is_voided=False).count() == 2


def test_system_charges_are_not_duplicates(make_patient, accountant):
    patient = make_patient()
    services.post_charge(
        patient=patient,
        charge_type=ChargeType.OTHER,
        description="Wound dressing",
        quantity=1,
        unit_price="500.00",
        source_type="test_source",
        source_id=1,
        acting_user=accountant,
    )
    add(patient, accountant)
    assert Charge.objects.filter(patient=patient).count() == 2


def _charge_on_issued_invoice(patient, user, created_at):
    charge = add(patient, user, now=created_at)
    invoice = services.create_invoice(patient=patient, charge_ids=[charge.pk], acting_user=user)
    services.issue_invoice(invoice, acting_user=user)
    # created_at is auto_now_add; move it to the day the test needs.
    Charge.objects.filter(pk=charge.pk).update(created_at=created_at)
    charge.refresh_from_db()
    return charge


def test_match_on_an_issued_invoice_from_yesterday_is_not_a_duplicate(make_patient, accountant):
    patient = make_patient()
    _charge_on_issued_invoice(patient, accountant, NOON - timedelta(days=1))

    add(patient, accountant, now=NOON)
    assert Charge.objects.filter(patient=patient).count() == 2


def test_match_on_an_issued_invoice_from_today_is_a_duplicate(make_patient, accountant):
    patient = make_patient()
    old = _charge_on_issued_invoice(patient, accountant, NOON - timedelta(hours=3))

    with pytest.raises(ValidationError) as error:
        add(patient, accountant, now=NOON)
    assert error.value.code == "possible_duplicate"
    assert old.invoice.number in error.value.messages[0]


def test_today_uses_the_local_calendar_day(make_patient, accountant):
    # 00:30 in Colombo on 7 Oct is still 6 Oct in UTC, but it is "today" at noon.
    patient = make_patient()
    early = timezone.make_aware(datetime(2026, 10, 7, 0, 30))
    _charge_on_issued_invoice(patient, accountant, early)

    with pytest.raises(ValidationError):
        add(patient, accountant, now=NOON)


def test_duplicate_check_also_applies_to_draft_invoice_lines(make_invoice, accountant):
    invoice = make_invoice(amounts=())
    add(invoice.patient, accountant, invoice=invoice)
    with pytest.raises(ValidationError) as error:
        add(invoice.patient, accountant, invoice=invoice)
    assert error.value.code == "possible_duplicate"


# --- Discount accountability -------------------------------------------------------------


def test_discount_records_who_and_when_and_reset_clears_it(make_invoice, accountant):
    invoice = make_invoice(amounts=("1000.00",))

    services.set_discount(
        invoice, amount="100", reason="Senior citizen", acting_user=accountant, now=NOON
    )
    invoice.refresh_from_db()
    assert invoice.discount_set_by == accountant
    assert invoice.discount_set_at == NOON

    services.set_discount(invoice, amount="0", reason="", acting_user=accountant)
    invoice.refresh_from_db()
    assert invoice.discount_set_by is None and invoice.discount_set_at is None


def test_discount_line_shows_who_set_it_on_detail_and_print(
    client_for_role, make_invoice, make_user
):
    accountant = make_user(role=Role.ACCOUNTANT, first_name="Nadee", last_name="Perera")
    invoice = make_invoice(amounts=("1000.00",))
    services.set_discount(
        invoice, amount="150", reason="Staff family", acting_user=accountant, now=NOON
    )
    client = client_for_role(Role.ADMIN)

    expected = "Discount: Rs. 150.00 — Staff family — set by Nadee Perera on 07 Oct 2026 12:00"
    for name in ("billing:invoice_detail", "billing:invoice_print"):
        page = client.get(reverse(name, args=[invoice.pk])).content.decode()
        assert expected in page


def test_older_discount_without_setter_omits_set_by(client_for_role, make_invoice):
    invoice = make_invoice(amounts=("1000.00",), discount=Decimal("100"), discount_reason="Old")
    page = client_for_role(Role.ADMIN).get(reverse("billing:invoice_detail", args=[invoice.pk]))
    text = page.content.decode()
    assert "Discount: Rs. 100.00 — Old" in text
    assert "set by" not in text


@pytest.mark.parametrize("role", [Role.ADMIN, Role.RECEPTIONIST])
def test_admin_and_receptionist_still_cannot_discount(client_for_role, make_invoice, role):
    invoice = make_invoice(amounts=("1000.00",))
    response = client_for_role(role).post(
        reverse("billing:invoice_discount", args=[invoice.pk]), {"amount": "10", "reason": "x"}
    )
    assert response.status_code == 403
    invoice.refresh_from_db()
    assert invoice.discount == 0


# --- Pages without JavaScript ---------------------------------------------------------------


class FormNesting(HTMLParser):
    """Records whether any <form> opens while another is still open."""

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.nested = False
        self.count = 0

    def handle_starttag(self, tag, attrs):
        if tag == "form":
            self.count += 1
            self.nested = self.nested or self.depth > 0
            self.depth += 1

    def handle_endtag(self, tag):
        if tag == "form":
            self.depth -= 1


def assert_no_nested_forms(html):
    parser = FormNesting()
    parser.feed(html)
    assert parser.count > 0
    assert not parser.nested
    assert parser.depth == 0


@pytest.mark.parametrize("role", [Role.ACCOUNTANT, Role.ADMIN, Role.RECEPTIONIST])
def test_patient_billing_page_has_no_prompt_and_no_nested_forms(
    client_for_role, make_charge, make_patient, role
):
    patient = make_patient()
    make_charge(patient=patient)
    make_charge(patient=patient, unit_price="20")
    html = (
        client_for_role(role)
        .get(reverse("billing:patient_billing", args=[patient.pk]))
        .content.decode()
    )
    assert "prompt(" not in html
    assert_no_nested_forms(html)


def test_draft_invoice_page_has_no_nested_forms(client_for_role, make_invoice):
    invoice = make_invoice(amounts=("100", "200"))
    html = (
        client_for_role(Role.ACCOUNTANT)
        .get(reverse("billing:invoice_detail", args=[invoice.pk]))
        .content.decode()
    )
    assert "prompt(" not in html
    assert_no_nested_forms(html)


def test_edit_and_void_links_follow_roles_and_editability(
    client_for_role, make_charge, make_patient, accountant
):
    patient = make_patient()
    manual = make_charge(patient=patient)
    system = services.post_charge(
        patient=patient,
        charge_type=ChargeType.LABORATORY,
        description="FBC",
        quantity=1,
        unit_price="900",
        source_type="lab_order_item",
        source_id=3,
        acting_user=accountant,
    )
    url = reverse("billing:patient_billing", args=[patient.pk])
    edit_url = lambda c: reverse("billing:charge_edit", args=[c.pk])  # noqa: E731
    void_url = lambda c: reverse("billing:charge_void", args=[c.pk])  # noqa: E731

    html = client_for_role(Role.ACCOUNTANT).get(url).content.decode()
    assert edit_url(manual) in html and edit_url(system) not in html
    assert void_url(manual) in html and void_url(system) in html

    html = client_for_role(Role.ADMIN).get(url).content.decode()
    assert edit_url(manual) not in html and void_url(manual) in html

    html = client_for_role(Role.RECEPTIONIST).get(url).content.decode()
    assert edit_url(manual) not in html and void_url(manual) not in html


def test_void_works_with_a_plain_get_then_post(client_for_role, make_charge):
    charge = make_charge()
    client = client_for_role(Role.ACCOUNTANT)
    url = reverse("billing:charge_void", args=[charge.pk])

    page = client.get(url)
    assert page.status_code == 200
    assert 'name="reason"' in page.content.decode()

    missing = client.post(url, {"reason": ""})
    assert missing.status_code == 200  # shown again with the field error
    charge.refresh_from_db()
    assert not charge.is_voided

    response = client.post(url, {"reason": "Entered twice"})
    assert response.url == reverse("billing:patient_billing", args=[charge.patient_id])
    charge.refresh_from_db()
    assert charge.is_voided and charge.void_reason == "Entered twice"


def test_voiding_a_draft_line_returns_to_the_invoice(client_for_role, make_invoice):
    invoice = make_invoice(amounts=("100",))
    charge = invoice.charges.get()
    response = client_for_role(Role.ADMIN).post(
        reverse("billing:charge_void", args=[charge.pk]), {"reason": "Not given"}
    )
    assert response.url == reverse("billing:invoice_detail", args=[invoice.pk])


def test_edit_works_with_a_plain_get_then_post(client_for_role, make_invoice):
    invoice = make_invoice(amounts=("100",))
    charge = invoice.charges.get()
    client = client_for_role(Role.ACCOUNTANT)
    url = reverse("billing:charge_edit", args=[charge.pk])

    page = client.get(url).content.decode()
    assert 'value="Test charge"' in page

    response = client.post(
        url,
        {
            "description": "Test charge",
            "charge_type": "OTHER",
            "quantity": "2",
            "unit_price": "100",
            "reason": "Two were given",
        },
    )
    assert response.url == reverse("billing:invoice_detail", args=[invoice.pk])
    charge.refresh_from_db()
    assert charge.amount == Decimal("200.00")


def test_edit_page_shows_service_errors_on_the_form(client_for_role, make_charge):
    charge = make_charge()
    response = client_for_role(Role.ACCOUNTANT).post(
        reverse("billing:charge_edit", args=[charge.pk]),
        {
            "description": charge.description,
            "charge_type": charge.charge_type,
            "quantity": charge.quantity,
            "unit_price": charge.unit_price,
            "reason": "No real change",
        },
    )
    assert response.status_code == 200
    assert "Nothing to change" in response.content.decode()


@pytest.mark.parametrize("page", ["billing:charge_edit", "billing:charge_void"])
def test_pages_for_blocked_charges_redirect_with_the_reason(
    client_for_role, make_invoice, accountant, page
):
    invoice = make_invoice(amounts=("100",))
    services.issue_invoice(invoice, acting_user=accountant)
    charge = invoice.charges.get()

    response = client_for_role(Role.ACCOUNTANT).get(reverse(page, args=[charge.pk]), follow=True)
    assert response.redirect_chain[-1][0] == reverse("billing:invoice_detail", args=[invoice.pk])
    assert "issued invoice" in response.content.decode()


def test_edit_page_redirects_for_system_charges(client_for_role, make_patient, accountant):
    charge = services.post_charge(
        patient=make_patient(),
        charge_type=ChargeType.PHARMACY,
        description="Medicines",
        quantity=1,
        unit_price="50",
        source_type="dispense_item",
        source_id=9,
        acting_user=accountant,
    )
    response = client_for_role(Role.ACCOUNTANT).get(
        reverse("billing:charge_edit", args=[charge.pk]), follow=True
    )
    assert "System charges can&#x27;t be edited" in response.content.decode()


def test_unknown_charge_is_404(client_for_role):
    client = client_for_role(Role.ACCOUNTANT)
    assert client.get(reverse("billing:charge_edit", args=[999])).status_code == 404
    assert client.get(reverse("billing:charge_void", args=[999])).status_code == 404


# --- Duplicate flow through the pages ---------------------------------------------------------


DUPLICATE_POST = {
    "charge_type": "OTHER",
    "description": "Wound dressing",
    "quantity": "1",
    "unit_price": "500",
}


def test_patient_page_duplicate_flow(client_for_role, make_patient):
    patient = make_patient()
    client = client_for_role(Role.RECEPTIONIST)
    url = reverse("billing:patient_charge_add", args=[patient.pk])
    client.post(url, DUPLICATE_POST)

    again = client.post(url, {**DUPLICATE_POST, "description": "wound DRESSING"})
    html = again.content.decode()
    assert again.status_code == 200
    assert "Possible duplicate" in html
    assert 'name="confirm_duplicate"' in html
    assert 'value="wound DRESSING"' in html  # the bound form keeps what was typed
    assert Charge.objects.filter(patient=patient).count() == 1

    confirmed = client.post(url, {**DUPLICATE_POST, "confirm_duplicate": "on"})
    assert confirmed.status_code == 302
    assert Charge.objects.filter(patient=patient).count() == 2


def test_checkbox_is_hidden_until_a_duplicate_is_found(client_for_role, make_patient):
    patient = make_patient()
    html = (
        client_for_role(Role.RECEPTIONIST)
        .get(reverse("billing:patient_billing", args=[patient.pk]))
        .content.decode()
    )
    assert 'name="confirm_duplicate"' not in html


def test_invoice_page_duplicate_flow(client_for_role, make_invoice):
    invoice = make_invoice(amounts=())
    client = client_for_role(Role.ACCOUNTANT)
    url = reverse("billing:invoice_charge_add", args=[invoice.pk])
    client.post(url, DUPLICATE_POST)

    again = client.post(url, DUPLICATE_POST)
    assert again.status_code == 200
    assert 'name="confirm_duplicate"' in again.content.decode()
    assert invoice.charges.count() == 1

    client.post(url, {**DUPLICATE_POST, "confirm_duplicate": "on"})
    assert invoice.charges.count() == 2
    assert invoice.status == InvoiceStatus.DRAFT
