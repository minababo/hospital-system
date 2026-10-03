from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.storage import default_storage
from django.utils import timezone

from accounts.models import Role
from appointments import services as appointment_services
from appointments.models import Status
from records import services
from records.models import (
    Diagnosis,
    MedicalRecord,
    Prescription,
    PrescriptionStatus,
    RecordReport,
    RecordStatus,
    Vitals,
)

pytestmark = pytest.mark.django_db


def doctor_of(record_or_appointment):
    return record_or_appointment.doctor.user


def add_primary(record, description="Asthma"):
    return services.add_diagnosis(
        record,
        data={"description": description, "diagnosis_type": "PRIMARY"},
        acting_user=doctor_of(record),
    )


def item_data(medicine, **overrides):
    data = {
        "medicine": medicine,
        "dose": "1 tablet",
        "frequency": "TDS",
        "route": "ORAL",
        "duration_days": 5,
        "quantity": 15,
        "instructions": "",
    }
    data.update(overrides)
    return data


def add_item(record, medicine, override=False, **overrides):
    return services.add_prescription_item(
        record,
        data=item_data(medicine, **overrides),
        allergy_override=override,
        acting_user=doctor_of(record),
    )


# --- start_consultation ------------------------------------------------------


def test_start_creates_draft_prefilled_from_reason(make_checked_in_appointment):
    appointment = make_checked_in_appointment(reason="Chest pain")

    record = services.start_consultation(
        appointment=appointment, acting_user=doctor_of(appointment)
    )

    assert record.status == RecordStatus.DRAFT
    assert record.presenting_complaint == "Chest pain"
    assert (record.patient, record.doctor) == (appointment.patient, appointment.doctor)


def test_start_is_idempotent(make_checked_in_appointment):
    appointment = make_checked_in_appointment()
    first = services.start_consultation(appointment=appointment, acting_user=doctor_of(appointment))

    second = services.start_consultation(
        appointment=appointment, acting_user=doctor_of(appointment)
    )

    assert first == second
    assert MedicalRecord.objects.count() == 1


def test_start_only_by_own_doctor(make_checked_in_appointment, make_doctor):
    with pytest.raises(PermissionDenied):
        services.start_consultation(
            appointment=make_checked_in_appointment(), acting_user=make_doctor().user
        )


@pytest.mark.parametrize("status", [Status.BOOKED, Status.CANCELLED, Status.NO_SHOW])
def test_start_requires_checked_in(make_checked_in_appointment, status):
    appointment = make_checked_in_appointment(status=status)

    with pytest.raises(ValidationError, match="checked in"):
        services.start_consultation(appointment=appointment, acting_user=doctor_of(appointment))


def test_start_allowed_for_completed_without_record(make_checked_in_appointment):
    appointment = make_checked_in_appointment(status=Status.COMPLETED)

    record = services.start_consultation(
        appointment=appointment, acting_user=doctor_of(appointment)
    )

    assert record.pk


def test_start_rejects_future_appointment(make_checked_in_appointment):
    appointment = make_checked_in_appointment(date=timezone.localdate() + timedelta(days=1))

    with pytest.raises(ValidationError, match="before the appointment date"):
        services.start_consultation(appointment=appointment, acting_user=doctor_of(appointment))


# --- Editing a draft ---------------------------------------------------------


def test_update_record_own_doctor_only(make_record, make_doctor):
    record = make_record()

    services.update_record(record, data={"clinical_notes": "Wheeze"}, acting_user=doctor_of(record))
    record.refresh_from_db()
    assert record.clinical_notes == "Wheeze"

    with pytest.raises(PermissionDenied):
        services.update_record(record, data={"clinical_notes": "x"}, acting_user=make_doctor().user)


def test_second_primary_diagnosis_gives_friendly_error(make_record):
    record = make_record()
    add_primary(record)

    with pytest.raises(ValidationError, match="already has a primary diagnosis"):
        add_primary(record, "Flu")


def test_edits_blocked_after_finalize(make_record, make_medicine):
    record = make_record()
    add_primary(record)
    record = services.finalize_record(record, acting_user=doctor_of(record))
    diagnosis = record.diagnoses.get()

    for action in (
        lambda: services.update_record(
            record, data={"clinical_notes": "x"}, acting_user=doctor_of(record)
        ),
        lambda: add_primary(record, "Other"),
        lambda: services.remove_diagnosis(diagnosis, acting_user=doctor_of(record)),
        lambda: add_item(record, make_medicine()),
    ):
        with pytest.raises(ValidationError, match="finalized"):
            action()


