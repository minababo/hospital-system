from datetime import date, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from patients.models import Patient

pytestmark = pytest.mark.django_db


def new_patient(**overrides):
    data = {
        "first_name": "Kamal",
        "last_name": "Perera",
        "date_of_birth": date(1985, 6, 15),
        "gender": Patient.Gender.MALE,
        "phone": "0771234567",
        "address": "10 Galle Road, Colombo",
    }
    data.update(overrides)
    return Patient(**data)


def test_valid_patient_passes_full_clean():
    patient = new_patient(first_name="  Kamal ")
    patient.full_clean()

    assert patient.first_name == "Kamal"
    assert patient.blood_group == Patient.BloodGroup.UNKNOWN


def test_future_date_of_birth_rejected():
    patient = new_patient(date_of_birth=timezone.localdate() + timedelta(days=1))

    with pytest.raises(ValidationError) as excinfo:
        patient.full_clean()
    assert "date_of_birth" in excinfo.value.message_dict


def test_date_of_birth_over_130_years_ago_rejected():
    patient = new_patient(date_of_birth=date(1850, 1, 1))

    with pytest.raises(ValidationError, match="130 years"):
        patient.full_clean()


def test_phone_numbers_are_normalized():
    patient = new_patient(phone="077 123-4567", emergency_contact_phone="+94 71 234 5678")
    patient.full_clean()

    assert patient.phone == "0771234567"
    assert patient.emergency_contact_phone == "+94712345678"


def test_invalid_emergency_phone_rejected():
    with pytest.raises(ValidationError) as excinfo:
        new_patient(emergency_contact_phone="123").full_clean()
    assert "emergency_contact_phone" in excinfo.value.message_dict


def test_invalid_nic_rejected():
    with pytest.raises(ValidationError) as excinfo:
        new_patient(nic="12345").full_clean()
    assert "nic" in excinfo.value.message_dict


def test_nic_is_normalized_and_blank_becomes_null():
    with_nic = new_patient(nic=" 123456789v ")
    with_nic.full_clean()
    without_nic = new_patient(nic="")
    without_nic.full_clean()

    assert with_nic.nic == "123456789V"
    assert without_nic.nic is None


def test_nic_unique_ignoring_case(make_patient):
    make_patient(nic="123456789V")

    with pytest.raises(ValidationError, match="already registered"):
        new_patient(nic="123456789v").full_clean()
    # make_patient() skips full_clean(), so this reaches the database index.
    with pytest.raises(IntegrityError), transaction.atomic():
        make_patient(nic="123456789v")


def test_many_patients_without_nic_allowed():
    for _ in range(2):
        patient = new_patient(nic="")
        patient.full_clean()
        patient.save()

    assert Patient.objects.filter(nic__isnull=True).count() == 2


def test_mrn_comes_from_primary_key(make_patient):
    patient = make_patient()

    assert patient.mrn == f"P{patient.pk:06d}"
    assert new_patient().mrn is None
    assert str(patient) == f"{patient.full_name} ({patient.mrn})"


def test_age(monkeypatch):
    monkeypatch.setattr(timezone, "localdate", lambda: date(2026, 10, 2))

    assert new_patient(date_of_birth=date(1990, 10, 2)).age == 36
    assert new_patient(date_of_birth=date(1990, 10, 3)).age == 35
