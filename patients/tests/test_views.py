import pytest
from django.urls import reverse

from accounts.models import Role
from conftest import SAMPLE_FILE_BYTES
from patients import services
from patients.models import Patient, PatientDocument

EDIT = {Role.ADMIN, Role.RECEPTIONIST}
VIEW = {Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR, Role.NURSE}
HISTORY = {Role.ADMIN, Role.DOCTOR, Role.NURSE}
DOC_DELETE = {Role.ADMIN}

# (url name, needs document?, method, allowed roles, status for an allowed role)
URLS = [
    ("patients:patient_list", False, "get", VIEW, 200),
    ("patients:patient_create", False, "get", EDIT, 200),
    ("patients:patient_detail", False, "get", VIEW, 200),
    ("patients:patient_update", False, "get", EDIT, 200),
    ("patients:patient_history", False, "get", HISTORY, 200),
    ("patients:document_upload", False, "post", VIEW, 302),
    ("patients:document_view", True, "get", VIEW, 200),
    ("patients:document_delete", True, "post", DOC_DELETE, 302),
]
POST_ONLY = [url for url in URLS if url[2] == "post"]


@pytest.fixture
def patient(make_patient):
    return make_patient(first_name="Kamal", last_name="Perera")


@pytest.fixture
def document(patient, make_upload, make_user):
    return services.upload_document(
        patient=patient,
        file=make_upload("blood test.pdf"),
        category="LAB_REPORT",
        description="",
        acting_user=make_user(),
    )


def url_for(name, needs_document, patient, document):
    if name in ("patients:patient_list", "patients:patient_create"):
        return reverse(name)
    if needs_document:
        return reverse(name, args=[patient.pk, document.pk])
    return reverse(name, args=[patient.pk])


# --- RBAC -------------------------------------------------------------------


@pytest.mark.parametrize(("name", "needs_doc", "method", "_roles", "_status"), URLS)
def test_anonymous_is_redirected(
    client, patient, document, name, needs_doc, method, _roles, _status
):
    response = getattr(client, method)(url_for(name, needs_doc, patient, document))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "needs_doc", "method", "roles", "status"), URLS)
def test_rbac_matrix(
    client_for_role, patient, document, role, name, needs_doc, method, roles, status
):
    client = client_for_role(role)

    response = getattr(client, method)(url_for(name, needs_doc, patient, document))

    assert response.status_code == (status if role in roles else 403)


@pytest.mark.parametrize(("name", "needs_doc", "_method", "_roles", "_status"), POST_ONLY)
def test_post_only_urls_reject_get(
    client_for_role, patient, document, name, needs_doc, _method, _roles, _status
):
    response = client_for_role(Role.ADMIN).get(url_for(name, needs_doc, patient, document))

    assert response.status_code == 405


# --- Register / edit / list -------------------------------------------------


def test_register_patient_shows_mrn(client_for_role):
    client = client_for_role(Role.RECEPTIONIST)

    response = client.post(
        reverse("patients:patient_create"),
        {
            "first_name": "Nimali",
            "last_name": "Silva",
            "date_of_birth": "1992-03-04",
            "gender": "FEMALE",
            "nic": "9206 4123 4567",
            "phone": "071 234 5678",
            "address": "22 Kandy Road",
            "blood_group": "O+",
        },
        follow=True,
    )

    patient = Patient.objects.get()
    assert response.redirect_chain[-1][0] == reverse("patients:patient_detail", args=[patient.pk])
    assert f"registered with MRN {patient.mrn}" in response.content.decode()
    assert patient.nic == "920641234567"
    assert patient.created_by.role == Role.RECEPTIONIST


def test_register_rejects_future_birth_date(client_for_role):
    response = client_for_role(Role.RECEPTIONIST).post(
        reverse("patients:patient_create"),
        {
            "first_name": "A",
            "last_name": "B",
            "date_of_birth": "2999-01-01",
            "gender": "MALE",
            "phone": "0771234567",
            "address": "x",
            "blood_group": "UNKNOWN",
        },
    )

    assert response.status_code == 200
    assert "date_of_birth" in response.context["form"].errors
    assert not Patient.objects.exists()


