from datetime import date, time, timedelta

import pytest

from accounts.models import Role
from appointments import selectors
from appointments.models import Status
from appointments.tests.clock import MONDAY, MONDAY_8AM, NEXT_MONDAY, SUNDAY_BEFORE, colombo
from doctors.models import Weekday

pytestmark = pytest.mark.django_db


@pytest.fixture
def doctor(make_scheduled_doctor):
    """Works Mondays 09:00-10:00 in 15-minute slots (4 slots)."""
    return make_scheduled_doctor(weekdays=[Weekday.MONDAY], start=time(9), end=time(10))


def starts(slots):
    return [start for start, _end in slots]


# --- available_slots ---------------------------------------------------------


def test_slots_come_with_end_times(doctor):
    slots = selectors.available_slots(doctor, MONDAY, now=MONDAY_8AM)

    assert slots[0] == (time(9, 0), time(9, 15))
    assert starts(slots) == [time(9, 0), time(9, 15), time(9, 30), time(9, 45)]


def test_booked_slot_is_excluded_and_cancelled_is_included(doctor, make_appointment):
    make_appointment(doctor=doctor, date=MONDAY, start_time=time(9, 15))
    make_appointment(doctor=doctor, date=MONDAY, start_time=time(9, 30), status=Status.CANCELLED)

    assert starts(selectors.available_slots(doctor, MONDAY, now=MONDAY_8AM)) == [
        time(9, 0),
        time(9, 30),
        time(9, 45),
    ]


def test_slots_that_already_started_today_are_excluded(doctor):
    slots = selectors.available_slots(doctor, MONDAY, now=colombo(MONDAY, 9, 20))

    assert starts(slots) == [time(9, 30), time(9, 45)]


def test_past_date_has_no_slots(doctor):
    assert selectors.available_slots(doctor, MONDAY - timedelta(days=7), now=MONDAY_8AM) == []
    assert selectors.available_slots(doctor, SUNDAY_BEFORE, now=MONDAY_8AM) == []


def test_booking_window(doctor, settings):
    settings.APPOINTMENT_BOOKING_WINDOW_DAYS = 7

    assert len(selectors.available_slots(doctor, NEXT_MONDAY, now=MONDAY_8AM)) == 4
    assert selectors.available_slots(doctor, NEXT_MONDAY + timedelta(days=7), now=MONDAY_8AM) == []


def test_exclude_appointment_frees_its_own_slot(doctor, make_appointment):
    appointment = make_appointment(doctor=doctor, date=MONDAY, start_time=time(9))

    slots = selectors.available_slots(
        doctor, MONDAY, now=MONDAY_8AM, exclude_appointment=appointment
    )

    assert time(9) in starts(slots)


def test_inactive_department_has_no_slots(doctor):
    doctor.department.is_active = False
    doctor.department.save()

    assert selectors.available_slots(doctor, MONDAY, now=MONDAY_8AM) == []


# --- Scoping and lists --------------------------------------------------------


def test_doctor_only_sees_own_appointments(make_doctor, make_appointment, make_user):
    mine, theirs = make_doctor(), make_doctor()
    own = make_appointment(doctor=mine)
    other = make_appointment(doctor=theirs, start_time=time(10))

    assert list(selectors.visible_appointments(mine.user)) == [own]
    assert list(selectors.appointment_list(user=mine.user, doctor=theirs)) == []
    receptionist = make_user(role=Role.RECEPTIONIST)
    assert set(selectors.visible_appointments(receptionist)) == {own, other}


def test_doctor_user_without_profile_sees_nothing(make_appointment, make_user):
    make_appointment()

    assert list(selectors.visible_appointments(make_user(role=Role.DOCTOR))) == []


def test_appointment_list_filters(make_appointment, make_patient, make_user):
    kamal = make_patient(first_name="Kamal")
    booked = make_appointment(patient=kamal)
    cancelled = make_appointment(start_time=time(10), status=Status.CANCELLED)
    make_appointment(date=MONDAY + timedelta(days=1))
    admin = make_user(role=Role.ADMIN)

    assert set(selectors.appointment_list(user=admin, date=MONDAY)) == {booked, cancelled}
    assert list(selectors.appointment_list(user=admin, status=Status.CANCELLED)) == [cancelled]
    assert list(selectors.appointment_list(user=admin, query="kamal")) == [booked]


def test_week_calendar_groups_by_day(make_appointment, make_user):
    tuesday = make_appointment(date=date(2026, 10, 6))
    make_appointment(date=date(2026, 10, 13))  # next week
    admin = make_user(role=Role.ADMIN)

    calendar = selectors.week_calendar(user=admin, week_start=date(2026, 10, 8))  # a Thursday

    assert calendar["week_start"] == MONDAY
    assert [day["date"] for day in calendar["days"]] == [
        MONDAY + timedelta(days=i) for i in range(7)
    ]
    assert calendar["days"][1]["appointments"] == [tuesday]
    assert sum(len(day["appointments"]) for day in calendar["days"]) == 1
    assert calendar["prev_week"] == date(2026, 9, 28)
    assert calendar["next_week"] == date(2026, 10, 12)


def test_todays_appointments(make_appointment, make_user):
    today = make_appointment(date=MONDAY)
    make_appointment(date=MONDAY + timedelta(days=1))

    assert list(selectors.todays_appointments(user=make_user(role=Role.NURSE), now=MONDAY_8AM)) == [
        today
    ]


def test_patient_appointments_split(make_appointment, make_patient):
    patient = make_patient()
    older = make_appointment(patient=patient, date=date(2026, 9, 1))
    newer_past = make_appointment(patient=patient, date=date(2026, 10, 1))
    today = make_appointment(patient=patient, date=MONDAY)
    future = make_appointment(patient=patient, date=NEXT_MONDAY)

    result = selectors.patient_appointments(patient, now=MONDAY_8AM)

    assert result["upcoming"] == [today, future]
    assert result["past"] == [newer_past, older]
