from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from records.models import (
    Diagnosis,
    MedicalRecord,
    Prescription,
    PrescriptionItem,
    Vitals,
)

pytestmark = pytest.mark.django_db


def test_one_record_per_appointment(make_record):
    record = make_record()

    with pytest.raises(IntegrityError), transaction.atomic():
        make_record(appointment=record.appointment)


def test_record_patient_and_doctor_must_match_appointment(make_record, make_patient):
    record = make_record()
    record.patient = make_patient()

    with pytest.raises(ValidationError, match="must match the appointment"):
        record.full_clean()


def test_follow_up_must_be_after_appointment(make_record):
    record = make_record()
    record.follow_up_date = record.appointment.date

    with pytest.raises(ValidationError) as excinfo:
        record.full_clean()
    assert "follow_up_date" in excinfo.value.message_dict

    record.follow_up_date = record.appointment.date + timedelta(days=7)
    record.full_clean()  # does not raise


def test_only_one_primary_diagnosis(make_record):
    record = make_record()
    Diagnosis.objects.create(record=record, description="Asthma", diagnosis_type="PRIMARY")
    Diagnosis.objects.create(record=record, description="Rhinitis", diagnosis_type="SECONDARY")

    with pytest.raises(IntegrityError), transaction.atomic():
        Diagnosis.objects.create(record=record, description="Flu", diagnosis_type="PRIMARY")


@pytest.mark.parametrize("code", ["J45", "j45.909", "A00.1", "Z99.89"])
def test_valid_icd10_codes(make_record, code):
    diagnosis = Diagnosis(record=make_record(), description="x", icd10_code=code)
    diagnosis.full_clean()

    assert diagnosis.icd10_code == code.upper()


@pytest.mark.parametrize("code", ["J4", "45J", "J45.", "J45.12345", "JJ45"])
def test_invalid_icd10_codes(make_record, code):
    with pytest.raises(ValidationError) as excinfo:
        Diagnosis(record=make_record(), description="x", icd10_code=code).full_clean()
    assert "icd10_code" in excinfo.value.message_dict


# --- Vitals -----------------------------------------------------------------


def make_vitals(appointment, **values):
    return Vitals(appointment=appointment, patient=appointment.patient, **values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("bp_systolic", 300),
        ("pulse_bpm", 10),
        ("spo2_percent", 101),
        ("temperature_c", Decimal("46.0")),
        ("weight_kg", Decimal("0.1")),
        ("height_cm", Decimal("10")),
    ],
)
def test_vitals_out_of_range_rejected_by_database(make_checked_in_appointment, field, value):
    vitals = make_vitals(make_checked_in_appointment(), **{field: value})

    with pytest.raises(IntegrityError), transaction.atomic():
        vitals.save()


def test_systolic_must_exceed_diastolic(make_checked_in_appointment):
    vitals = make_vitals(make_checked_in_appointment(), bp_systolic=80, bp_diastolic=90)

    with pytest.raises(IntegrityError), transaction.atomic():
        vitals.save()


def test_vitals_need_at_least_one_value(make_checked_in_appointment):
    with pytest.raises(ValidationError, match="at least one"):
        make_vitals(make_checked_in_appointment()).full_clean()


def test_vitals_need_both_bp_values(make_checked_in_appointment):
    with pytest.raises(ValidationError, match="both systolic and diastolic"):
        make_vitals(make_checked_in_appointment(), bp_systolic=120).full_clean()


def test_bmi(make_checked_in_appointment):
    vitals = make_vitals(
        make_checked_in_appointment(), weight_kg=Decimal("70"), height_cm=Decimal("175")
    )

    assert vitals.bmi == Decimal("22.9")
    assert make_vitals(make_checked_in_appointment(), weight_kg=Decimal("70")).bmi is None


# --- Prescription items ------------------------------------------------------


@pytest.fixture
def prescription(make_record):
    record = make_record()
    return Prescription.objects.create(record=record, patient=record.patient, doctor=record.doctor)


def item(prescription, medicine, **kwargs):
    data = {"dose": "1 tablet", "frequency": "BD", "duration_days": 5, "quantity": 10}
    data.update(kwargs)
    return PrescriptionItem(prescription=prescription, medicine=medicine, **data)


@pytest.mark.parametrize("field", ["quantity", "duration_days"])
def test_item_quantity_and_duration_must_be_positive(prescription, make_medicine, field):
    with pytest.raises(IntegrityError), transaction.atomic():
        item(prescription, make_medicine(), **{field: 0}).save()


def test_same_medicine_twice_rejected(prescription, make_medicine):
    medicine = make_medicine()
    item(prescription, medicine).save()

    with pytest.raises(IntegrityError), transaction.atomic():
        item(prescription, medicine).save()


def test_str_methods(make_record):
    record = make_record()

    assert str(record).startswith("Consultation for")
    assert isinstance(MedicalRecord.objects.get(pk=record.pk).primary_diagnosis, type(None))