def test_edit_patient(client_for_role, patient):
    response = client_for_role(Role.ADMIN).post(
        reverse("patients:patient_update", args=[patient.pk]),
        {
            "first_name": patient.first_name,
            "last_name": patient.last_name,
            "date_of_birth": "1990-01-01",
            "gender": patient.gender,
            "phone": patient.phone,
            "address": patient.address,
            "blood_group": "AB-",
            "allergies": "Peanuts",
        },
    )

    assert response.status_code == 302
    patient.refresh_from_db()
    assert patient.blood_group == "AB-"
    assert patient.allergies == "Peanuts"


def test_list_search(client_for_role, patient, make_patient):
    make_patient(first_name="Someone", last_name="Else")

    response = client_for_role(Role.NURSE).get(reverse("patients:patient_list"), {"q": "kamal"})

    assert list(response.context["patients"]) == [patient]
    assert b"Register patient" not in response.content  # nurses can't register


def test_detail_shows_no_known_allergies(client_for_role, patient):
    response = client_for_role(Role.DOCTOR).get(
        reverse("patients:patient_detail", args=[patient.pk])
    )

    assert b"No known allergies" in response.content


# --- Documents --------------------------------------------------------------


def test_view_document_inline(client_for_role, patient, document):
    response = client_for_role(Role.NURSE).get(
        reverse("patients:document_view", args=[patient.pk, document.pk])
    )

    assert response.status_code == 200
    assert b"".join(response.streaming_content) == SAMPLE_FILE_BYTES["pdf"]
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].startswith("inline")
    assert 'filename="blood test.pdf"' in response["Content-Disposition"]
    assert response["Cache-Control"] == "private, no-store"
    assert response["X-Content-Type-Options"] == "nosniff"


def test_download_document_as_attachment(client_for_role, patient, document):
    response = client_for_role(Role.NURSE).get(
        reverse("patients:document_view", args=[patient.pk, document.pk]), {"download": "1"}
    )

    assert response["Content-Disposition"].startswith("attachment")


def test_document_of_another_patient_is_404(client_for_role, document, make_patient):
    other = make_patient()

    response = client_for_role(Role.ADMIN).get(
        reverse("patients:document_view", args=[other.pk, document.pk])
    )

    assert response.status_code == 404


def test_upload_document(client_for_role, patient, make_upload):
    response = client_for_role(Role.DOCTOR).post(
        reverse("patients:document_upload", args=[patient.pk]),
        {"file": make_upload("xray.png", SAMPLE_FILE_BYTES["png"]), "category": "IMAGING"},
        follow=True,
    )

    document = PatientDocument.objects.get()
    assert document.content_type == "image/png"
    assert "xray.png was uploaded" in response.content.decode()


def test_invalid_upload_shows_error_and_stores_nothing(client_for_role, patient, make_upload):
    response = client_for_role(Role.RECEPTIONIST).post(
        reverse("patients:document_upload", args=[patient.pk]),
        {"file": make_upload("fake.pdf", b"plain text pretending"), "category": "OTHER"},
        follow=True,
    )

    assert "Upload failed" in response.content.decode()
    assert not PatientDocument.objects.exists()


def test_upload_without_file_shows_error(client_for_role, patient):
    response = client_for_role(Role.NURSE).post(
        reverse("patients:document_upload", args=[patient.pk]), {"category": "OTHER"}, follow=True
    )

    assert "Upload failed" in response.content.decode()


def test_admin_deletes_document(
    client_for_role, patient, document, django_capture_on_commit_callbacks
):
    with django_capture_on_commit_callbacks(execute=True):
        response = client_for_role(Role.ADMIN).post(
            reverse("patients:document_delete", args=[patient.pk, document.pk])
        )

    assert response.status_code == 302
    assert not PatientDocument.objects.exists()


def test_history_page_lists_document(client_for_role, patient, document):
    response = client_for_role(Role.DOCTOR).get(
        reverse("patients:patient_history", args=[patient.pk])
    )

    assert "Lab report uploaded: blood test.pdf" in response.content.decode()
