import re
from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage

from conftest import SAMPLE_FILE_BYTES
from patients import services
from patients.models import Patient, PatientDocument

PATIENT_DATA = {
    "first_name": "Kamal",
    "last_name": "Perera",
    "date_of_birth": date(1985, 6, 15),
    "gender": Patient.Gender.MALE,
    "phone": "077 123 4567",
    "address": "10 Galle Road, Colombo",
}


def upload(patient, file, user):
    return services.upload_document(
        patient=patient, file=file, category="LAB_REPORT", description="", acting_user=user
    )


def test_register_patient_sets_created_by(make_user):
    receptionist = make_user()

    patient = services.register_patient(data=PATIENT_DATA, acting_user=receptionist)

    assert patient.created_by == receptionist
    assert patient.phone == "0771234567"


def test_register_patient_validates(make_user):
    with pytest.raises(ValidationError):
        services.register_patient(data={**PATIENT_DATA, "phone": "123"}, acting_user=make_user())
    assert not Patient.objects.exists()


def test_update_patient(make_patient, make_user):
    patient = make_patient()

    services.update_patient(patient, data={"allergies": "Penicillin"}, acting_user=make_user())

    patient.refresh_from_db()
    assert patient.allergies == "Penicillin"


def test_upload_stores_random_key_without_original_name(make_patient, make_upload, make_user):
    patient = make_patient()

    document = upload(patient, make_upload("Kamal Perera blood test.pdf"), make_user())

    assert re.fullmatch(rf"patients/{patient.pk}/[0-9a-f]{{32}}\.pdf", document.file.name)
    assert document.original_name == "Kamal Perera blood test.pdf"
    assert document.size_bytes == len(SAMPLE_FILE_BYTES["pdf"])
    assert default_storage.exists(document.file.name)


def test_upload_content_type_comes_from_bytes_not_browser(make_patient, make_upload, make_user):
    file = make_upload("scan.JPEG", SAMPLE_FILE_BYTES["jpg"], content_type="application/pdf")

    document = upload(make_patient(), file, make_user())

    assert document.content_type == "image/jpeg"
    assert document.file.name.endswith(".jpg")


def test_invalid_upload_stores_nothing(make_patient, make_upload, make_user):
    with pytest.raises(ValidationError):
        upload(make_patient(), make_upload("fake.pdf", b"not a pdf"), make_user())

    assert not PatientDocument.objects.exists()


def test_upload_respects_max_upload_setting(settings, make_patient, make_upload, make_user):
    settings.MAX_UPLOAD_MB = 1
    big = make_upload("big.pdf", SAMPLE_FILE_BYTES["pdf"] + b"0" * (1024 * 1024))

    with pytest.raises(ValidationError, match="too large"):
        upload(make_patient(), big, make_user())


def test_delete_removes_file_only_after_commit(
    make_patient, make_upload, make_user, django_capture_on_commit_callbacks
):
    document = upload(make_patient(), make_upload(), make_user())
    name = document.file.name

    with django_capture_on_commit_callbacks(execute=False) as callbacks:
        services.delete_document(document, acting_user=make_user())
    assert not PatientDocument.objects.exists()
    assert default_storage.exists(name)  # not committed yet, so the file is still there

    for callback in callbacks:
        callback()
    assert not default_storage.exists(name)


def test_delete_with_commit_callbacks_executed(
    make_patient, make_upload, make_user, django_capture_on_commit_callbacks
):
    document = upload(make_patient(), make_upload(), make_user())
    name = document.file.name

    with django_capture_on_commit_callbacks(execute=True):
        services.delete_document(document, acting_user=make_user())

    assert not default_storage.exists(name)
