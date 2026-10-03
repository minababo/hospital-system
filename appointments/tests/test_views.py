"""View tests use real time, so dates are relative to today (tomorrow is always bookable)."""

from datetime import time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from appointments.models import Appointment, Status

VIEW = {Role.ADMIN, Role.RECEPTIONIST, Role.NURSE, Role.DOCTOR}
BOOK = {Role.ADMIN, Role.RECEPTIONIST}
CHECKIN = {Role.ADMIN, Role.RECEPTIONIST, Role.NURSE}
COMPLETE = {Role.DOCTOR}

# (url name, takes appointment pk?, method, allowed roles, status for an allowed role)
URLS = [
    ("appointments:appointment_list", False, "get", VIEW, 200),
    ("appointments:calendar", False, "get", VIEW, 200),
    ("appointments:book", False, "get", BOOK, 200),
    ("appointments:appointment_detail", True, "get", VIEW, 200),
    ("appointments:reschedule", True, "get", BOOK, 200),
    ("appointments:cancel", True, "post", BOOK, 302),
    ("appointments:check_in", True, "post", CHECKIN, 302),
    ("appointments:complete", True, "post", COMPLETE, 302),
    ("appointments:no_show", True, "post", BOOK, 302),
]
POST_ONLY = [url for url in URLS if url[2] == "post"]


def tomorrow():
    return timezone.localdate() + timedelta(days=1)


def url_for(name, takes_pk, appointment):
    return reverse(name, args=[appointment.pk]) if takes_pk else reverse(name)


@pytest.fixture
def appointment(make_appointment, make_scheduled_doctor):
    return make_appointment(doctor=make_scheduled_doctor(), date=tomorrow())


# --- RBAC -------------------------------------------------------------------


@pytest.mark.parametrize(("name", "takes_pk", "method", "_roles", "_status"), URLS)
def test_anonymous_redirected(client, appointment, name, takes_pk, method, _roles, _status):
    response = getattr(client, method)(url_for(name, takes_pk, appointment))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "takes_pk", "method", "roles", "status"), URLS)
def test_rbac_matrix(
    client,
    make_user,
    make_scheduled_doctor,
    make_appointment,
    role,
    name,
    takes_pk,
    method,
    roles,
    status,
):
    user = make_user(role=role)
    # A doctor only sees their own appointments, so give the doctor one.
    doctor = make_scheduled_doctor(user=user) if role == Role.DOCTOR else make_scheduled_doctor()
    appointment = make_appointment(doctor=doctor, date=tomorrow())
    client.force_login(user)

    data = {"reason": "Patient asked"} if method == "post" else None
    response = getattr(client, method)(url_for(name, takes_pk, appointment), data)

    assert response.status_code == (status if role in roles else 403)


@pytest.mark.parametrize(("name", "takes_pk", "_method", "_roles", "_status"), POST_ONLY)
def test_post_only_urls_reject_get(
    client_for_role, appointment, name, takes_pk, _method, _roles, _status
):
    response = client_for_role(Role.ADMIN).get(url_for(name, takes_pk, appointment))

    assert response.status_code == 405


def test_doctor_cannot_open_another_doctors_appointment(client, appointment, make_scheduled_doctor):
    other_doctor = make_scheduled_doctor()
    client.force_login(other_doctor.user)

    assert (
        client.get(reverse("appointments:appointment_detail", args=[appointment.pk])).status_code
        == 404
    )
    assert client.post(reverse("appointments:complete", args=[appointment.pk])).status_code == 404


# --- List / calendar ---------------------------------------------------------


def test_list_defaults_to_today_with_day_links(
    client_for_role, make_appointment, make_scheduled_doctor
):
    today = timezone.localdate()
    todays = make_appointment(doctor=make_scheduled_doctor(), date=today)
    make_appointment(date=tomorrow())

    response = client_for_role(Role.RECEPTIONIST).get(reverse("appointments:appointment_list"))

    assert response.context["appointments"] == [todays]
    assert f"date={(today - timedelta(days=1)):%Y-%m-%d}" in response.content.decode()


