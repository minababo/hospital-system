from datetime import time

import pytest
from django.urls import reverse

from accounts.models import Role
from appointments.models import Status
from records.models import Diagnosis, Prescription, RecordStatus, Vitals

VIEW_RECORDS = {Role.ADMIN, Role.DOCTOR, Role.NURSE}
DOCTOR_ONLY = {Role.DOCTOR}
RECORD_VITALS = {Role.NURSE, Role.DOCTOR}


@pytest.fixture
def setup_for(
    make_user, make_scheduled_doctor, make_checked_in_appointment, make_record, make_medicine
):
    """Builds data for one role: when the role is DOCTOR, that user is the doctor of
    every object, so "own doctor" rules pass. Returns (user, objects)."""

    def _setup(role, record_status=RecordStatus.DRAFT):
        user = make_user(role=role)
        doctor = (
            make_scheduled_doctor(user=user) if role == Role.DOCTOR else make_scheduled_doctor()
        )
        appointment = make_checked_in_appointment(doctor=doctor)
        record = make_record(appointment=appointment, status=record_status)
        diagnosis = Diagnosis.objects.create(record=record, description="Asthma")
        prescription = Prescription.objects.create(
            record=record, patient=record.patient, doctor=doctor
        )
        item = prescription.items.create(
            medicine=make_medicine(), dose="1", frequency="OD", duration_days=1, quantity=1
        )
        empty_appointment = make_checked_in_appointment(doctor=doctor, start_time=time(10))
        return user, {
            "record": record,
            "appointment": appointment,
            "empty_appointment": empty_appointment,
            "patient": record.patient,
            "diagnosis": diagnosis,
            "item": item,
        }

    return _setup


def build(name, objects):
    args = {
        "records:start": [objects["empty_appointment"].pk],
        "records:vitals": [objects["appointment"].pk],
        "records:treatment_history": [objects["patient"].pk],
        "records:diagnosis_remove": [objects["record"].pk, objects["diagnosis"].pk],
        "records:item_remove": [objects["record"].pk, objects["item"].pk],
    }.get(name, [objects["record"].pk])
    return reverse(name, args=args)


# (url name, method, allowed roles, expected status for allowed role, record status)
URLS = [
    ("records:record_detail", "get", VIEW_RECORDS, 200, RecordStatus.FINALIZED),
    ("records:treatment_history", "get", VIEW_RECORDS, 200, RecordStatus.FINALIZED),
    ("records:vitals", "get", RECORD_VITALS, 200, RecordStatus.DRAFT),
    ("records:print_record", "get", VIEW_RECORDS, 200, RecordStatus.FINALIZED),
    ("records:print_prescription", "get", VIEW_RECORDS, 200, RecordStatus.FINALIZED),
    ("records:start", "post", DOCTOR_ONLY, 302, RecordStatus.DRAFT),
    ("records:record_update", "post", DOCTOR_ONLY, 200, RecordStatus.DRAFT),
    ("records:diagnosis_add", "post", DOCTOR_ONLY, 200, RecordStatus.DRAFT),
    ("records:diagnosis_remove", "post", DOCTOR_ONLY, 302, RecordStatus.DRAFT),
    ("records:item_add", "post", DOCTOR_ONLY, 200, RecordStatus.DRAFT),
    ("records:item_remove", "post", DOCTOR_ONLY, 302, RecordStatus.DRAFT),
    ("records:report_upload", "post", DOCTOR_ONLY, 302, RecordStatus.DRAFT),
    ("records:finalize", "post", DOCTOR_ONLY, 302, RecordStatus.DRAFT),
    ("records:addendum_add", "post", DOCTOR_ONLY, 302, RecordStatus.FINALIZED),
    ("records:prescription_cancel", "post", DOCTOR_ONLY, 302, RecordStatus.FINALIZED),
]
POST_ONLY = [url for url in URLS if url[1] == "post"]


