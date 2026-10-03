from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from appointments.models import Status
from billing import services
from billing.models import Charge, ChargeType, Invoice, InvoiceStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def cashier(make_user):
    return make_user()  # receptionist


def post(patient, user, **kwargs):
    data = {
        "charge_type": ChargeType.LABORATORY,
        "description": "Full blood count",
        "quantity": 1,
        "unit_price": "1500",
    }
    data.update(kwargs)
    return services.post_charge(patient=patient, acting_user=user, **data)


# --- post_charge ---------------------------------------------------------------


def test_post_charge_computes_amount(make_patient, cashier):
    charge = post(make_patient(), cashier, quantity=3, unit_price="33.333")

    assert charge.unit_price == Decimal("33.33")
    assert charge.amount == Decimal("99.99")
    assert charge.created_by == cashier


def test_post_charge_is_idempotent_per_source(make_patient, cashier):
    patient = make_patient()
    first = post(patient, cashier, source_type="lab_order", source_id=7)

    again = post(patient, cashier, source_type="lab_order", source_id=7, unit_price="9999")

    assert again == first
    assert again.unit_price == Decimal("1500.00")
    assert Charge.objects.count() == 1


def test_post_charge_race_returns_existing(make_patient, make_charge, cashier, monkeypatch):
    patient = make_patient()
    existing = make_charge(patient=patient, source_type="lab_order", source_id=7)
    # Simulate a race: the pre-check didn't see the other request's charge yet.
    real_lookup = services._live_charge_for
    calls = iter([None])
    monkeypatch.setattr(
        services, "_live_charge_for", lambda *args: next(calls, None) or real_lookup(*args)
    )

    charge = post(patient, cashier, source_type="lab_order", source_id=7)

    assert charge == existing
    assert Charge.objects.count() == 1


def test_post_charge_rejects_bad_input(make_patient, cashier):
    with pytest.raises(ValidationError):
        post(make_patient(), cashier, quantity=0)
    with pytest.raises(ValidationError):
        post(make_patient(), cashier, unit_price="-5")
    with pytest.raises(ValidationError):
        post(make_patient(), cashier, source_type="lab_order")  # no id


# --- Consultation capture -----------------------------------------------------


def test_capture_uses_fee_snapshot_and_is_idempotent(make_completed_appointment, cashier):
    appointment = make_completed_appointment()
    appointment.doctor.consultation_fee = Decimal("9999")
    appointment.doctor.save()

    first = services.capture_consultation_charges(patient=appointment.patient, acting_user=cashier)
    second = services.capture_consultation_charges(patient=appointment.patient, acting_user=cashier)

    assert len(first) == 1 and second == []
    charge = first[0]
    assert charge.unit_price == appointment.consultation_fee == Decimal("1500.00")
    assert charge.charge_type == ChargeType.CONSULTATION
    assert charge.source_type == "appointment" and charge.source_id == appointment.pk
    assert charge.description.startswith("Consultation — Dr.")


@pytest.mark.parametrize(
    "status", [Status.BOOKED, Status.CHECKED_IN, Status.CANCELLED, Status.NO_SHOW]
)
def test_capture_ignores_appointments_that_are_not_completed(make_appointment, cashier, status):
    appointment = make_appointment(status=status)

    assert (
        services.capture_consultation_charges(patient=appointment.patient, acting_user=cashier)
        == []
    )


# --- Invoices -------------------------------------------------------------------


def test_create_invoice_with_selected_charges_and_pending_consultations(
    make_completed_appointment, make_charge, cashier
):
    appointment = make_completed_appointment()
    patient = appointment.patient
    lab = make_charge(patient=patient, unit_price="500")
    left_out = make_charge(patient=patient, unit_price="200")

    invoice = services.create_invoice(patient=patient, charge_ids=[lab.pk], acting_user=cashier)

    assert invoice.status == InvoiceStatus.DRAFT
    assert set(invoice.charges.values_list("charge_type", flat=True)) == {"OTHER", "CONSULTATION"}
    assert invoice.subtotal == Decimal("2000.00")
    left_out.refresh_from_db()
    assert left_out.invoice is None


def test_create_invoice_rejections(make_patient, make_charge, make_invoice, cashier):
    patient = make_patient()
    other = make_charge()
    voided = make_charge(patient=patient, is_voided=True)
    invoiced = make_invoice(patient=patient).charges.get()

    for ids, message in [
        ([], "at least one"),
        ([other.pk], "another patient"),
        ([voided.pk], "voided"),
        ([invoiced.pk], "already on an invoice"),
    ]:
        with pytest.raises(ValidationError, match=message):
            services.create_invoice(patient=patient, charge_ids=ids, acting_user=cashier)


def test_remove_charge_from_draft_makes_it_unbilled(make_invoice, cashier):
    invoice = make_invoice(amounts=["100", "200"])
    charge = invoice.charges.first()

    services.remove_charge_from_invoice(invoice, charge, acting_user=cashier)

    charge.refresh_from_db()
    assert charge.invoice is None
    assert invoice.charges.count() == 1


def test_discount_rules(make_invoice, cashier):
    invoice = make_invoice(amounts=["1000"])

    with pytest.raises(ValidationError, match="reason"):
        services.set_discount(invoice, amount="100", reason="", acting_user=cashier)
    with pytest.raises(ValidationError, match="more than the subtotal"):
        services.set_discount(invoice, amount="1000.01", reason="x", acting_user=cashier)

    services.set_discount(invoice, amount="250", reason="Senior citizen", acting_user=cashier)
    invoice.refresh_from_db()
    assert invoice.total == Decimal("750.00")

    services.set_discount(invoice, amount="0", reason="ignored", acting_user=cashier)
    invoice.refresh_from_db()
    assert invoice.discount_reason == ""


