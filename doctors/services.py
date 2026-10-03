from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import services as account_services
from accounts.models import Role
from doctors.models import Department, Doctor, DoctorSchedule

# Audit logging of these actions will be added in these services (audit app).
# Every write calls full_clean() so model validation runs even outside forms.


@transaction.atomic
def create_department(*, acting_user, **fields):
    department = Department(**fields)
    department.full_clean()
    department.save()
    return department


@transaction.atomic
def update_department(department, *, acting_user, **fields):
    for name, value in fields.items():
        setattr(department, name, value)
    department.full_clean()
    department.save()
    return department


@transaction.atomic
def set_department_active(department, active, *, acting_user):
    department.is_active = active
    department.save(update_fields=["is_active", "updated_at"])
    return department


@transaction.atomic
def create_doctor(*, user_data, profile_data, acting_user):
    """Create the DOCTOR user and their profile together. If the profile is invalid,
    the ValidationError rolls back the whole transaction, so no user is left behind."""
    user = account_services.create_user(
        **user_data, role=Role.DOCTOR, acting_user=acting_user, allow_doctor=True
    )
    return _save_new_profile(user, profile_data)


@transaction.atomic
def create_doctor_profile(*, user, profile_data, acting_user):
    """Profile for an existing DOCTOR user (e.g. one created before this module)."""
    return _save_new_profile(user, profile_data)


def _save_new_profile(user, profile_data):
    doctor = Doctor(user=user, **profile_data)
    doctor.full_clean()
    doctor.save()
    return doctor


@transaction.atomic
def update_doctor(doctor, *, user_fields, profile_fields, acting_user):
    account_services.update_user(doctor.user, acting_user=acting_user, **user_fields)
    for name, value in profile_fields.items():
        setattr(doctor, name, value)
    doctor.full_clean()
    doctor.save()
    return doctor


@transaction.atomic
def create_schedule(*, doctor, acting_user, **fields):
    schedule = DoctorSchedule(doctor=doctor, **fields)
    schedule.full_clean()
    schedule.save()
    return schedule


@transaction.atomic
def update_schedule(schedule, *, acting_user, today=None, **fields):
    # Read the saved weekday: a ModelForm may already have changed it in memory, and
    # appointments on the old day must be checked too if the block moves.
    old_weekday = DoctorSchedule.objects.values_list("weekday", flat=True).get(pk=schedule.pk)
    for name, value in fields.items():
        setattr(schedule, name, value)
    schedule.full_clean()
    schedule.save()
    _check_upcoming_appointments_fit(schedule.doctor, [old_weekday, schedule.weekday], today=today)
    return schedule


@transaction.atomic
def delete_schedule(schedule, *, acting_user, today=None):
    weekday = schedule.weekday
    schedule.delete()
    _check_upcoming_appointments_fit(schedule.doctor, [weekday], today=today)


def _check_upcoming_appointments_fit(doctor, weekdays, *, today=None):
    """Raise if a schedule change would leave upcoming appointments outside working hours.

    Called after the change inside the same transaction, so raising rolls the change back.
    Uses the reverse relation doctor.appointments instead of importing the appointments
    app; CANCELLED is the only status that frees a slot.
    """
    today = today or timezone.localdate()
    count = 0
    for weekday in set(weekdays):
        blocks = list(doctor.schedules.filter(weekday=weekday, is_active=True))
        upcoming = doctor.appointments.filter(
            date__gte=today, date__iso_week_day=weekday + 1
        ).exclude(status="CANCELLED")
        count += sum(
            1
            for appointment in upcoming
            if not any(b.start_time <= appointment.start_time < b.end_time for b in blocks)
        )
    if count:
        raise ValidationError(
            f"{count} upcoming appointment(s) fall outside the new schedule. "
            "Reschedule or cancel them first."
        )