@pytest.mark.parametrize(("name", "method", "_roles", "_status", "record_status"), URLS)
def test_anonymous_redirected(client, setup_for, name, method, _roles, _status, record_status):
    _, objects = setup_for(Role.ADMIN, record_status)

    response = getattr(client, method)(build(name, objects))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "method", "roles", "status", "record_status"), URLS)
def test_rbac_matrix(client, setup_for, role, name, method, roles, status, record_status):
    user, objects = setup_for(role, record_status)
    client.force_login(user)

    response = getattr(client, method)(build(name, objects))

    assert response.status_code == (status if role in roles else 403)


@pytest.mark.parametrize(("name", "_method", "_roles", "_status", "record_status"), POST_ONLY)
def test_post_only_urls_reject_get(
    client, setup_for, name, _method, _roles, _status, record_status
):
    user, objects = setup_for(Role.DOCTOR, record_status)
    client.force_login(user)

    assert client.get(build(name, objects)).status_code == 405


# --- Object scoping -------------------------------------------------------------


def test_other_doctors_draft_is_404(client, make_record, make_doctor):
    draft = make_record()
    client.force_login(make_doctor().user)

    assert client.get(reverse("records:record_detail", args=[draft.pk])).status_code == 404
    assert client.post(reverse("records:finalize", args=[draft.pk])).status_code == 404


def test_finalized_record_read_only_for_other_doctor(client, make_record, make_doctor):
    record = make_record(status=RecordStatus.FINALIZED)
    client.force_login(make_doctor().user)

    page = client.get(reverse("records:record_detail", args=[record.pk]))
    addendum = client.post(reverse("records:addendum_add", args=[record.pk]), {"text": "x"})

    assert page.status_code == 200
    assert page.context["editable"] is False
    assert addendum.status_code == 403


def test_own_draft_is_editable_workspace(client, make_record):
    record = make_record()
    client.force_login(record.doctor.user)

    response = client.get(reverse("records:record_detail", args=[record.pk]))

    assert response.context["editable"] is True
    assert reverse("records:finalize", args=[record.pk]) in response.content.decode()


def test_print_pages_require_finalized(client, make_record):
    record = make_record()
    client.force_login(record.doctor.user)

    assert client.get(reverse("records:print_record", args=[record.pk])).status_code == 404
    assert client.get(reverse("records:print_prescription", args=[record.pk])).status_code == 404


def test_print_prescription_404_without_prescription(client_for_role, make_record):
    record = make_record(status=RecordStatus.FINALIZED)

    response = client_for_role(Role.NURSE).get(
        reverse("records:print_prescription", args=[record.pk])
    )

    assert response.status_code == 404


# --- Workflow through the pages ------------------------------------------------


def test_full_consultation_flow(client, make_checked_in_appointment, make_medicine):
    appointment = make_checked_in_appointment()
    medicine = make_medicine(name="Salbutamol", strength="100 mcg", form="INHALER")
    client.force_login(appointment.doctor.user)

    response = client.post(reverse("records:start", args=[appointment.pk]))
    record = appointment.medical_record
    assert response.url == reverse("records:record_detail", args=[record.pk])

    client.post(
        reverse("records:diagnosis_add", args=[record.pk]),
        {"description": "Asthma", "icd10_code": "j45", "diagnosis_type": "PRIMARY"},
    )
    client.post(
        reverse("records:item_add", args=[record.pk]),
        {
            "medicine": medicine.pk,
            "dose": "2 puffs",
            "frequency": "PRN",
            "route": "INHALED",
            "duration_days": 30,
            "quantity": 1,
        },
    )
    client.post(reverse("records:finalize", args=[record.pk]))

    record.refresh_from_db()
    appointment.refresh_from_db()
    assert record.status == RecordStatus.FINALIZED
    assert record.diagnoses.get().icd10_code == "J45"
    assert record.prescription.status == "ISSUED"
    assert appointment.status == Status.COMPLETED

    summary = client.get(reverse("records:print_record", args=[record.pk]))
    prescription = client.get(reverse("records:print_prescription", args=[record.pk]))
    assert "Asthma" in summary.content.decode()
    assert "Salbutamol" in prescription.content.decode()


