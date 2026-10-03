from datetime import time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from appointments import services
from appointments.models import ALLOWED_TRANSITIONS, Appointment, Status
from appointments.tests.clock import MONDAY, MONDAY_8AM, NEXT_MONDAY, SUNDAY_BEFORE, colombo
from doctors.models import Weekday

pytestmark = pytest.mark.django_db


@pytest.fixture
def doctor(make_scheduled_doctor):
    """Works Mondays 09:00-12:00 in 15-minute slots, fee Rs. 1,500."""
    return make_scheduled_doctor(weekdays=[Weekday.MONDAY])


@pytest.fixture
def receptionist(make_user):
    return make_user()


def book(patient, doctor, user, *, date=MONDAY, start=time(9), now=MONDAY_8AM):
    return services.book_appointment(
        patient=patient,
        doctor=doctor,
        date=date,
        start_time=start,
        reason="  Fever  ",
        acting_user=user,
        now=now,
    )


# --- Booking ------------------------------------------------------------------


def test_book_sets_end_time_and_fee_snapshot(doctor, make_patient, receptionist):
    appointment = book(make_patient(), doctor, receptionist)

    assert appointment.end_time == time(9, 15)
    assert appointment.consultation_fee == Decimal("1500.00")
    assert appointment.reason == "Fever"
    assert appointment.created_by == receptionist
    assert appointment.status == Status.BOOKED

    doctor.consultation_fee = Decimal("9999")
    doctor.save()
    appointment.refresh_from_db()
    assert appointment.consultation_fee == Decimal("1500.00")


@pytest.mark.parametrize(
    ("setup", "kwargs", "message"),
    [
        ("inactive_doctor", {}, "not active"),
        ("inactive_department", {}, "department is inactive"),
        (None, {"date": SUNDAY_BEFORE}, "past date"),
        (None, {"start": time(9), "now": colombo(MONDAY, 9, 20)}, "already passed"),
        (None, {"start": time(13)}, "not an available slot"),
        (None, {"start": time(9, 5)}, "not an available slot"),
        (None, {"date": MONDAY + timedelta(days=70)}, "days ahead"),
    ],
    ids=[
        "inactive-doctor",
        "inactive-department",
        "past-date",
        "past-time-today",
        "off-schedule",
        "between-slots",
        "beyond-window",
    ],
)
def test_book_rejections(doctor, make_patient, receptionist, setup, kwargs, message):
    if setup == "inactive_doctor":
        doctor.user.is_active = False
        doctor.user.save()
    elif setup == "inactive_department":
        doctor.department.is_active = False
        doctor.department.save()

    with pytest.raises(ValidationError, match=message):
        book(make_patient(), doctor, receptionist, **kwargs)
    assert not Appointment.objects.exists()


def test_same_patient_same_doctor_same_day_rejected(doctor, make_patient, receptionist):
    patient = make_patient()
    book(patient, doctor, receptionist, start=time(9))

    with pytest.raises(ValidationError, match="already has an appointment with this doctor"):
        book(patient, doctor, receptionist, start=time(10))


def test_booked_slot_rejected(doctor, make_patient, receptionist):
    book(make_patient(), doctor, receptionist, start=time(9))

    with pytest.raises(ValidationError, match="not an available slot"):
        book(make_patient(), doctor, receptionist, start=time(9))


def test_race_for_the_same_slot_gives_friendly_error(
    doctor, make_patient, make_appointment, receptionist, monkeypatch
):
    # Simulate a race: our availability check ran before the other booking was saved,
    # so it still thinks 09:00 is free. The database constraint must catch it.
    make_appointment(doctor=doctor, date=MONDAY, start_time=time(9))
    monkeypatch.setattr(
        services, "available_slots", lambda *args, **kwargs: [(time(9), time(9, 15))]
    )

    with pytest.raises(ValidationError, match="just booked by someone else"):
        book(make_patient(), doctor, receptionist, start=time(9))

    # The savepoint kept the transaction usable: queries still work.
    assert Appointment.objects.count() == 1


# --- Status transitions -------------------------------------------------------

AFTER_START = colombo(MONDAY, 9, 30)  # on the appointment day, after its 09:00 start


def change_status(appointment, new_status):
    doctor_user = appointment.doctor.user
    if new_status == Status.CHECKED_IN:
        services.check_in_appointment(appointment, acting_user=doctor_user, now=AFTER_START)
    elif new_status == Status.COMPLETED:
        services.complete_appointment(appointment, acting_user=doctor_user, now=AFTER_START)
    elif new_status == Status.CANCELLED:
        services.cancel_appointment(
            appointment, reason="Patient request", acting_user=doctor_user, now=AFTER_START
        )
    elif new_status == Status.NO_SHOW:
        services.mark_no_show(appointment, acting_user=doctor_user, now=AFTER_START)


TARGETS = [Status.CHECKED_IN, Status.COMPLETED, Status.CANCELLED, Status.NO_SHOW]


