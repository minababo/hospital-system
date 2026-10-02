import re
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.urls import reverse

from common.validators import normalize_nic, normalize_phone
from patients.models import Patient, PatientDocument

# "P000123", "p123" or "123". Capped at 18 digits so it always fits a bigint pk.
MRN_PATTERN = re.compile(r"[Pp]?(\d{1,18})")


def _phone_variants(phone):
    """The same Sri Lankan number in both stored formats: 0771234567 and +94771234567."""
    variants = {phone}
    if phone.startswith("+94"):
        variants.add("0" + phone[3:])
    elif phone.startswith("0"):
        variants.add("+94" + phone[1:])
    return variants


def search_patients(query=None):
    """Search by MRN, NIC, phone or name. Every name word must match first or last name,
    so "kamal perera" finds Kamal Perera. Empty query returns everyone, newest first."""
    patients = Patient.objects.all()
    query = (query or "").strip()
    if not query:
        return patients

    conditions = Q(nic__iexact=normalize_nic(query))

    mrn = MRN_PATTERN.fullmatch(query)
    if mrn:
        conditions |= Q(pk=int(mrn.group(1)))

    phone = normalize_phone(query)
    if phone:
        conditions |= Q(phone__in=_phone_variants(phone))

    name_match = Q()
    for term in query.split():
        name_match &= Q(first_name__icontains=term) | Q(last_name__icontains=term)
    conditions |= name_match

    return patients.filter(conditions)


def get_patient_document(patient_pk, document_pk):
    """The document, or 404 if it doesn't exist or belongs to another patient."""
    return get_object_or_404(
        PatientDocument.objects.select_related("patient", "uploaded_by"),
        pk=document_pk,
        patient_id=patient_pk,
    )


# --- Medical history ---------------------------------------------------------


@dataclass
class HistoryEvent:
    timestamp: datetime
    kind: str
    title: str
    detail: str = ""
    url: str | None = None


def _document_events(patient):
    documents = patient.documents.select_related("uploaded_by")
    return [
        HistoryEvent(
            timestamp=document.uploaded_at,
            kind="Document",
            title=f"{document.get_category_display()} uploaded: {document.original_name}",
            detail=document.description,
            url=reverse("patients:document_view", args=[patient.pk, document.pk]),
        )
        for document in documents
    ]


# Each provider takes a patient and returns a list of HistoryEvent. Appointments,
# medical records, lab, pharmacy and admissions add their own providers here.
PROVIDERS = [_document_events]


def patient_history(patient):
    events = [event for provider in PROVIDERS for event in provider(patient)]
    return sorted(events, key=lambda event: event.timestamp, reverse=True)