# --- Prescriptions and allergies ---------------------------------------------


def test_allergy_match_blocks_without_override(make_record, make_medicine):
    record = make_record()
    record.patient.allergies = "Severe penicillin allergy (rash)"
    record.patient.save()
    medicine = make_medicine(name="Penicillin V", generic_name="Penicillin")

    with pytest.raises(ValidationError, match="Patient allergies mention: Penicillin"):
        add_item(record, medicine)
    assert not Prescription.objects.exists()

    item = add_item(record, medicine, override=True)
    assert item.allergy_override is True


def test_no_false_allergy_match_on_unrelated_words(make_record, make_patient, make_medicine):
    record = make_record()
    record.patient.allergies = "Penicillinase-resistant reaction? no. Sulfa drugs."
    record.patient.save()

    item = add_item(record, make_medicine(name="Penicillin"))

    assert item.allergy_override is False


def test_override_flag_only_saved_when_there_was_a_match(make_record, make_medicine):
    item = add_item(make_record(), make_medicine(), override=True)

    assert item.allergy_override is False


def test_inactive_medicine_rejected(make_record, make_medicine):
    with pytest.raises(ValidationError, match="no longer available"):
        add_item(make_record(), make_medicine(is_active=False))


def test_duplicate_medicine_friendly_error(make_record, make_medicine):
    record, medicine = make_record(), make_medicine()
    add_item(record, medicine)

    with pytest.raises(ValidationError, match="already on the prescription"):
        add_item(record, medicine)


def test_remove_item(make_record, make_medicine):
    record = make_record()
    item = add_item(record, make_medicine())

    services.remove_prescription_item(item, acting_user=doctor_of(record))

    assert not record.prescription.items.exists()


# --- Finalize ----------------------------------------------------------------


def test_finalize_requires_a_diagnosis(make_record):
    record = make_record()

    with pytest.raises(ValidationError, match="at least one diagnosis"):
        services.finalize_record(record, acting_user=doctor_of(record))


def test_finalize_requires_exactly_one_primary(make_record):
    record = make_record()
    Diagnosis.objects.create(record=record, description="Rhinitis", diagnosis_type="SECONDARY")

    with pytest.raises(ValidationError, match="exactly one diagnosis as primary"):
        services.finalize_record(record, acting_user=doctor_of(record))


def test_finalize_issues_prescription_and_completes_appointment(make_record, make_medicine):
    record = make_record()
    add_primary(record)
    add_item(record, make_medicine())

    record = services.finalize_record(record, acting_user=doctor_of(record))

    assert record.status == RecordStatus.FINALIZED
    assert record.finalized_at is not None
    prescription = Prescription.objects.get(record=record)
    assert prescription.status == PrescriptionStatus.ISSUED
    assert prescription.issued_at == record.finalized_at
    record.appointment.refresh_from_db()
    assert record.appointment.status == Status.COMPLETED
    assert record.appointment.completed_by == doctor_of(record)


def test_finalize_deletes_empty_draft_prescription(make_record, make_medicine):
    record = make_record()
    add_primary(record)
    item = add_item(record, make_medicine())
    services.remove_prescription_item(item, acting_user=doctor_of(record))

    services.finalize_record(record, acting_user=doctor_of(record))

    assert not Prescription.objects.exists()


def test_finalize_leaves_completed_appointment_alone(make_record, make_checked_in_appointment):
    appointment = make_checked_in_appointment(status=Status.COMPLETED)
    record = make_record(appointment=appointment)
    add_primary(record)

    services.finalize_record(record, acting_user=doctor_of(record))

    appointment.refresh_from_db()
    assert appointment.status == Status.COMPLETED


def test_finalize_only_own_doctor(make_record, make_doctor):
    record = make_record()
    add_primary(record)

    with pytest.raises(PermissionDenied):
        services.finalize_record(record, acting_user=make_doctor().user)


def test_complete_appointment_without_finalized_record_fails(make_record):
    record = make_record()

    with pytest.raises(ValidationError, match="Finalize the consultation"):
        appointment_services.complete_appointment(record.appointment, acting_user=doctor_of(record))


# --- Addenda and cancelling ----------------------------------------------------


def test_addendum_only_on_finalized_by_own_doctor(make_record, make_doctor):
    record = make_record()
    with pytest.raises(ValidationError, match="finalized"):
        services.add_addendum(record, text="Note", acting_user=doctor_of(record))

    add_primary(record)
    record = services.finalize_record(record, acting_user=doctor_of(record))
    addendum = services.add_addendum(
        record, text=" Lab result normal ", acting_user=doctor_of(record)
    )
    assert addendum.text == "Lab result normal"

    with pytest.raises(PermissionDenied):
        services.add_addendum(record, text="x", acting_user=make_doctor().user)
    with pytest.raises(ValidationError):
        services.add_addendum(record, text="  ", acting_user=doctor_of(record))


