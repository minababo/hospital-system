from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from billing.models import Invoice, Payment

pytestmark = pytest.mark.django_db


def test_amount_must_equal_quantity_times_unit_price(make_charge):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(quantity=2, unit_price="100.00", amount=Decimal("150.00"))


def test_amount_check_handles_cents_exactly(make_charge):
    charge = make_charge(quantity=3, unit_price="33.33")  # 99.99, a float-unfriendly product

    assert charge.amount == Decimal("99.99")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quantity": 0, "unit_price": "10.00", "amount": Decimal("0.00")},
        {"unit_price": "-1.00", "amount": Decimal("-1.00")},
    ],
    ids=["zero-quantity", "negative-price"],
)
def test_quantity_and_price_constraints(make_charge, kwargs):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(**kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [{"source_type": "appointment"}, {"source_id": 5}],
    ids=["type-without-id", "id-without-type"],
)
def test_source_fields_both_or_neither(make_charge, kwargs):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(**kwargs)


def test_unique_live_source_but_voided_allows_repost(make_charge, make_patient):
    patient = make_patient()
    first = make_charge(patient=patient, source_type="appointment", source_id=1)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_charge(patient=patient, source_type="appointment", source_id=1)

    first.is_voided = True
    first.save()
    make_charge(patient=patient, source_type="appointment", source_id=1)  # allowed now


def test_discount_needs_reason(make_patient):
    patient = make_patient()

    with pytest.raises(IntegrityError), transaction.atomic():
        Invoice.objects.create(patient=patient, discount=Decimal("100"))
    Invoice.objects.create(patient=patient, discount=Decimal("100"), discount_reason="Staff")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"amount": Decimal("0"), "method": "CASH"},
        {"amount": Decimal("100"), "method": "CARD", "reference": ""},
    ],
    ids=["zero-amount", "card-without-reference"],
)
def test_payment_constraints(make_invoice, make_user, kwargs):
    invoice = make_invoice()

    with pytest.raises(IntegrityError), transaction.atomic():
        Payment.objects.create(invoice=invoice, received_by=make_user(), **kwargs)


def test_numbers_and_computed_totals(make_invoice, make_user):
    invoice = make_invoice(
        amounts=["1000.00", "500.50"], discount=Decimal("100"), discount_reason="x"
    )
    Payment.objects.create(
        invoice=invoice, amount=Decimal("400"), method="CASH", received_by=make_user()
    )

    assert invoice.number == f"INV-{invoice.pk:06d}"
    assert invoice.subtotal == Decimal("1500.50")
    assert invoice.total == Decimal("1400.50")
    assert invoice.amount_paid == Decimal("400.00")
    assert invoice.balance == Decimal("1000.50")
    assert invoice.payments.get().receipt_number.startswith("RCT-")
