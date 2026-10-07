"""Schedule hints on the booking page (issue #48 item 7). Read-only: booking rules are
tested in test_services.py and are unchanged."""

from datetime import date, time, timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from accounts.models import Role
from appointments.models import Status
from appointments.selectors import next_available_dates
from appointments.tests.clock import MONDAY, MONDAY_8AM, colombo

pytestmark = pytest.mark.django_db

WEDNESDAY = MONDAY + timedelta(days=2)


@pytest.fixture
def mon_wed_doctor(make_scheduled_doctor):
    """Works Monday and Wednesday, 09:00-12:00 in 15-minute slots (12 slots a day)."""
    return make_scheduled_doctor(weekdays=[0, 2])


def fill_day(make_appointment, doctor, day):
    start = timezone.datetime.combine(day, time(9))
    for n in range(12):
        make_appointment(
            doctor=doctor, date=day, start_time=(start + timedelta(minutes=15 * n)).time()
        )


def test_skips_non_working_days_and_stops_at_the_limit(mon_wed_doctor):
    dates = next_available_dates(mon_wed_doctor, now=MONDAY_8AM)

    assert dates == [
        MONDAY,
        WEDNESDAY,
        MONDAY + timedelta(days=7),
        WEDNESDAY + timedelta(days=7),
        MONDAY + timedelta(days=14),
    ]
    assert len(next_available_dates(mon_wed_doctor, now=MONDAY_8AM, limit=2)) == 2


def test_skips_fully_booked_days(mon_wed_doctor, make_appointment):
    fill_day(make_appointment, mon_wed_doctor, WEDNESDAY)

    dates = next_available_dates(mon_wed_doctor, now=MONDAY_8AM, limit=3)
    assert WEDNESDAY not in dates
    assert dates == [MONDAY, MONDAY + timedelta(days=7), WEDNESDAY + timedelta(days=7)]


def test_cancelled_bookings_free_their_slot(mon_wed_doctor, make_appointment):
    fill_day(make_appointment, mon_wed_doctor, WEDNESDAY)
    first = mon_wed_doctor.appointments.filter(date=WEDNESDAY).first()
    first.status = Status.CANCELLED
    first.save()

    assert WEDNESDAY in next_available_dates(mon_wed_doctor, now=MONDAY_8AM, limit=3)


def test_respects_past_times_today(mon_wed_doctor):
    # At 11:50 every Monday slot (last one 11:45) has started: Monday is not offered.
    assert MONDAY not in next_available_dates(mon_wed_doctor, now=colombo(MONDAY, 11, 50))
    # At 11:40 the 11:45 slot is still ahead.
    assert MONDAY in next_available_dates(mon_wed_doctor, now=colombo(MONDAY, 11, 40))


def test_stays_within_the_booking_window_and_horizon(mon_wed_doctor, settings):
    settings.APPOINTMENT_BOOKING_WINDOW_DAYS = 3
    assert next_available_dates(mon_wed_doctor, now=MONDAY_8AM) == [MONDAY, WEDNESDAY]

    settings.APPOINTMENT_BOOKING_WINDOW_DAYS = 60
    dates = next_available_dates(mon_wed_doctor, now=MONDAY_8AM, horizon_days=1)
    assert dates == [MONDAY]


def test_no_dates_for_a_doctor_without_working_days(make_doctor):
    assert next_available_dates(make_doctor(), now=MONDAY_8AM) == []


def test_bookings_are_loaded_in_one_query(mon_wed_doctor, make_appointment):
    for day in (MONDAY, WEDNESDAY, MONDAY + timedelta(days=7)):
        make_appointment(doctor=mon_wed_doctor, date=day, start_time=time(9))

    with CaptureQueriesContext(connection) as context:
        dates = next_available_dates(mon_wed_doctor, now=MONDAY_8AM)
    booking_queries = [
        q["sql"] for q in context.captured_queries if "appointments_appointment" in q["sql"]
    ]
    assert len(dates) == 5
    assert len(booking_queries) == 1
    # Schedule: one query for the working weekdays + one per working weekday (2 here).
    assert len(context.captured_queries) <= 6


# --- Booking page ---------------------------------------------------------------------------


def next_weekday(weekday):
    """The next date (after today) that falls on `weekday` (0 = Monday)."""
    today = timezone.localdate()
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


def book_page(client, **params):
    response = client.get(reverse("appointments:book"), params)
    assert response.status_code == 200
    return response


def test_schedule_card_appears_when_a_doctor_is_chosen(
    client_for_role, make_patient, make_scheduled_doctor
):
    doctor = make_scheduled_doctor(weekdays=[0, 2])
    patient = make_patient()
    client = client_for_role(Role.RECEPTIONIST)

    without = book_page(client, patient=patient.pk).content.decode()
    assert "schedule</h2>" not in without

    html = book_page(client, patient=patient.pk, doctor=doctor.pk).content.decode()
    assert f"{escape(str(doctor))}'s schedule" in html
    assert "09:00–12:00" in html and "15 min" in html
    assert "Not working" in html  # Tuesday, Thursday, ...


def test_non_working_day_warning(client_for_role, make_patient, make_scheduled_doctor):
    doctor = make_scheduled_doctor(weekdays=[0])
    tuesday = next_weekday(1)
    html = book_page(
        client_for_role(Role.RECEPTIONIST),
        patient=make_patient().pk,
        doctor=doctor.pk,
        date=tuesday.isoformat(),
    ).content.decode()

    assert "alert-warning" in html
    assert f"{escape(str(doctor))} doesn't work on Tuesdays — pick another date." in html


def test_no_warning_on_a_working_day(client_for_role, make_patient, make_scheduled_doctor):
    doctor = make_scheduled_doctor(weekdays=[0])
    html = book_page(
        client_for_role(Role.RECEPTIONIST),
        patient=make_patient().pk,
        doctor=doctor.pk,
        date=next_weekday(0).isoformat(),
    ).content.decode()
    assert "doesn't work on" not in html


def test_next_date_links_keep_patient_department_and_doctor(
    client_for_role, make_patient, make_scheduled_doctor
):
    doctor = make_scheduled_doctor()  # works every day
    patient = make_patient()
    response = book_page(
        client_for_role(Role.RECEPTIONIST),
        patient=patient.pk,
        department=doctor.department_id,
        doctor=doctor.pk,
    )
    links = response.context["next_dates"]

    assert 1 <= len(links) <= 5
    day, url = links[0]
    assert isinstance(day, date)
    for part in (
        f"patient={patient.pk}",
        f"department={doctor.department_id}",
        f"doctor={doctor.pk}",
        f"date={day.isoformat()}",
    ):
        assert part in url
    assert url.replace("&", "&amp;") in response.content.decode()
