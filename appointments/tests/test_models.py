from datetime import time
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from appointments.models import Status
from appointments.tests.clock import MONDAY, colombo


def test_double_booking_same_doctor_slot_rejected_by_database(make_appointment, make_doctor):
    doctor = make_doctor()
    make_appointment(doctor=doctor)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_appointment(doctor=doctor)


def test_cancelled_appointment_frees_the_slot(make_appointment, make_doctor):
    doctor = make_doctor()
    make_appointment(doctor=doctor, status=Status.CANCELLED)

    make_appointment(doctor=doctor)  # does not raise


def test_no_show_keeps_the_slot_taken(make_appointment, make_doctor):
    doctor = make_doctor()
    make_appointment(doctor=doctor, status=Status.NO_SHOW)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_appointment(doctor=doctor)


def test_patient_cannot_be_with_two_doctors_at_once(make_appointment, make_patient):
    patient = make_patient()
    make_appointment(patient=patient)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_appointment(patient=patient)  # another doctor, same date and time


def test_end_must_be_after_start(make_appointment):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_appointment(start_time=time(10), end_time=time(9, 45))


def test_fee_cannot_be_negative(make_appointment):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_appointment(consultation_fee=Decimal("-1"))


def test_datetimes_and_helpers(make_appointment):
    appointment = make_appointment(date=MONDAY, start_time=time(9), end_time=time(9, 15))

    assert appointment.start_datetime == colombo(MONDAY, 9)
    assert appointment.end_datetime == colombo(MONDAY, 9, 15)
    assert not appointment.is_past(now=colombo(MONDAY, 8, 59))
    assert appointment.is_past(now=colombo(MONDAY, 9))
    assert appointment.can_transition_to(Status.CHECKED_IN)
    assert not appointment.can_transition_to(Status.COMPLETED)