def test_doctor_list_only_shows_own(client, make_appointment, make_scheduled_doctor):
    mine, theirs = make_scheduled_doctor(), make_scheduled_doctor()
    own = make_appointment(doctor=mine, date=tomorrow())
    make_appointment(doctor=theirs, date=tomorrow(), start_time=time(10))
    client.force_login(mine.user)

    response = client.get(
        reverse("appointments:appointment_list"),
        {"date": f"{tomorrow():%Y-%m-%d}", "doctor": theirs.pk},  # doctor filter is ignored
    )

    assert response.context["appointments"] == [own]


def test_calendar_week_navigation(client_for_role, appointment):
    response = client_for_role(Role.NURSE).get(
        reverse("appointments:calendar"), {"week": f"{appointment.date:%Y-%m-%d}"}
    )

    calendar = response.context["calendar"]
    content = response.content.decode()
    assert appointment in [a for day in calendar["days"] for a in day["appointments"]]
    assert f"week={calendar['prev_week']:%Y-%m-%d}" in content
    assert f"week={calendar['next_week']:%Y-%m-%d}" in content


def test_doctor_calendar_is_always_their_own(client, appointment, make_scheduled_doctor):
    other_doctor = make_scheduled_doctor()
    client.force_login(other_doctor.user)

    response = client.get(
        reverse("appointments:calendar"),
        {"week": f"{appointment.date:%Y-%m-%d}", "doctor": appointment.doctor.pk},
    )

    assert response.context["doctor"] == other_doctor
    assert all(not day["appointments"] for day in response.context["calendar"]["days"])


# --- Booking wizard -----------------------------------------------------------


def test_book_step_patient_search(client_for_role, make_patient):
    patient = make_patient(first_name="Kamal")
    client = client_for_role(Role.RECEPTIONIST)

    first = client.get(reverse("appointments:book"))
    search = client.get(reverse("appointments:book"), {"q": "kamal"})

    assert first.context["step"] == "patient"
    assert list(search.context["patients"]) == [patient]


def test_book_step_doctor_choice(client_for_role, make_patient):
    response = client_for_role(Role.RECEPTIONIST).get(
        reverse("appointments:book"), {"patient": make_patient().pk}
    )

    assert response.context["step"] == "doctor"


def test_book_step_slots_listed(client_for_role, make_patient, make_scheduled_doctor):
    doctor = make_scheduled_doctor()

    response = client_for_role(Role.RECEPTIONIST).get(
        reverse("appointments:book"),
        {"patient": make_patient().pk, "doctor": doctor.pk, "date": f"{tomorrow():%Y-%m-%d}"},
    )

    assert response.context["step"] == "slot"
    assert len(response.context["slots"]) == 12  # 09:00-12:00 in 15-minute slots
    assert "09:00–09:15" in response.content.decode()


def booking_post(patient, doctor, start="09:00"):
    return {
        "patient": patient.pk,
        "doctor": doctor.pk,
        "date": f"{tomorrow():%Y-%m-%d}",
        "start_time": start,
        "reason": "Headache",
    }


def test_book_post_success(client_for_role, make_patient, make_scheduled_doctor):
    patient, doctor = make_patient(), make_scheduled_doctor()

    response = client_for_role(Role.RECEPTIONIST).post(
        reverse("appointments:book"), booking_post(patient, doctor)
    )

    appointment = Appointment.objects.get()
    assert response.status_code == 302
    assert response.url == reverse("appointments:appointment_detail", args=[appointment.pk])
    assert appointment.patient == patient
    assert appointment.start_time == time(9)


def test_book_post_slot_taken(
    client_for_role, make_patient, make_scheduled_doctor, make_appointment
):
    doctor = make_scheduled_doctor()
    make_appointment(doctor=doctor, date=tomorrow(), start_time=time(9))

    response = client_for_role(Role.RECEPTIONIST).post(
        reverse("appointments:book"), booking_post(make_patient(), doctor)
    )

    assert response.status_code == 200
    assert "not an available slot" in response.content.decode()
    assert Appointment.objects.count() == 1
    assert (time(9), time(9, 15)) not in response.context["slots"]


