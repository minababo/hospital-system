from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from admissions import services
from admissions.models import Admission, ProgressNote
from billing.models import Charge

VIEW = {Role.ADMIN, Role.DOCTOR, Role.NURSE, Role.RECEPTIONIST}
CLINICAL = {Role.ADMIN, Role.DOCTOR, Role.NURSE}
ADMIT = {Role.DOCTOR, Role.NURSE, Role.RECEPTIONIST}
CARE = {Role.DOCTOR, Role.NURSE}
DISCHARGE = {Role.DOCTOR}
WARDS = {Role.ADMIN}


@pytest.fixture
def objects(make_admission, make_bed, make_ward, make_doctor, make_patient):
    current = make_admission(admitted_at=timezone.now() - timedelta(days=1))
    finished = make_admission(admitted_at=timezone.now() - timedelta(days=2))
    services.discharge_patient(
        finished, discharge_type="HOME", discharge_summary="Well", acting_user=make_doctor().user
    )
    empty_ward = make_ward()
    return {
        "admission": current,
        "discharged": finished,
        "ward": current.current_bed.ward,
        "empty_ward": empty_ward,
        "bed": make_bed(ward=empty_ward),
        "patient": make_patient(),
    }


def build(name, o):
    return {
        "admissions:admission_detail": [o["admission"].pk],
        "admissions:transfer": [o["admission"].pk],
        "admissions:note_add": [o["admission"].pk],
        "admissions:discharge": [o["admission"].pk],
        "admissions:discharge_summary": [o["discharged"].pk],
        "admissions:ward_update": [o["ward"].pk],
        "admissions:ward_toggle_active": [o["empty_ward"].pk],
        "admissions:bed_list": [o["ward"].pk],
        "admissions:bed_add": [o["ward"].pk],
        "admissions:bed_update": [o["bed"].pk],
        "admissions:bed_toggle_active": [o["bed"].pk],
    }.get(name, [])


# (url name, method, allowed roles, status for allowed role)
URLS = [
    ("admissions:admission_list", "get", VIEW, 200),
    ("admissions:bed_board", "get", VIEW, 200),
    ("admissions:admit", "get", ADMIT, 200),
    ("admissions:admission_detail", "get", VIEW, 200),
    ("admissions:transfer", "get", CARE, 200),
    ("admissions:discharge", "get", DISCHARGE, 200),
    ("admissions:discharge_summary", "get", CLINICAL, 200),
    ("admissions:ward_list", "get", WARDS, 200),
    ("admissions:ward_create", "get", WARDS, 200),
    ("admissions:ward_update", "get", WARDS, 200),
    ("admissions:bed_list", "get", WARDS, 200),
    ("admissions:bed_update", "get", WARDS, 200),
    ("admissions:note_add", "post", CARE, 302),
    ("admissions:ward_toggle_active", "post", WARDS, 302),
    ("admissions:bed_add", "post", WARDS, 200),  # empty form is re-shown with errors
    ("admissions:bed_toggle_active", "post", WARDS, 302),
]
POST_ONLY = [url for url in URLS if url[1] == "post"]


@pytest.mark.parametrize(("name", "method", "_roles", "_status"), URLS)
def test_anonymous_redirected(client, objects, name, method, _roles, _status):
    response = getattr(client, method)(reverse(name, args=build(name, objects)))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "method", "roles", "status"), URLS)
def test_rbac_matrix(client_for_role, objects, role, name, method, roles, status):
    client = client_for_role(role)
    url = reverse(name, args=build(name, objects))

    response = (
        client.post(url, {"note_type": "NURSING", "text": "Stable"})
        if method == "post"
        else client.get(url)
    )

    assert response.status_code == (status if role in roles else 403)


@pytest.mark.parametrize(("name", "_method", "_roles", "_status"), POST_ONLY)
def test_post_only_reject_get(client_for_role, objects, name, _method, _roles, _status):
    role = Role.ADMIN if name != "admissions:note_add" else Role.NURSE
    response = client_for_role(role).get(reverse(name, args=build(name, objects)))

    assert response.status_code == 405


# --- Pages ----------------------------------------------------------------------------


def test_receptionist_sees_detail_without_notes(client_for_role, objects, make_user):
    services.add_progress_note(
        objects["admission"],
        note_type="NURSING",
        text="Secret clinical note",
        acting_user=make_user(role=Role.NURSE),
    )
    url = reverse("admissions:admission_detail", args=[objects["admission"].pk])

    reception = client_for_role(Role.RECEPTIONIST).get(url)
    nurse = client_for_role(Role.NURSE).get(url)

    assert reception.context["show_notes"] is False
    assert "Secret clinical note" not in reception.content.decode()
    assert "Secret clinical note" in nurse.content.decode()


