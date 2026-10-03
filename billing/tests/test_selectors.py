from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from billing import selectors, services
from billing.models import InvoiceStatus

pytestmark = pytest.mark.django_db


@pytest.fixture
def cashier(make_user):
    return make_user()


def issue_and_pay(invoice, user, *payments):
    services.issue_invoice(invoice, acting_user=user)
    for amount, method, reference in payments:
        services.record_payment(
            invoice=invoice, amount=amount, method=method, reference=reference, acting_user=user
        )


def test_list_totals_not_double_counted(make_invoice, cashier):
    # 3 charges x 2 payments would give 6 joined rows; totals must still be exact.
    invoice = make_invoice(amounts=["1000", "500", "250.50"])
    issue_and_pay(invoice, cashier, ("300", "CASH", ""), ("200", "CARD", "SLIP-1"))

    row = selectors.invoice_list().get(pk=invoice.pk)

    assert row.subtotal_amount == Decimal("1750.50")
    assert row.total_amount == Decimal("1750.50")
    assert row.paid_amount == Decimal("500.00")
    assert row.balance_amount == Decimal("1250.50")


def test_list_totals_ignore_voided_payments(make_invoice, cashier, admin_user_obj):
    invoice = make_invoice(amounts=["1000"])
    issue_and_pay(invoice, cashier, ("400", "CASH", ""))
    services.void_payment(invoice.payments.get(), reason="x", acting_user=admin_user_obj)

    assert selectors.invoice_list().get(pk=invoice.pk).paid_amount == Decimal("0.00")


def test_outstanding_filter(make_invoice, cashier):
    owing = make_invoice(amounts=["1000"])
    issue_and_pay(owing, cashier, ("100", "CASH", ""))
    paid = make_invoice(amounts=["500"])
    issue_and_pay(paid, cashier, ("500", "CASH", ""))
    make_invoice()  # draft

    assert list(selectors.invoice_list(outstanding_only=True)) == [owing]
    assert list(selectors.invoice_list(status=InvoiceStatus.PAID)) == [paid]


def test_search_by_invoice_number_and_patient(make_invoice, make_patient):
    kamal = make_invoice(patient=make_patient(first_name="Kamal"))
    other = make_invoice()

    assert list(selectors.invoice_list(q=kamal.number)) == [kamal]
    assert other in selectors.invoice_list(q=str(other.pk))
    assert list(selectors.invoice_list(q="kamal")) == [kamal]


def test_patient_billing_summary(make_completed_appointment, make_charge, make_invoice, cashier):
    appointment = make_completed_appointment()
    patient = appointment.patient
    make_charge(patient=patient, unit_price="200")
    owing = make_invoice(patient=patient, amounts=["1000"])
    issue_and_pay(owing, cashier, ("250", "CASH", ""))

    summary = selectors.patient_billing_summary(patient)

    assert len(summary["unbilled_charges"]) == 1
    assert summary["pending_consultations"] == [appointment]
    assert summary["total_outstanding"] == Decimal("750.00")


def test_payments_received_by_method(make_invoice, cashier, admin_user_obj):
    invoice = make_invoice(amounts=["5000"])
    issue_and_pay(
        invoice,
        cashier,
        ("1000", "CASH", ""),
        ("500", "CASH", ""),
        ("2000", "CARD", "SLIP-9"),
    )
    services.void_payment(invoice.payments.last(), reason="x", acting_user=admin_user_obj)
    today = timezone.localdate()

    result = selectors.payments_received(today, today)

    assert result["total"] == Decimal("1500.00")
    assert result["by_method"] == {"CASH": Decimal("1500.00")}
    assert selectors.payments_received(today - timedelta(days=9), today - timedelta(days=1))[
        "total"
    ] == Decimal("0.00")


def test_invoiced_by_charge_type(make_invoice, make_charge, make_patient, cashier):
    patient = make_patient()
    invoice = make_invoice(patient=patient, amounts=[])
    make_charge(patient=patient, invoice=invoice, charge_type="CONSULTATION", unit_price="1500")
    make_charge(patient=patient, invoice=invoice, charge_type="LABORATORY", unit_price="800")
    make_invoice(amounts=["999"])  # draft: not counted
    services.issue_invoice(invoice, acting_user=cashier)
    today = timezone.localdate()

    totals = selectors.invoiced_by_charge_type(today, today)

    assert totals["CONSULTATION"] == Decimal("1500.00")
    assert totals["LABORATORY"] == Decimal("800.00")
    assert totals["PHARMACY"] == Decimal("0.00")


def test_balance_after_payment(make_invoice, cashier):
    invoice = make_invoice(amounts=["1000"])
    issue_and_pay(invoice, cashier, ("300", "CASH", ""), ("200", "CASH", ""))
    first, second = selectors.get_invoice(invoice.pk).payments.all()

    assert selectors.balance_after_payment(first) == Decimal("700.00")
    assert selectors.balance_after_payment(second) == Decimal("500.00")