@pytest.fixture
def issued_prescription(make_record, make_medicine):
    record = make_record()
    add_primary(record)
    add_item(record, make_medicine())
    services.finalize_record(record, acting_user=doctor_of(record))
    return Prescription.objects.get(record=record)


def test_cancel_prescription(issued_prescription):
    doctor_user = issued_prescription.doctor.user

    services.cancel_prescription(issued_prescription, reason="Wrong dose", acting_user=doctor_user)

    issued_prescription.refresh_from_db()
    assert issued_prescription.status == PrescriptionStatus.CANCELLED
    assert issued_prescription.cancelled_by == doctor_user


def test_cancel_prescription_rules(issued_prescription, make_doctor):
    doctor_user = issued_prescription.doctor.user
    with pytest.raises(ValidationError):
        services.cancel_prescription(issued_prescription, reason=" ", acting_user=doctor_user)
    with pytest.raises(PermissionDenied):
        services.cancel_prescription(
            issued_prescription, reason="x", acting_user=make_doctor().user
        )

    issued_prescription.status = PrescriptionStatus.DISPENSED
    issued_prescription.save()
    with pytest.raises(ValidationError, match="Only issued"):
        services.cancel_prescription(issued_prescription, reason="x", acting_user=doctor_user)


# --- Vitals ----------------------------------------------------------------------

VITALS = {"bp_systolic": 120, "bp_diastolic": 80, "pulse_bpm": 72}


def test_nurse_records_and_updates_vitals(make_checked_in_appointment, make_user):
    appointment = make_checked_in_appointment()
    nurse = make_user(role=Role.NURSE)

    services.record_vitals(appointment=appointment, data=VITALS, acting_user=nurse)
    vitals = services.record_vitals(
        appointment=appointment, data={**VITALS, "pulse_bpm": 90}, acting_user=nurse
    )

    assert Vitals.objects.count() == 1
    assert vitals.pulse_bpm == 90
    assert vitals.recorded_by == nurse


def test_vitals_roles(make_checked_in_appointment, make_user, make_doctor):
    appointment = make_checked_in_appointment()

    services.record_vitals(appointment=appointment, data=VITALS, acting_user=doctor_of(appointment))
    with pytest.raises(PermissionDenied):
        services.record_vitals(appointment=appointment, data=VITALS, acting_user=make_user())
    with pytest.raises(PermissionDenied):
        services.record_vitals(appointment=appointment, data=VITALS, acting_user=make_doctor().user)


def test_vitals_need_checked_in(make_checked_in_appointment, make_user):
    appointment = make_checked_in_appointment(status=Status.BOOKED)

    with pytest.raises(ValidationError, match="checked-in"):
        services.record_vitals(
            appointment=appointment, data=VITALS, acting_user=make_user(role=Role.NURSE)
        )


def test_vitals_blocked_after_finalize(make_record, make_user):
    record = make_record(status=RecordStatus.FINALIZED)

    with pytest.raises(ValidationError, match="finalized"):
        services.record_vitals(
            appointment=record.appointment, data=VITALS, acting_user=make_user(role=Role.NURSE)
        )


def test_vitals_validation(make_checked_in_appointment, make_user):
    with pytest.raises(ValidationError):
        services.record_vitals(
            appointment=make_checked_in_appointment(),
            data={"temperature_c": Decimal("50.0")},
            acting_user=make_user(role=Role.NURSE),
        )


# --- Reports -----------------------------------------------------------------------


def test_attach_report_creates_document_and_link(make_record, make_upload):
    record = make_record(status=RecordStatus.FINALIZED)  # reports may arrive later

    report = services.attach_report(
        record,
        file=make_upload("xray.pdf"),
        category="IMAGING",
        description="Chest X-ray",
        acting_user=doctor_of(record),
    )

    assert RecordReport.objects.get() == report
    assert report.document.patient == record.patient
    assert default_storage.exists(report.document.file.name)


def test_attach_report_own_doctor_only(make_record, make_upload, make_doctor):
    with pytest.raises(PermissionDenied):
        services.attach_report(
            make_record(),
            file=make_upload(),
            category="OTHER",
            description="",
            acting_user=make_doctor().user,
        )
