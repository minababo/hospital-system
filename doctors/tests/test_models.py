from datetime import time
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from accounts.models import Role
from doctors.forms import DepartmentForm
from doctors.models import Department, Doctor, DoctorSchedule, Weekday

# full_clean() queries the database for uniqueness and overlap checks.
pytestmark = pytest.mark.django_db

# --- Department -------------------------------------------------------------


def test_department_name_unique_ignoring_case_in_db(make_department):
    make_department(name="Cardiology")

    with pytest.raises(IntegrityError), transaction.atomic():
        Department.objects.create(name="CARDIOLOGY")


def test_department_name_unique_ignoring_case_in_form(make_department):
    make_department(name="Cardiology")

    form = DepartmentForm(data={"name": "  cardiology  "})

    assert not form.is_valid()
    assert "A department with this name already exists." in form.non_field_errors()


def test_department_clean_strips_name():
    department = Department(name="  Neurology ")
    department.full_clean()

    assert department.name == "Neurology"


# --- Doctor -----------------------------------------------------------------


def test_negative_fee_rejected_by_database(make_doctor):
    doctor = make_doctor()

    with pytest.raises(IntegrityError), transaction.atomic():
        Doctor.objects.filter(pk=doctor.pk).update(consultation_fee=Decimal("-1"))


def test_registration_number_unique_ignoring_case(make_doctor):
    make_doctor(registration_number="SLMC123")

    with pytest.raises(IntegrityError), transaction.atomic():
        make_doctor(registration_number="slmc123")


def test_clean_normalizes_registration_number_and_phone(make_user, make_department):
    doctor = Doctor(
        user=make_user(role=Role.DOCTOR),
        department=make_department(),
        specialization="Cardiology",
        registration_number="  slmc 42 ",
        phone="077 123-4567",
        consultation_fee=Decimal("2000"),
    )
    doctor.full_clean()

    assert doctor.registration_number == "SLMC 42"
    assert doctor.phone == "0771234567"


def test_clean_rejects_invalid_phone(make_doctor):
    doctor = make_doctor()
    doctor.phone = "12345"

    with pytest.raises(ValidationError) as excinfo:
        doctor.full_clean()
    assert "phone" in excinfo.value.message_dict


def test_clean_rejects_non_doctor_user(make_user, make_department):
    doctor = Doctor(
        user=make_user(role=Role.NURSE),
        department=make_department(),
        specialization="X",
        registration_number="R1",
        phone="0771234567",
        consultation_fee=Decimal("100"),
    )

    with pytest.raises(ValidationError, match="Doctor role"):
        doctor.full_clean()


def test_clean_rejects_inactive_department_on_create(make_user, make_department):
    doctor = Doctor(
        user=make_user(role=Role.DOCTOR),
        department=make_department(is_active=False),
        specialization="X",
        registration_number="R1",
        phone="0771234567",
        consultation_fee=Decimal("100"),
    )

    with pytest.raises(ValidationError) as excinfo:
        doctor.full_clean()
    assert "department" in excinfo.value.message_dict


def test_doctor_in_deactivated_department_can_still_be_edited(make_doctor):
    doctor = make_doctor()
    doctor.department.is_active = False
    doctor.department.save()

    doctor.specialization = "Updated"
    doctor.full_clean()  # does not raise: the department didn't change


def test_doctor_str_and_properties(make_doctor):
    doctor = make_doctor(user__first_name="Ana", user__last_name="Silva")

    assert str(doctor) == "Dr. Ana Silva"
    assert doctor.full_name == "Ana Silva"
    assert doctor.is_active is True


# --- DoctorSchedule ---------------------------------------------------------


def block(doctor, start, end, weekday=Weekday.MONDAY, **kwargs):
    return DoctorSchedule(
        doctor=doctor, weekday=weekday, start_time=time(*start), end_time=time(*end), **kwargs
    )


def test_end_before_start_rejected_by_database(make_doctor):
    with pytest.raises(IntegrityError), transaction.atomic():
        block(make_doctor(), (10, 0), (9, 0)).save()


def test_end_equal_to_start_rejected_by_full_clean(make_doctor):
    with pytest.raises(ValidationError, match="End time must be after start time"):
        block(make_doctor(), (9, 0), (9, 0)).full_clean()


def test_overlapping_block_rejected(make_doctor):
    doctor = make_doctor()
    block(doctor, (9, 0), (12, 0)).save()

    with pytest.raises(ValidationError, match="overlaps"):
        block(doctor, (11, 0), (13, 0)).full_clean()


def test_adjacent_block_allowed(make_doctor):
    doctor = make_doctor()
    block(doctor, (9, 0), (12, 0)).save()

    block(doctor, (12, 0), (14, 0)).full_clean()  # does not raise


def test_same_time_on_another_weekday_allowed(make_doctor):
    doctor = make_doctor()
    block(doctor, (9, 0), (12, 0)).save()

    block(doctor, (9, 0), (12, 0), weekday=Weekday.TUESDAY).full_clean()


def test_same_time_for_another_doctor_allowed(make_doctor):
    block(make_doctor(), (9, 0), (12, 0)).save()

    block(make_doctor(), (9, 0), (12, 0)).full_clean()


def test_inactive_block_does_not_block_a_new_one(make_doctor):
    doctor = make_doctor()
    block(doctor, (9, 0), (12, 0), is_active=False).save()

    block(doctor, (10, 0), (11, 0)).full_clean()


def test_editing_a_block_does_not_conflict_with_itself(make_doctor):
    existing = block(make_doctor(), (9, 0), (12, 0))
    existing.save()

    existing.end_time = time(13, 0)
    existing.full_clean()