def test_allergy_warning_shows_override_checkbox(client, make_record, make_medicine):
    record = make_record()
    record.patient.allergies = "Aspirin"
    record.patient.save()
    medicine = make_medicine(name="Aspirin", strength="75 mg")
    client.force_login(record.doctor.user)
    data = {
        "medicine": medicine.pk,
        "dose": "1",
        "frequency": "OD",
        "route": "ORAL",
        "duration_days": 7,
        "quantity": 7,
    }

    blocked = client.post(reverse("records:item_add", args=[record.pk]), data)
    allowed = client.post(
        reverse("records:item_add", args=[record.pk]), {**data, "allergy_override": "on"}
    )

    assert blocked.status_code == 200
    assert blocked.context["show_allergy_override"] is True
    assert "Patient allergies mention: Aspirin" in blocked.content.decode()
    assert allowed.status_code == 302
    assert record.prescription.items.get().allergy_override is True


def test_finalize_without_diagnosis_shows_message(client, make_record):
    record = make_record()
    client.force_login(record.doctor.user)

    response = client.post(reverse("records:finalize", args=[record.pk]), follow=True)

    assert "Add at least one diagnosis" in response.content.decode()


def test_nurse_records_vitals(client_for_role, make_checked_in_appointment):
    appointment = make_checked_in_appointment()

    response = client_for_role(Role.NURSE).post(
        reverse("records:vitals", args=[appointment.pk]),
        {"bp_systolic": 120, "bp_diastolic": 80, "temperature_c": "37.2"},
    )

    assert response.status_code == 302
    assert Vitals.objects.get().bp_systolic == 120


def test_vitals_form_shows_errors(client_for_role, make_checked_in_appointment):
    appointment = make_checked_in_appointment()

    response = client_for_role(Role.NURSE).post(
        reverse("records:vitals", args=[appointment.pk]), {"bp_systolic": 120}
    )

    assert response.status_code == 200
    assert "both systolic and diastolic" in response.content.decode()
    assert not Vitals.objects.exists()


# --- Appointment detail integration ------------------------------------------------


def detail(client, appointment):
    return client.get(reverse("appointments:appointment_detail", args=[appointment.pk]))


def test_appointment_buttons_for_own_doctor(client, make_checked_in_appointment, make_record):
    appointment = make_checked_in_appointment()
    client.force_login(appointment.doctor.user)

    start = detail(client, appointment).content.decode()
    assert "Start consultation" in start and "Record vitals" in start

    record = make_record(appointment=appointment)
    assert "Open consultation" in detail(client, appointment).content.decode()

    record.status = RecordStatus.FINALIZED
    record.save()
    finished = detail(client, appointment).content.decode()
    assert "View record" in finished
    assert "Record vitals" not in finished


def test_appointment_buttons_for_nurse_and_receptionist(
    client_for_role, make_checked_in_appointment
):
    appointment = make_checked_in_appointment()

    nurse_page = detail(client_for_role(Role.NURSE), appointment).content.decode()
    assert "Record vitals" in nurse_page
    assert "Start consultation" not in nurse_page

    receptionist_page = detail(client_for_role(Role.RECEPTIONIST), appointment).content.decode()
    assert "Record vitals" not in receptionist_page


def test_no_standalone_complete_button(client, make_checked_in_appointment):
    appointment = make_checked_in_appointment()
    client.force_login(appointment.doctor.user)

    content = detail(client, appointment).content.decode()

    assert reverse("appointments:complete", args=[appointment.pk]) not in content


# Only these roles can open the patient page at all.
@pytest.mark.parametrize("role", [Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR, Role.NURSE])
def test_patient_detail_treatment_history_link(client_for_role, make_patient, role):
    patient = make_patient()

    content = (
        client_for_role(role)
        .get(reverse("patients:patient_detail", args=[patient.pk]))
        .content.decode()
    )

    url = reverse("records:treatment_history", args=[patient.pk])
    assert (url in content) == (role in VIEW_RECORDS)
