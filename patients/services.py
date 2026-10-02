import os

from django.conf import settings
from django.db import transaction

from common.validators import MIME_TYPES, validate_document_upload
from patients.models import Patient, PatientDocument

# Audit logging of these actions will be added in these services (audit app).


@transaction.atomic
def register_patient(*, data, acting_user):
    patient = Patient(**data, created_by=acting_user)
    patient.full_clean()
    patient.save()
    return patient


@transaction.atomic
def update_patient(patient, *, data, acting_user):
    for name, value in data.items():
        setattr(patient, name, value)
    patient.full_clean()
    patient.save()
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
    return document


@transaction.atomic
def delete_document(document, *, acting_user):
    storage, name = document.file.storage, document.file.name
    document.delete()
    # Remove the stored file only once the database delete has committed. If the
    # transaction rolls back, the row still exists and still needs its file.
    transaction.on_commit(lambda: storage.delete(name))
