from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from appointments.models import SLOT_OCCUPYING_STATUSES, Appointment, Status
from appointments.selectors import available_slots, last_bookable_date, local_now

# Audit logging of these actions will be added in these services (audit app).
# Every function takes now=None so tests can fix the clock instead of using real time.

SLOT_TAKEN_MESSAGE = "This time slot was just booked by someone else. Please choose another slot."


# --- Booking and rescheduling -----------------------------------------------


@transaction.atomic
def book_appointment(*, patient, doctor, date, start_time, reason, acting_user, now=None):
    start, end = _check_slot(doctor, date, start_time, now=now)
    _check_patient_free(patient, doctor, date, start)
    appointment = Appointment(
        patient=patient,
        doctor=doctor,
        date=date,
        start_time=start,
        end_time=end,
        reason=(reason or "").strip(),
        consultation_fee=doctor.consultation_fee,
        created_by=acting_user,
    )
    _validate_and_save(appointment)
    return appointment


@transaction.atomic
def reschedule_appointment(appointment, *, date, start_time, acting_user, now=None):
    if appointment.status != Status.BOOKED:
        raise ValidationError("Only booked appointments can be rescheduled.")
    start, end = _check_slot(
        appointment.doctor, date, start_time, now=now, exclude_appointment=appointment
    )
    _check_patient_free(appointment.patient, appointment.doctor, date, start, exclude=appointment)
    appointment.date = date
    appointment.start_time = start
    appointment.end_time = end
    appointment.reschedule_count += 1
    _validate_and_save(appointment)
    return appointment


def _check_slot(doctor, date, start_time, *, now=None, exclude_appointment=None):
    """Return the (start, end) slot, or raise a ValidationError saying why it can't be booked."""
    if not doctor.user.is_active:
        raise ValidationError("This doctor is not active and can't be booked.")
    if not doctor.department.is_active:
        raise ValidationError("This doctor's department is inactive.")
    today, current_time = local_now(now)
    if date < today:
        raise ValidationError("Appointments can't be booked for a past date.")
    if date > last_bookable_date(today):
        raise ValidationError(
            f"Appointments can only be booked up to {settings.APPOINTMENT_BOOKING_WINDOW_DAYS} "
            "days ahead."
        )
    if date == today and start_time <= current_time:
        raise ValidationError("That time has already passed.")

    slots = available_slots(doctor, date, now=now, exclude_appointment=exclude_appointment)
    for start, end in slots:
        if start == start_time:
            return start, end
    raise ValidationError("That time is not an available slot for this doctor.")


def _check_patient_free(patient, doctor, date, start_time, exclude=None):
    existing = Appointment.objects.filter(
        patient=patient, date=date, status__in=SLOT_OCCUPYING_STATUSES
    )
    if exclude is not None:
        existing = existing.exclude(pk=exclude.pk)
    if existing.filter(doctor=doctor).exists():
        raise ValidationError("This patient already has an appointment with this doctor that day.")
    if existing.filter(start_time=start_time).exists():
        raise ValidationError("This patient already has another appointment at that time.")


def _validate_and_save(appointment):
    # The unique constraints are not re-checked here (validate_constraints=False): the
    # slot was checked above, and the database enforces uniqueness on save anyway.
    # A second check now would still leave a gap for two people booking at once.
    appointment.full_clean(validate_constraints=False)
    try:
        # Nested atomic() = savepoint. If two bookings race for the same slot, the loser's
        # INSERT/UPDATE fails here; rolling back to the savepoint keeps the outer
        # transaction usable (on PostgreSQL a failed statement otherwise breaks it).
        with transaction.atomic():
            appointment.save()
    except IntegrityError as error:
        raise ValidationError(SLOT_TAKEN_MESSAGE) from error


# --- Status changes ---------------------------------------------------------


def _transition(appointment, new_status):
    """Raise unless ALLOWED_TRANSITIONS permits this change. Callers set the new status
    only after their other checks pass, so a rejected change leaves the object untouched."""
    if not appointment.can_transition_to(new_status):
        raise ValidationError(
            f"A {appointment.get_status_display().lower()} appointment can't be changed to "
            f"{Status(new_status).label.lower()}."
        )


@transaction.atomic
def cancel_appointment(appointment, *, reason, acting_user, now=None):
    reason = (reason or "").strip()
    _transition(appointment, Status.CANCELLED)
    if not reason:
        raise ValidationError("Please give a reason for cancelling.")
    appointment.status = Status.CANCELLED
    appointment.cancel_reason = reason[:255]
    appointment.cancelled_at = now or timezone.now()
    appointment.cancelled_by = acting_user
    appointment.save()
    return appointment


@transaction.atomic
def check_in_appointment(appointment, *, acting_user, now=None):
    _transition(appointment, Status.CHECKED_IN)
    today, _ = local_now(now)
    if appointment.date != today:
        raise ValidationError("Patients can only be checked in on the day of the appointment.")
    appointment.status = Status.CHECKED_IN
    appointment.checked_in_at = now or timezone.now()
    appointment.checked_in_by = acting_user
    appointment.save()
    return appointment


@transaction.atomic
def complete_appointment(appointment, *, acting_user, now=None):
    if appointment.doctor.user_id != acting_user.pk:
        raise PermissionDenied("Only the appointment's own doctor can complete it.")
    _transition(appointment, Status.COMPLETED)
    # A visit is only complete once the doctor has finalized its medical record.
    # Reverse one-to-one access (no import of the records app); getattr returns None
    # when no record exists, because the "does not exist" error is an AttributeError.
    record = getattr(appointment, "medical_record", None)
    if record is None or record.status != "FINALIZED":
        raise ValidationError("Finalize the consultation record before completing the appointment.")
    appointment.status = Status.COMPLETED
    appointment.completed_at = now or timezone.now()
    appointment.completed_by = acting_user
    appointment.save()
    return appointment


@transaction.atomic
def mark_no_show(appointment, *, acting_user, now=None):
    _transition(appointment, Status.NO_SHOW)
    if not appointment.is_past(now):
        raise ValidationError("An appointment can only be marked as a no-show after it starts.")
    appointment.status = Status.NO_SHOW
    appointment.save()
    return appointment
