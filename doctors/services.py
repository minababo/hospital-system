from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import services as account_services
from accounts.models import Role
from audit.services import Action, created_changes, log_action, saved_snapshot, updated_changes
from doctors.models import Department, Doctor, DoctorSchedule

# Every write calls full_clean() so model validation runs even outside forms, and records
# one audit entry as its last step (inside the same transaction).


@transaction.atomic
def create_department(*, acting_user, **fields):
    department = Department(**fields)
    department.full_clean()
    department.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="doctors.department.created",
        obj=department,
        changes=created_changes(department),
    )
    return department


@transaction.atomic
def update_department(department, *, acting_user, **fields):
    before = saved_snapshot(department)
    for name, value in fields.items():
        setattr(department, name, value)
    department.full_clean()
    department.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="doctors.department.updated",
        obj=department,
        changes=updated_changes(department, before),
    )
    return department


@transaction.atomic
def set_department_active(department, active, *, acting_user):
    was_active = Department.objects.values_list("is_active", flat=True).get(pk=department.pk)
    department.is_active = active
    department.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="doctors.department.activated" if active else "doctors.department.deactivated",
        obj=department,
        changes={"is_active": [was_active, active]},
    )
    return department


@transaction.atomic
def create_doctor(*, user_data, profile_data, acting_user):
    """Create the DOCTOR user and their profile together. If the profile is invalid,
    the ValidationError rolls back the whole transaction, so no user is left behind."""
    user = account_services.create_user(
        **user_data, role=Role.DOCTOR, acting_user=acting_user, allow_doctor=True
    )
    return _save_new_profile(user, profile_data, acting_user=acting_user)


@transaction.atomic
def create_doctor_profile(*, user, profile_data, acting_user):
    """Profile for an existing DOCTOR user (e.g. one created before this module)."""
    return _save_new_profile(user, profile_data, acting_user=acting_user)


def _save_new_profile(user, profile_data, *, acting_user):
    doctor = Doctor(user=user, **profile_data)
    doctor.full_clean()
    doctor.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="doctors.doctor.created",
        obj=doctor,
        changes=created_changes(doctor),
    )
    return doctor


@transaction.atomic
def update_doctor(doctor, *, user_fields, profile_fields, acting_user):
    # update_user records its own entry for the account fields.
    account_services.update_user(doctor.user, acting_user=acting_user, **user_fields)
    before = saved_snapshot(doctor)
    for name, value in profile_fields.items():
        setattr(doctor, name, value)
    doctor.full_clean()
    doctor.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="doctors.doctor.updated",
        obj=doctor,
        changes=updated_changes(doctor, before),
    )
    return doctor


@transaction.atomic
def create_schedule(*, doctor, acting_user, **fields):
    schedule = DoctorSchedule(doctor=doctor, **fields)
    schedule.full_clean()
    schedule.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="doctors.schedule.created",
        obj=schedule,
        changes=created_changes(schedule),
    )
    return schedule


@transaction.atomic
def update_schedule(schedule, *, acting_user, today=None, **fields):
    # Read the saved weekday: a ModelForm may already have changed it in memory, and
    # appointments on the old day must be checked too if the block moves.
    old_weekday = DoctorSchedule.objects.values_list("weekday", flat=True).get(pk=schedule.pk)
    before = saved_snapshot(schedule)
    for name, value in fields.items():
        setattr(schedule, name, value)
    schedule.full_clean()
    schedule.save()
    _check_upcoming_appointments_fit(schedule.doctor, [old_weekday, schedule.weekday], today=today)
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="doctors.schedule.updated",
        obj=schedule,
        changes=updated_changes(schedule, before),
    )
    return schedule


@transaction.atomic
def delete_schedule(schedule, *, acting_user, today=None):
    weekday = schedule.weekday
    pk, description = schedule.pk, str(schedule)
    removed = {name: [value, None] for name, value in saved_snapshot(schedule).items()}
    schedule.delete()
    _check_upcoming_appointments_fit(schedule.doctor, [weekday], today=today)
    schedule.pk = pk  # delete() cleared it; the audit entry still names the row
    log_action(
        actor=acting_user,
        action=Action.DELETE,
        event="doctors.schedule.deleted",
        obj=schedule,
        changes=removed,
        message=f"Deleted {description}",
    )


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