def test_issue_rules(make_invoice, make_patient, cashier):
    empty = Invoice.objects.create(patient=make_patient())
    with pytest.raises(ValidationError, match="at least one charge"):
        services.issue_invoice(empty, acting_user=cashier)

    invoice = make_invoice()
    services.issue_invoice(invoice, acting_user=cashier)
    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.ISSUED
    assert invoice.issued_by == cashier

    with pytest.raises(ValidationError, match="Only draft"):
        services.issue_invoice(invoice, acting_user=cashier)


def test_fully_discounted_invoice_is_paid_on_issue(make_invoice, cashier):
    invoice = make_invoice(amounts=["500"], discount=Decimal("500"), discount_reason="Charity")

    services.issue_invoice(invoice, acting_user=cashier)

    invoice.refresh_from_db()
    assert invoice.status == InvoiceStatus.PAID


def test_issued_invoice_cannot_be_edited(make_invoice, cashier):
    invoice = make_invoice()
    services.issue_invoice(invoice, acting_user=cashier)
    charge = invoice.charges.get()

    for action in (
        lambda: services.remove_charge_from_invoice(invoice, charge, acting_user=cashier),
        lambda: services.set_discount(invoice, amount="10", reason="x", acting_user=cashier),
        lambda: services.add_manual_charge(
            patient=invoice.patient,
            charge_type="OTHER",
            description="x",
            quantity=1,
            unit_price="10",
            invoice=invoice,
            acting_user=cashier,
        ),
        lambda: services.void_charge(charge, reason="x", acting_user=cashier),
    ):
        with pytest.raises(ValidationError):
            action()


def test_void_charge(make_charge, make_invoice, cashier):
    unbilled = make_charge()
    with pytest.raises(ValidationError, match="reason"):
        services.void_charge(unbilled, reason=" ", acting_user=cashier)
    services.void_charge(unbilled, reason="Entered twice", acting_user=cashier)
    unbilled.refresh_from_db()
    assert unbilled.is_voided and unbilled.voided_by == cashier

    on_draft = make_invoice().charges.get()
    services.void_charge(on_draft, reason="Wrong test", acting_user=cashier)
    on_draft.refresh_from_db()
    assert on_draft.is_voided and on_draft.invoice is None


# --- Payments -------------------------------------------------------------------


@pytest.fixture
def issued(make_invoice, cashier):
    invoice = make_invoice(amounts=["1000"])
    services.issue_invoice(invoice, acting_user=cashier)
    return invoice


def pay(invoice, user, amount, method="CASH", reference=""):
    return services.record_payment(
        invoice=invoice, amount=amount, method=method, reference=reference, acting_user=user
    )


def test_partial_then_full_payment(issued, cashier):
    pay(issued, cashier, "400")
    issued.refresh_from_db()
    assert issued.status == InvoiceStatus.PARTIALLY_PAID
    assert issued.balance == Decimal("600.00")

    pay(issued, cashier, "600", method="CARD", reference="SLIP-123")
    issued.refresh_from_db()
    assert issued.status == InvoiceStatus.PAID
    assert issued.balance == Decimal("0.00")


def test_payment_rejections(issued, make_invoice, cashier):
    with pytest.raises(ValidationError, match="more than the balance"):
        pay(issued, cashier, "1000.01")
    with pytest.raises(ValidationError, match="reference"):
        pay(issued, cashier, "100", method="BANK_TRANSFER")
    with pytest.raises(ValidationError, match="greater than zero"):
        pay(issued, cashier, "0")

    draft = make_invoice()
    with pytest.raises(ValidationError, match="issued, unpaid"):
        pay(draft, cashier, "100")

    pay(issued, cashier, "1000")
    with pytest.raises(ValidationError, match="issued, unpaid"):
        pay(issued, cashier, "1")  # already PAID


def test_void_payment_recalculates_status(issued, cashier, admin_user_obj):
    first = pay(issued, cashier, "400")
    second = pay(issued, cashier, "600")

    services.void_payment(second, reason="Card declined", acting_user=admin_user_obj)
    issued.refresh_from_db()
    assert issued.status == InvoiceStatus.PARTIALLY_PAID

    services.void_payment(first, reason="Refunded", acting_user=admin_user_obj)
    issued.refresh_from_db()
    assert issued.status == InvoiceStatus.ISSUED
    assert issued.balance == Decimal("1000.00")

    with pytest.raises(ValidationError, match="already voided"):
        services.void_payment(first, reason="again", acting_user=admin_user_obj)


def test_void_invoice_blocked_by_payments_then_allowed(issued, cashier, admin_user_obj):
    payment = pay(issued, cashier, "100")
    with pytest.raises(ValidationError, match="Void the payments first"):
        services.void_invoice(issued, reason="Wrong patient", acting_user=admin_user_obj)

    services.void_payment(payment, reason="Refund", acting_user=admin_user_obj)
    services.void_invoice(issued, reason="Wrong patient", acting_user=admin_user_obj)

    issued.refresh_from_db()
    assert issued.status == InvoiceStatus.VOID
    assert Charge.objects.filter(invoice__isnull=True, is_voided=False).count() == 1
    with pytest.raises(ValidationError, match="issued, unpaid"):
        pay(issued, cashier, "10")
