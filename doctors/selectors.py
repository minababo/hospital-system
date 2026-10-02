from datetime import datetime, timedelta

from django.db.models import Count, Q

from accounts.models import Role, User
from doctors.models import Department, Doctor, Weekday


def department_list(search=None, is_active=None):
    departments = Department.objects.annotate(doctor_count=Count("doctors"))
    if search:
        departments = departments.filter(name__icontains=search)
    if is_active is not None:
        departments = departments.filter(is_active=is_active)
    return departments.order_by("name")


def active_departments():
    return Department.objects.filter(is_active=True).order_by("name")


def doctor_list(search=None, department=None, is_active=None):
    doctors = Doctor.objects.select_related("user", "department")
    if search:
        doctors = doctors.filter(
            Q(user__first_name__icontains=search)
            | Q(user__last_name__icontains=search)
            | Q(specialization__icontains=search)
            | Q(registration_number__icontains=search)
        )
    if department:
        doctors = doctors.filter(department=department)
    if is_active is not None:
        doctors = doctors.filter(user__is_active=is_active)
    return doctors


def active_doctors(department=None):
    """Doctors who can be booked: active account in an active department."""
    doctors = Doctor.objects.select_related("user", "department").filter(
        user__is_active=True, department__is_active=True
    )
    if department:
        doctors = doctors.filter(department=department)
    return doctors


def doctor_weekly_schedule(doctor):
    """All blocks (active and inactive) grouped by day: [("Monday", [blocks]), ...]."""
    blocks = list(doctor.schedules.all())
    return [
        (label, [block for block in blocks if block.weekday == value])
        for value, label in Weekday.choices
    ]


def slots_for_date(doctor, date):
    """Start times of every slot the doctor works on `date`, sorted.

    Built from the doctor's active blocks for that weekday. A final slot that would
    end after the block's end_time is dropped (09:00-09:50 at 20 minutes gives 09:00
    and 09:20). Returns [] if the doctor's account is inactive.

    This ignores bookings: the appointments module removes already-booked slots.
    """
    if not doctor.is_active:
        return []

    slots = set()
    for block in doctor.schedules.filter(weekday=date.weekday(), is_active=True):
        step = timedelta(minutes=block.slot_minutes)
        current = datetime.combine(date, block.start_time)
        end = datetime.combine(date, block.end_time)
        while current + step <= end:
            slots.add(current.time())
            current += step
    return sorted(slots)


def doctor_users_without_profile():
    """Active DOCTOR users whose profile hasn't been created yet."""
    return User.objects.filter(
        role=Role.DOCTOR, is_active=True, doctor_profile__isnull=True
    ).order_by("last_name", "first_name", "username")
