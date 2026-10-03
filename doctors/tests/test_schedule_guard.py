from datetime import date, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from appointments.models import Status
from doctors import services
from doctors.models import DoctorSchedule, Weekday

TODAY = date(2026, 10, 5)  # Monday
NEXT_MONDAY = date(2026, 10, 12)
LAST_MONDAY = date(2026, 9, 28)


@pytest.fixture
def doctor(make_scheduled_doctor):
    return make_scheduled_doctor(weekdays=[Weekday.MONDAY])  # Monday 09:00-12:00


@pytest.fixture
def block(doctor):
    return doctor.schedules.get()


def test_delete_block_with_future_booking_rejected(doctor, block, make_appointment, admin_user_obj):
    make_appointment(doctor=doctor, date=NEXT_MONDAY, start_time=time(10))
    block_pk = block.pk  # delete() clears block.pk in memory even when it's rolled back

    with pytest.raises(ValidationError, match="1 upcoming appointment"):
        services.delete_schedule(block, acting_user=admin_user_obj, today=TODAY)
    assert DoctorSchedule.objects.filter(pk=block_pk).exists()


def test_deactivate_block_with_future_booking_rejected(
    doctor, block, make_appointment, admin_user_obj
):
    make_appointment(doctor=doctor, date=NEXT_MONDAY, start_time=time(10))

    with pytest.raises(ValidationError, match="Reschedule or cancel"):
        services.update_schedule(block, is_active=False, acting_user=admin_user_obj, today=TODAY)
    block.refresh_from_db()
    assert block.is_active is True


def test_shrink_block_leaving_booking_outside_rejected(
    doctor, block, make_appointment, admin_user_obj
):
    make_appointment(doctor=doctor, date=NEXT_MONDAY, start_time=time(11))

    with pytest.raises(ValidationError):
        services.update_schedule(block, end_time=time(10), acting_user=admin_user_obj, today=TODAY)
    block.refresh_from_db()
    assert block.end_time == time(12)


def test_moving_block_to_another_day_checks_the_old_day(
    doctor, block, make_appointment, admin_user_obj
):
    make_appointment(doctor=doctor, date=NEXT_MONDAY, start_time=time(10))

    with pytest.raises(ValidationError):
        services.update_schedule(
            block, weekday=Weekday.TUESDAY, acting_user=admin_user_obj, today=TODAY
        )


def test_shrink_that_keeps_bookings_inside_is_allowed(
    doctor, block, make_appointment, admin_user_obj
):
    make_appointment(doctor=doctor, date=NEXT_MONDAY, start_time=time(9))

    services.update_schedule(block, end_time=time(10), acting_user=admin_user_obj, today=TODAY)

    block.refresh_from_db()
    assert block.end_time == time(10)


@pytest.mark.parametrize("kind", ["none", "cancelled", "past"])
def test_delete_allowed_without_live_future_bookings(
    doctor, block, make_appointment, admin_user_obj, kind
):
    if kind == "cancelled":
        make_appointment(doctor=doctor, date=NEXT_MONDAY, status=Status.CANCELLED)
    elif kind == "past":
        make_appointment(doctor=doctor, date=LAST_MONDAY)

    services.delete_schedule(block, acting_user=admin_user_obj, today=TODAY)

    assert not DoctorSchedule.objects.filter(pk=block.pk).exists()


# --- Through the views (real dates) ------------------------------------------


def test_delete_view_shows_error_instead_of_500(client_for_role, doctor, block, make_appointment):
    # The next Monday after today, so the booking is in the future.
    today = timezone.localdate()
    upcoming_monday = today + timedelta(days=7 - today.weekday())
    make_appointment(doctor=doctor, date=upcoming_monday, start_time=time(10))

    response = client_for_role(Role.ADMIN).post(
        reverse("doctors:schedule_delete", args=[block.pk]), follow=True
    )

    assert response.status_code == 200
    assert "Reschedule or cancel them first" in response.content.decode()
    assert DoctorSchedule.objects.filter(pk=block.pk).exists()


def test_update_view_shows_error(client_for_role, doctor, block, make_appointment):
    today = timezone.localdate()
    upcoming_monday = today + timedelta(days=7 - today.weekday())
    make_appointment(doctor=doctor, date=upcoming_monday, start_time=time(11))

    response = client_for_role(Role.ADMIN).post(
        reverse("doctors:schedule_update", args=[block.pk]),
        {
            "weekday": Weekday.MONDAY,
            "start_time": "09:00",
            "end_time": "10:00",
            "slot_minutes": 15,
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    assert "Reschedule or cancel them first" in str(response.context["form"].non_field_errors())
