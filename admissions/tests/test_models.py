from datetime import UTC, datetime
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from admissions.models import Admission, Bed, BedAssignment, Ward, nights_between
from admissions.tests.clock import colombo

# --- nights_between (pure function) ---------------------------------------------------


def test_same_day_is_zero_nights():
    assert nights_between(colombo(2026, 10, 10, 8), colombo(2026, 10, 10, 20)) == 0


def test_just_past_midnight_is_one_night():
    assert nights_between(colombo(2026, 10, 10, 23, 50), colombo(2026, 10, 11, 0, 10)) == 1


def test_three_nights():
    assert nights_between(colombo(2026, 10, 10, 15), colombo(2026, 10, 13, 9)) == 3


def test_never_negative():
    assert nights_between(colombo(2026, 10, 12), colombo(2026, 10, 10)) == 0


def test_uses_colombo_dates_not_utc():
    # 03:00 and 23:00 on 10 Oct in Colombo are 21:30 on 9 Oct and 17:30 on 10 Oct in
    # UTC. UTC dates differ, Colombo dates don't: still 0 nights.
    start = datetime(2026, 10, 9, 21, 30, tzinfo=UTC)
    end = datetime(2026, 10, 10, 17, 30, tzinfo=UTC)

    assert nights_between(start, end) == 0


# --- Constraints ----------------------------------------------------------------------


@pytest.mark.django_db
def test_ward_name_unique_ignoring_case(make_ward):
    make_ward(name="General Ward A")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_ward(name="GENERAL WARD A")


@pytest.mark.django_db
def test_daily_rate_not_negative(make_ward):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_ward(daily_rate="-1")


@pytest.mark.django_db
def test_bed_number_unique_per_ward_ignoring_case(make_ward):
    ward = make_ward()
    Bed.objects.create(ward=ward, bed_number="B12")

    with pytest.raises(IntegrityError), transaction.atomic():
        Bed.objects.create(ward=ward, bed_number="b12")
    Bed.objects.create(ward=make_ward(), bed_number="B12")  # another ward: fine


@pytest.mark.django_db
def test_one_current_admission_per_patient(make_admission, make_patient):
    patient = make_patient()
    make_admission(patient=patient)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_admission(patient=patient)


@pytest.mark.django_db
def test_one_open_assignment_per_bed(make_admission, make_bed):
    bed = make_bed()
    make_admission(bed=bed)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_admission(bed=bed)


@pytest.mark.django_db
def test_one_open_assignment_per_admission(make_admission, make_bed):
    admission = make_admission()

    with pytest.raises(IntegrityError), transaction.atomic():
        BedAssignment.objects.create(
            admission=admission,
            bed=make_bed(),
            daily_rate=Decimal("1"),
            started_at=admission.admitted_at,
        )


@pytest.mark.django_db
def test_discharge_fields_must_be_consistent(make_admission):
    admission = make_admission()

    with pytest.raises(IntegrityError), transaction.atomic():
        Admission.objects.filter(pk=admission.pk).update(status="DISCHARGED")


@pytest.mark.django_db
def test_opd_admission_needs_appointment(make_admission):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_admission(source="OPD")


@pytest.mark.django_db
def test_number_and_length_of_stay(make_admission):
    admission = make_admission(admitted_at=colombo(2026, 10, 8, 10))

    assert admission.number == f"ADM-{admission.pk:06d}"
    assert admission.length_of_stay_days(now=colombo(2026, 10, 8, 18)) == 1  # minimum 1
    assert admission.length_of_stay_days(now=colombo(2026, 10, 11, 9)) == 3
    assert str(admission.current_bed).startswith(Ward.objects.get().name)
