import os

from django.conf import settings
from django.db import transaction

from audit.services import (
    Action,
    created_changes,
    log_action,
    saved_snapshot,
    tracked_fields,
    updated_changes,
)
from common.validators import MIME_TYPES, validate_document_upload
from patients.models import Patient, PatientDocument

# Recorded patient fields (created_by is already the entry's actor).
PATIENT_FIELDS = [name for name in tracked_fields(Patient) if name != "created_by"]


@transaction.atomic
def register_patient(*, data, acting_user):
    patient = Patient(**data, created_by=acting_user)
    patient.full_clean()
    patient.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="patients.patient.registered",
        obj=patient,
        patient=patient,
        changes=created_changes(patient, PATIENT_FIELDS),
        message=f"Registered with MRN {patient.mrn}",
    )
    return patient


@transaction.atomic
def update_patient(patient, *, data, acting_user):
    before = saved_snapshot(patient, PATIENT_FIELDS)
    for name, value in data.items():
        setattr(patient, name, value)
    patient.full_clean()
    patient.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="patients.patient.updated",
        obj=patient,
        patient=patient,
        changes=updated_changes(patient, before, PATIENT_FIELDS),
    )
    return patient


@transaction.atomic
def upload_document(*, patient, file, category, description, acting_user):
    file_type = validate_document_upload(file, settings.MAX_UPLOAD_MB)
    document = PatientDocument(
        patient=patient,
        file=file,
        # basename() drops any folder part some browsers include in the name.
        original_name=os.path.basename(file.name)[:255],
        category=category,
        description=description,
        # From the detected bytes, never the browser-sent content type.
        content_type=MIME_TYPES[file_type],
        size_bytes=file.size,
        uploaded_by=acting_user,
    )
    document.full_clean()
    document.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="patients.document.uploaded",
        obj=document,
        patient=patient,
        message=f"{document.get_category_display()}: {document.original_name}",
    )
    return document


@transaction.atomic
def delete_document(document, *, acting_user):
    storage, name = document.file.storage, document.file.name
    pk, patient, original_name = document.pk, document.patient, document.original_name
    document.delete()
    document.pk = pk  # delete() cleared it; the audit entry still names the row
    log_action(
        actor=acting_user,
        action=Action.DELETE,
        event="patients.document.deleted",
        obj=document,
        patient=patient,
        message=f"Deleted {original_name}",
    )
    # Remove the stored file only once the database delete has committed. If the
    # transaction rolls back, the row still exists and still needs its file.
    transaction.on_commit(lambda: storage.delete(name))