def test_book_post_requires_reason_and_slot(client_for_role, make_patient, make_scheduled_doctor):
    data = booking_post(make_patient(), make_scheduled_doctor())
    data.update(reason="", start_time="")

    response = client_for_role(Role.RECEPTIONIST).post(reverse("appointments:book"), data)

    errors = response.context["confirm_form"].errors
    assert "reason" in errors and "start_time" in errors
    assert not Appointment.objects.exists()


# --- Detail / actions -----------------------------------------------------------


def test_detail_shows_fee_and_actions(client_for_role, appointment):
    response = client_for_role(Role.RECEPTIONIST).get(
        reverse("appointments:appointment_detail", args=[appointment.pk])
    )

    assert "Rs. 1,500.00" in response.content.decode()
    assert {"reschedule", "cancel"} <= response.context["actions"]
    assert "check_in" not in response.context["actions"]  # it's tomorrow


def test_cancel_via_view(client_for_role, appointment):
    client = client_for_role(Role.RECEPTIONIST)

    client.post(reverse("appointments:cancel", args=[appointment.pk]), {"reason": "Sick"})

    appointment.refresh_from_db()
    assert appointment.status == Status.CANCELLED


def test_cancel_without_reason_shows_error(client_for_role, appointment):
    response = client_for_role(Role.RECEPTIONIST).post(
        reverse("appointments:cancel", args=[appointment.pk]), {"reason": ""}, follow=True
    )

    assert "reason for cancelling" in response.content.decode()
    appointment.refresh_from_db()
    assert appointment.status == Status.BOOKED


def test_check_in_and_complete_today(
    client_for_role, client, make_appointment, make_scheduled_doctor, make_record
):
    doctor = make_scheduled_doctor(start=time(0), end=time(23, 45))
    appointment = make_appointment(
        doctor=doctor, date=timezone.localdate(), start_time=time(23, 30)
    )

    client_for_role(Role.NURSE).post(reverse("appointments:check_in", args=[appointment.pk]))
    appointment.refresh_from_db()
    assert appointment.status == Status.CHECKED_IN

    make_record(appointment=appointment, status="FINALIZED")
    client.force_login(doctor.user)
    client.post(reverse("appointments:complete", args=[appointment.pk]))
    appointment.refresh_from_db()
    assert appointment.status == Status.COMPLETED


def test_reschedule_via_view(client_for_role, appointment):
    client = client_for_role(Role.RECEPTIONIST)
    url = reverse("appointments:reschedule", args=[appointment.pk])
    new_date = tomorrow() + timedelta(days=1)

    page = client.get(url, {"date": f"{new_date:%Y-%m-%d}"})
    response = client.post(url, {"date": f"{new_date:%Y-%m-%d}", "start_time": "10:00"})

    assert len(page.context["slots"]) == 12
    assert response.status_code == 302
    appointment.refresh_from_db()
    assert (appointment.date, appointment.start_time) == (new_date, time(10))
    assert appointment.reschedule_count == 1


# --- Patient integration ------------------------------------------------------


@pytest.mark.parametrize("role", [Role.ADMIN, Role.RECEPTIONIST, Role.NURSE, Role.DOCTOR])
def test_patient_detail_shows_appointments(client_for_role, appointment, role):
    response = client_for_role(role).get(
        reverse("patients:patient_detail", args=[appointment.patient.pk])
    )
    content = response.content.decode()

    assert reverse("appointments:appointment_detail", args=[appointment.pk]) in content
    book_link = f"{reverse('appointments:book')}?patient={appointment.patient.pk}"
    assert (book_link in content) == (role in BOOK)


def test_patient_history_includes_appointments(client_for_role, appointment):
    response = client_for_role(Role.DOCTOR).get(
        reverse("patients:patient_history", args=[appointment.patient.pk])
    )

    assert f"Appointment booked with {appointment.doctor}" in response.content.decode()


def test_receptionist_dashboard_has_book_button(client_for_role):
    response = client_for_role(Role.RECEPTIONIST).get(reverse("dashboard"))

    assert reverse("appointments:book") in response.content.decode()


@pytest.mark.parametrize("role", list(Role))
def test_appointments_nav_link(client_for_role, role):
    response = client_for_role(role).get(reverse("dashboard"))

    labels = [item["label"] for item in response.context["nav_items"]]
    assert ("Appointments" in labels) == (role in VIEW)