def test_bed_board_and_discharge_summary_render(client_for_role, objects):
    client = client_for_role(Role.NURSE)

    board = client.get(reverse("admissions:bed_board"))
    summary = client.get(reverse("admissions:discharge_summary", args=[objects["discharged"].pk]))

    assert objects["admission"].patient.full_name in board.content.decode()
    assert board.context["summary"]["occupied"] == 1
    assert objects["discharged"].number in summary.content.decode()
    assert "Discharged home" in summary.content.decode()


def test_discharge_summary_404_while_admitted(client_for_role, objects):
    response = client_for_role(Role.DOCTOR).get(
        reverse("admissions:discharge_summary", args=[objects["admission"].pk])
    )

    assert response.status_code == 404


def test_admit_page_for_admitted_patient_links_to_current(client_for_role, objects):
    patient = objects["admission"].patient

    page = client_for_role(Role.RECEPTIONIST).get(
        reverse("admissions:admit"), {"patient": patient.pk}
    )

    assert page.context["current"] == objects["admission"]
    assert 'name="bed"' not in page.content.decode()


def test_full_flow_through_views(
    client, client_for_role, make_patient, make_bed, make_ward, make_doctor
):
    patient = make_patient()
    bed_a = make_bed(ward=make_ward(daily_rate="2000"))
    bed_b = make_bed(ward=make_ward(daily_rate="6000"))
    doctor = make_doctor()
    nurse = client_for_role(Role.NURSE)
    admitted_at = timezone.localtime() - timedelta(days=2)

    nurse.post(
        reverse("admissions:admit"),
        {
            "patient": patient.pk,
            "bed": bed_a.pk,
            "admitting_doctor": doctor.pk,
            "source": "EMERGENCY",
            "reason": "Dengue fever",
            "admitted_at": admitted_at.strftime("%Y-%m-%dT%H:%M"),
        },
    )
    admission = Admission.objects.get(patient=patient)
    nurse.post(
        reverse("admissions:transfer", args=[admission.pk]),
        {
            "new_bed": bed_b.pk,
            "transferred_at": (admitted_at + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
        },
    )
    nurse.post(
        reverse("admissions:note_add", args=[admission.pk]),
        {"note_type": "NURSING", "text": "Stable"},
    )
    client.force_login(doctor.user)
    client.post(
        reverse("admissions:discharge", args=[admission.pk]),
        {
            "discharge_type": "HOME",
            "discharge_summary": "Platelets recovered.",
            "discharged_at": timezone.localtime().strftime("%Y-%m-%dT%H:%M"),
        },
    )

    admission.refresh_from_db()
    assert admission.status == "DISCHARGED"
    assert ProgressNote.objects.filter(admission=admission).count() == 1
    charges = Charge.objects.filter(source_type="bed_assignment").order_by("pk")
    assert [(c.quantity, c.unit_price) for c in charges] == [(1, 2000), (1, 6000)]


def test_admit_form_errors_shown(client_for_role, make_patient, make_bed, make_doctor):
    patient = make_patient()

    response = client_for_role(Role.NURSE).post(
        reverse("admissions:admit"),
        {
            "patient": patient.pk,
            "bed": make_bed().pk,
            "admitting_doctor": make_doctor().pk,
            "source": "DIRECT",
            "reason": "x",
            "admitted_at": (timezone.localtime() + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
        },
    )

    assert response.status_code == 200
    assert "admitted_at" in response.context["form"].errors
    assert not Admission.objects.exists()


# --- Integration through template tags -----------------------------------------------


@pytest.mark.parametrize("role", list(VIEW))
def test_appointment_page_admit_button(
    client, make_user, make_checked_in_appointment, make_doctor, role
):
    user = make_user(role=role)
    doctor = make_doctor(user=user) if role == Role.DOCTOR else make_doctor()
    appointment = make_checked_in_appointment(doctor=doctor)
    client.force_login(user)

    content = client.get(
        reverse("appointments:appointment_detail", args=[appointment.pk])
    ).content.decode()

    assert ("Admit patient" in content) == (role in ADMIT)


def test_appointment_page_no_button_when_booked(client_for_role, make_appointment):
    appointment = make_appointment(date=timezone.localdate() + timedelta(days=1))

    content = (
        client_for_role(Role.NURSE)
        .get(reverse("appointments:appointment_detail", args=[appointment.pk]))
        .content.decode()
    )

    assert "Admit patient" not in content


def test_appointment_page_shows_current_admission(
    client_for_role, make_checked_in_appointment, make_admission
):
    appointment = make_checked_in_appointment()
    admission = make_admission(patient=appointment.patient)

    content = (
        client_for_role(Role.RECEPTIONIST)
        .get(reverse("appointments:appointment_detail", args=[appointment.pk]))
        .content.decode()
    )

    assert f"Currently admitted — {admission.number}" in content
    assert "Admit patient" not in content


def test_patient_page_banner(client_for_role, make_admission):
    admission = make_admission()

    content = (
        client_for_role(Role.NURSE)
        .get(reverse("patients:patient_detail", args=[admission.patient.pk]))
        .content.decode()
    )

    assert "Currently admitted" in content and admission.number in content


EXPECTED_NAV = {
    Role.ADMIN: ["Admissions", "Wards & beds"],
    Role.DOCTOR: ["Admissions"],
    Role.NURSE: ["Admissions"],
    Role.RECEPTIONIST: ["Admissions"],
}


@pytest.mark.parametrize("role", list(Role))
def test_admissions_nav(client_for_role, role):
    labels = [
        item["label"]
        for item in client_for_role(role).get(reverse("dashboard")).context["nav_items"]
    ]

    assert [
        label for label in labels if label in {"Admissions", "Wards & beds"}
    ] == EXPECTED_NAV.get(role, [])


def test_nurse_dashboard_quick_actions(client_for_role):
    content = client_for_role(Role.NURSE).get(reverse("dashboard")).content.decode()

    assert (
        reverse("admissions:admission_list") in content
        and reverse("admissions:bed_board") in content
    )


# --- List tabs with data (regression: the discharged tab crashed on |last) -------------


@pytest.fixture
def one_of_each(make_admission, make_bed, make_ward, make_doctor, make_user, make_appointment):
    """One current inpatient, one discharged patient who was moved once, one outpatient."""
    current = make_admission(bed=make_bed(ward=make_ward(name="General A"), bed_number="A1"))
    moved = make_admission(
        bed=make_bed(ward=make_ward(name="ICU"), bed_number="I1"),
        admitted_at=timezone.now() - timedelta(days=2),
    )
    services.transfer_bed(
        moved,
        new_bed=make_bed(ward=make_ward(name="Ward C"), bed_number="C3"),
        transferred_at=timezone.now() - timedelta(days=1),
        acting_user=make_user(role=Role.NURSE),
    )
    services.discharge_patient(
        moved, discharge_type="HOME", discharge_summary="Well", acting_user=make_doctor().user
    )
    outpatient = make_appointment(date=timezone.localdate())
    return current, moved, outpatient


@pytest.mark.parametrize("role", [Role.NURSE, Role.RECEPTIONIST])
def test_inpatients_tab_renders_with_data(client_for_role, one_of_each, role):
    current, _, _ = one_of_each

    response = client_for_role(role).get(
        reverse("admissions:admission_list"), {"tab": "inpatients"}
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert current.number in content and "General A — A1" in content


@pytest.mark.parametrize("role", [Role.NURSE, Role.RECEPTIONIST])
def test_discharged_tab_renders_final_bed(client_for_role, one_of_each, role):
    _, moved, _ = one_of_each

    response = client_for_role(role).get(
        reverse("admissions:admission_list"), {"tab": "discharged"}
    )

    content = response.content.decode()
    assert response.status_code == 200
    assert moved.number in content
    assert "Ward C — C3" in content  # the bed they left from, not the first one
    assert "ICU — I1" not in content


def test_outpatients_tab_renders_with_data(client_for_role, one_of_each):
    _, _, outpatient = one_of_each

    response = client_for_role(Role.RECEPTIONIST).get(
        reverse("admissions:admission_list"), {"tab": "outpatients"}
    )

    assert response.status_code == 200
    assert outpatient.patient.full_name in response.content.decode()


def test_discharged_tab_has_no_per_row_queries(
    client_for_role, one_of_each, make_admission, make_doctor, django_assert_max_num_queries
):
    client = client_for_role(Role.NURSE)
    url = reverse("admissions:admission_list")
    doctor_user = make_doctor().user
    for _ in range(3):  # more discharged rows must not mean more queries
        extra = make_admission(admitted_at=timezone.now() - timedelta(days=1))
        services.discharge_patient(
            extra, discharge_type="HOME", discharge_summary="Well", acting_user=doctor_user
        )
    client.get(url, {"tab": "discharged"})  # warm up session/user queries

    with django_assert_max_num_queries(12):
        response = client.get(url, {"tab": "discharged"})

    assert len(response.context["admissions"]) == 4


def test_final_bed_property(make_admission):
    admission = make_admission()

    assert admission.final_bed == admission.current_bed
