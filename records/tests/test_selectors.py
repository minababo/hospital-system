from datetime import time, timedelta

import pytest
from django.utils import timezone

from accounts.models import Role
from patients.selectors import patient_history
from records import selectors
from records.models import Diagnosis, Prescription, RecordStatus

pytestmark = pytest.mark.django_db


def test_drafts_only_visible_to_their_doctor(make_record, make_doctor, make_user):
    draft = make_record()
    finalized = make_record(status=RecordStatus.FINALIZED)

    assert set(selectors.visible_records(draft.doctor.user)) == {draft, finalized}
    assert list(selectors.visible_records(make_doctor().user)) == [finalized]
    for role in (Role.NURSE, Role.ADMIN):
        assert list(selectors.visible_records(make_user(role=role))) == [finalized]


def test_treatment_history_ordering_and_contents(
    make_record, make_patient, make_checked_in_appointment, make_user
):
    patient = make_patient()
    now = timezone.now()
    older = make_record(
        appointment=make_checked_in_appointment(patient=patient),
        status=RecordStatus.FINALIZED,
        finalized_at=now - timedelta(days=30),
    )
    newer = make_record(
        appointment=make_checked_in_appointment(patient=patient, start_time=time(10)),
        status=RecordStatus.FINALIZED,
        finalized_at=now,
    )
    make_record(
        appointment=make_checked_in_appointment(patient=patient, start_time=time(11))
    )  # draft
    Diagnosis.objects.create(record=newer, description="Asthma", diagnosis_type="PRIMARY")

    history = list(selectors.treatment_history(patient, make_user(role=Role.NURSE)))

    assert history == [newer, older]
    assert [d.description for d in history[0].diagnoses.all()] == ["Asthma"]


def test_allergy_matches(make_patient, make_medicine):
    patient = make_patient(allergies="Allergic to Amoxicillin and sulfa drugs")

    assert selectors.allergy_matches(
        patient, make_medicine(name="Amoxil", generic_name="amoxicillin")
    ) == ["amoxicillin"]
    assert selectors.allergy_matches(patient, make_medicine(name="Paracetamol")) == []
    assert selectors.allergy_matches(make_patient(allergies=""), make_medicine(name="Brufen")) == []


def test_history_provider_events(make_record):
    record = make_record(status=RecordStatus.FINALIZED)
    Diagnosis.objects.create(record=record, description="Migraine", diagnosis_type="PRIMARY")
    Prescription.objects.create(
        record=record,
        patient=record.patient,
        doctor=record.doctor,
        status="ISSUED",
        issued_at=timezone.now(),
    )

    titles = [event.title for event in patient_history(record.patient)]

    assert "Consultation — Migraine" in titles
    assert "Prescription issued (0 items)" in titles
