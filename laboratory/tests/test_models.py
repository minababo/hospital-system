from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from laboratory.models import (
    LabOrder,
    LabOrderItem,
    LabResult,
    LabTest,
    LabTestParameter,
    compute_flag,
)

# --- compute_flag (pure function, no database) ------------------------------------


@pytest.mark.parametrize(
    ("value", "low", "high", "expected"),
    [
        ("11.9", "12", "16", "L"),
        ("16.1", "12", "16", "H"),
        ("12", "12", "16", "N"),  # boundaries count as normal
        ("16", "12", "16", "N"),
        ("14", "12", "16", "N"),
        ("0.5", "1", None, "L"),  # only a low limit
        ("99", "1", None, "N"),
        ("5.1", None, "5", "H"),  # only a high limit
        ("4", None, "5", "N"),
        ("4", None, None, ""),  # no range: nothing to compare
        (None, "12", "16", ""),  # text result / no numeric value
    ],
)
def test_compute_flag(value, low, high, expected):
    def as_decimal(x):
        return Decimal(x) if x is not None else None

    assert compute_flag(as_decimal(value), as_decimal(low), as_decimal(high)) == expected


# --- Constraints -----------------------------------------------------------------


@pytest.mark.django_db
def test_code_unique_ignoring_case(make_lab_test):
    make_lab_test(code="FBC")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_lab_test(code="fbc")


@pytest.mark.django_db
def test_price_not_negative(make_lab_test):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_lab_test(price=Decimal("-1"))


@pytest.mark.django_db
def test_reference_range_order(make_lab_test):
    test = make_lab_test(parameters=[])

    with pytest.raises(IntegrityError), transaction.atomic():
        LabTestParameter.objects.create(test=test, name="X", ref_low=10, ref_high=5)


@pytest.mark.django_db
def test_parameter_name_unique_per_test(make_lab_test):
    test = make_lab_test()  # has "Haemoglobin"

    with pytest.raises(IntegrityError), transaction.atomic():
        LabTestParameter.objects.create(test=test, name="HAEMOGLOBIN")
    # The same name on another test is fine.
    LabTestParameter.objects.create(test=make_lab_test(parameters=[]), name="Haemoglobin")


@pytest.mark.django_db
def test_order_needs_record_or_referrer(make_patient):
    with pytest.raises(IntegrityError), transaction.atomic():
        LabOrder.objects.create(patient=make_patient())


@pytest.mark.django_db
def test_duplicate_test_per_order_rejected(make_lab_order, make_lab_test):
    test = make_lab_test()
    order = make_lab_order(tests=[test])

    with pytest.raises(IntegrityError), transaction.atomic():
        LabOrderItem.objects.create(order=order, test=test, price=test.price)


@pytest.mark.django_db
def test_result_unique_per_item_and_parameter(make_lab_order, make_user):
    order = make_lab_order()
    item = order.items.get()
    parameter = item.test.parameters.get()

    LabResult.objects.create(
        item=item, parameter=parameter, value_numeric=1, entered_at=timezone.now()
    )

    with pytest.raises(IntegrityError), transaction.atomic():
        LabResult.objects.create(
            item=item, parameter=parameter, value_numeric=2, entered_at=timezone.now()
        )


@pytest.mark.django_db
def test_order_clean_requires_matching_record(make_record, make_patient):
    record = make_record()
    order = LabOrder(patient=make_patient(), record=record, ordering_doctor=record.doctor)

    with pytest.raises(Exception, match="must match the consultation"):
        order.full_clean()


@pytest.mark.django_db
def test_display_helpers(make_lab_test):
    test = make_lab_test(
        code="fbc",
        name="Full Blood Count",
        parameters=[
            {"name": "Haemoglobin", "unit": "g/dL", "ref_low": "12.0", "ref_high": "16.0"},
            {"name": "CRP", "unit": "mg/L", "ref_high": "5"},
            {"name": "Ferritin", "ref_low": "1"},
            {"name": "Culture", "result_type": "TEXT", "ref_text": "Negative"},
            {"name": "Notes", "result_type": "TEXT"},
        ],
    )
    test.full_clean()
    displays = [p.reference_display for p in test.parameters.all()]

    assert str(test) == "FBC — Full Blood Count"
    assert displays == ["12–16 g/dL", "< 5 mg/L", "> 1", "Negative", "—"]
    assert LabTest.objects.get(pk=test.pk).code == "fbc"  # create() doesn't run clean()