@pytest.mark.parametrize("from_status", list(Status))
@pytest.mark.parametrize("to_status", TARGETS)
def test_every_transition_pair(make_appointment, make_record, from_status, to_status):
    appointment = make_appointment(status=from_status)
    allowed = to_status in ALLOWED_TRANSITIONS.get(from_status, set())
    if to_status == Status.COMPLETED:
        # Completing needs a finalized consultation record.
        make_record(appointment=appointment, status="FINALIZED")

    if allowed:
        change_status(appointment, to_status)
        appointment.refresh_from_db()
        assert appointment.status == to_status
    else:
        with pytest.raises(ValidationError, match="can't be changed"):
            change_status(appointment, to_status)
        appointment.refresh_from_db()
        assert appointment.status == from_status


def test_cancel_requires_reason(make_appointment, receptionist):
    appointment = make_appointment()

    with pytest.raises(ValidationError, match="reason"):
        services.cancel_appointment(appointment, reason="   ", acting_user=receptionist)
    appointment.refresh_from_db()
    assert appointment.status == Status.BOOKED


def test_cancel_records_who_and_when(make_appointment, receptionist):
    appointment = make_appointment()

    services.cancel_appointment(
        appointment, reason="Travelling", acting_user=receptionist, now=MONDAY_8AM
    )

    assert appointment.cancelled_by == receptionist
    assert appointment.cancelled_at == MONDAY_8AM
    assert appointment.cancel_reason == "Travelling"


def test_check_in_not_before_the_day(make_appointment, receptionist):
    appointment = make_appointment(date=MONDAY)

    with pytest.raises(ValidationError, match="day of the appointment"):
        services.check_in_appointment(
            appointment, acting_user=receptionist, now=colombo(SUNDAY_BEFORE, 17)
        )


def test_no_show_not_before_start(make_appointment, receptionist):
    appointment = make_appointment(date=MONDAY, start_time=time(9))

    with pytest.raises(ValidationError, match="after it starts"):
        services.mark_no_show(appointment, acting_user=receptionist, now=colombo(MONDAY, 8, 59))


def test_only_own_doctor_can_complete(make_appointment, make_doctor):
    appointment = make_appointment(status=Status.CHECKED_IN)
    other_doctor = make_doctor()

    with pytest.raises(PermissionDenied):
        services.complete_appointment(appointment, acting_user=other_doctor.user)
    appointment.refresh_from_db()
    assert appointment.status == Status.CHECKED_IN


# --- Rescheduling -------------------------------------------------------------


def test_reschedule_moves_slot_and_counts(doctor, make_patient, receptionist):
    appointment = book(make_patient(), doctor, receptionist, start=time(9))

    services.reschedule_appointment(
        appointment, date=NEXT_MONDAY, start_time=time(11), acting_user=receptionist, now=MONDAY_8AM
    )

    appointment.refresh_from_db()
    assert (appointment.date, appointment.start_time, appointment.end_time) == (
        NEXT_MONDAY,
        time(11),
        time(11, 15),
    )
    assert appointment.reschedule_count == 1
    # The old slot is free again.
    book(make_patient(), doctor, receptionist, start=time(9))


def test_reschedule_within_same_day(doctor, make_patient, receptionist):
    appointment = book(make_patient(), doctor, receptionist, start=time(9))

    services.reschedule_appointment(
        appointment, date=MONDAY, start_time=time(9, 15), acting_user=receptionist, now=MONDAY_8AM
    )

    assert appointment.start_time == time(9, 15)


def test_reschedule_only_booked(doctor, make_appointment, receptionist):
    appointment = make_appointment(doctor=doctor, status=Status.CHECKED_IN)

    with pytest.raises(ValidationError, match="Only booked"):
        services.reschedule_appointment(
            appointment,
            date=NEXT_MONDAY,
            start_time=time(10),
            acting_user=receptionist,
            now=MONDAY_8AM,
        )


def test_reschedule_to_taken_slot_rejected(doctor, make_patient, receptionist):
    appointment = book(make_patient(), doctor, receptionist, start=time(9))
    book(make_patient(), doctor, receptionist, start=time(10))

    with pytest.raises(ValidationError, match="not an available slot"):
        services.reschedule_appointment(
            appointment, date=MONDAY, start_time=time(10), acting_user=receptionist, now=MONDAY_8AM
        )


def test_complete_requires_finalized_record(make_appointment, make_record):
    appointment = make_appointment(status=Status.CHECKED_IN)
    doctor_user = appointment.doctor.user

    with pytest.raises(ValidationError, match="Finalize the consultation"):
        services.complete_appointment(appointment, acting_user=doctor_user, now=AFTER_START)

    make_record(appointment=appointment)  # a DRAFT record is not enough
    appointment.refresh_from_db()
    with pytest.raises(ValidationError, match="Finalize the consultation"):
        services.complete_appointment(appointment, acting_user=doctor_user, now=AFTER_START)
