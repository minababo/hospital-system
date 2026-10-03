import re

from django.db.models import Q
from django.urls import reverse

from accounts.models import Role
from patients.selectors import HistoryEvent
from records.models import MedicalRecord, PrescriptionStatus, RecordStatus


def visible_records(user):
    """Records this user may open. Finalized records are visible to all clinical roles;
    a DRAFT is visible only to its own doctor (it's work in progress)."""
    records = MedicalRecord.objects.select_related(
        "patient", "doctor__user", "doctor__department", "appointment"
    )
    finalized = Q(status=RecordStatus.FINALIZED)
    if user.role == Role.DOCTOR:
        return records.filter(finalized | Q(doctor__user=user))
    return records.filter(finalized)


def record_for_appointment(appointment):
    return MedicalRecord.objects.filter(appointment=appointment).first()


def treatment_history(patient, user):
    """The patient's finalized consultations, newest first, with everything shown on
    the history page loaded up front (a few queries in total, not a few per record)."""
    return (
        visible_records(user)
        .filter(patient=patient, status=RecordStatus.FINALIZED)
        .select_related("prescription", "appointment__vitals")
        .prefetch_related(
            "diagnoses",
            "prescription__items__medicine",
            "addenda__author",
            "reports__document",
        )
        .order_by("-finalized_at", "-pk")
    )


def allergy_matches(patient, medicine):
    """Names of the medicine (brand or generic) that appear as whole words in the
    patient's allergy notes, ignoring case. "Penicillin" matches "penicillin allergy"
    but not "penicillinase"."""
    allergies = patient.allergies or ""
    if not allergies.strip():
        return []
    matches = []
    for name in (medicine.name, medicine.generic_name):
        name = (name or "").strip()
        if name and name not in matches:
            if re.search(rf"\b{re.escape(name)}\b", allergies, flags=re.IGNORECASE):
                matches.append(name)
    return matches


def get_prescription(record):
    """The record's prescription or None (the reverse one-to-one raises if missing)."""
    return getattr(record, "prescription", None)


# --- Patient history provider ------------------------------------------------


def record_history_events(patient):
    """Registered in patients.selectors.PROVIDERS by RecordsConfig.ready()."""
    events = []
    records = (
        MedicalRecord.objects.filter(patient=patient, status=RecordStatus.FINALIZED)
        .select_related("doctor__user", "prescription")
        .prefetch_related("diagnoses", "prescription__items")
    )
    for record in records:
        url = reverse("records:record_detail", args=[record.pk])
        primary = record.primary_diagnosis
        events.append(
            HistoryEvent(
                timestamp=record.finalized_at,
                kind="Consultation",
                title=f"Consultation — {primary.description if primary else 'no diagnosis'}",
                detail=str(record.doctor),
                url=url,
            )
        )
        prescription = get_prescription(record)
        if prescription and prescription.issued_at:
            count = len(prescription.items.all())
            events.append(
                HistoryEvent(
                    timestamp=prescription.issued_at,
                    kind="Prescription",
                    title=f"Prescription issued ({count} item{'s' if count != 1 else ''})",
                    detail=str(record.doctor),
                    url=url,
                )
            )
        if prescription and prescription.status == PrescriptionStatus.CANCELLED:
            events.append(
                HistoryEvent(
                    timestamp=prescription.cancelled_at,
                    kind="Prescription",
                    title="Prescription cancelled",
                    detail=prescription.cancel_reason,
                    url=url,
                )
            )
    return events
