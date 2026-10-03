from datetime import timedelta

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from appointments.models import SLOT_OCCUPYING_STATUSES, Appointment, Status
from doctors.selectors import slot_ranges_for_date
from patients.selectors import HistoryEvent, search_patients


def local_now(now=None):
    """(today, current time) in the hospital's time zone. `now` lets tests fix the clock."""
    current = timezone.localtime(now or timezone.now())
    return current.date(), current.time()


def last_bookable_date(today):
    return today + timedelta(days=settings.APPOINTMENT_BOOKING_WINDOW_DAYS)


def booked_start_times(doctor, date, exclude_appointment=None):
    appointments = Appointment.objects.filter(
        doctor=doctor, date=date, status__in=SLOT_OCCUPYING_STATUSES
    )
    if exclude_appointment is not None:
        appointments = appointments.exclude(pk=exclude_appointment.pk)
    return set(appointments.values_list("start_time", flat=True))


def available_slots(doctor, date, *, now=None, exclude_appointment=None):
    """Free (start, end) slots for the doctor on `date`.

    Starts from the doctor's schedule, then removes booked slots (except
    exclude_appointment's own, for rescheduling) and, for today, slots that have
    already started. Empty for past dates, dates beyond the booking window, and
    doctors who can't be booked.
    """
    today, current_time = local_now(now)
    if date < today or date > last_bookable_date(today):
        return []
    if not doctor.department.is_active:
        return []

    booked = booked_start_times(doctor, date, exclude_appointment)
    slots = [
        (start, end) for start, end in slot_ranges_for_date(doctor, date) if start not in booked
    ]
    if date == today:
        slots = [(start, end) for start, end in slots if start > current_time]
    return slots


def visible_appointments(user):
    """Appointments this user may see. Doctors only ever see their own, so looking up
    another doctor's appointment through this queryset gives a 404."""
    appointments = Appointment.objects.select_related(
        "patient", "doctor__user", "doctor__department"
    )
    if user.role == Role.DOCTOR:
        return appointments.filter(doctor__user=user)
    return appointments


def appointment_list(*, user, date=None, doctor=None, status=None, query=None):
    appointments = visible_appointments(user)
    if date:
        appointments = appointments.filter(date=date)
    if doctor:
        appointments = appointments.filter(doctor=doctor)
    if status:
        appointments = appointments.filter(status=status)
    if query:
        appointments = appointments.filter(patient__in=search_patients(query))
    return appointments.order_by("date", "start_time")


def week_calendar(*, user, doctor=None, week_start):
    """Seven days (Monday first) with their appointments, plus prev/next week dates."""
    monday = week_start - timedelta(days=week_start.weekday())
    days = [monday + timedelta(days=offset) for offset in range(7)]
    appointments = appointment_list(user=user, doctor=doctor).filter(
        date__range=(days[0], days[-1])
    )
    by_day = {day: [] for day in days}
    for appointment in appointments:
        by_day[appointment.date].append(appointment)
    return {
        "days": [{"date": day, "appointments": by_day[day]} for day in days],
        "week_start": monday,
        "prev_week": monday - timedelta(days=7),
        "next_week": monday + timedelta(days=7),
    }


def todays_appointments(*, user, now=None):
    today, _ = local_now(now)
    return appointment_list(user=user, date=today)


def patient_appointments(patient, *, now=None):
    """{"upcoming": today onwards, soonest first; "past": earlier days, latest first}."""
    today, _ = local_now(now)
    appointments = patient.appointments.select_related("doctor__user").order_by(
        "date", "start_time"
    )
    return {
        "upcoming": [a for a in appointments if a.date >= today],
        "past": [a for a in reversed(appointments) if a.date < today],
    }


# --- Patient history provider ------------------------------------------------

_HISTORY_TITLES = {
    Status.BOOKED: "Appointment booked",
    Status.CHECKED_IN: "Checked in for appointment",
    Status.COMPLETED: "Appointment completed",
    Status.CANCELLED: "Appointment cancelled",
    Status.NO_SHOW: "Missed appointment (no-show)",
}


def appointment_history_events(patient):
    """Registered in patients.selectors.PROVIDERS by AppointmentsConfig.ready()."""
    events = []
    for appointment in patient.appointments.select_related("doctor__user"):
        timestamp = {
            Status.COMPLETED: appointment.completed_at,
            Status.CANCELLED: appointment.cancelled_at,
        }.get(appointment.status) or appointment.start_datetime
        detail = f"{appointment.date:%d %b %Y} {appointment.start_time:%H:%M}. {appointment.reason}"
        if appointment.status == Status.CANCELLED and appointment.cancel_reason:
            detail += f" Cancelled: {appointment.cancel_reason}"
        events.append(
            HistoryEvent(
                timestamp=timestamp,
                kind="Appointment",
                title=f"{_HISTORY_TITLES[appointment.status]} with {appointment.doctor}",
                detail=detail,
                url=reverse("appointments:appointment_detail", args=[appointment.pk]),
            )
        )
    return events
