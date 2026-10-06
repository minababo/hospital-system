import calendar
import re
from collections import Counter
from dataclasses import dataclass
from datetime import date as date_type

from django.contrib.auth import get_user_model
from django.db.models import Count, Q

from appointments.models import Appointment
from appointments.models import Status as AppointmentStatus
from common.validators import normalize_nic, normalize_phone
from staff.models import (
    ACTIVE_LEAVE_STATUSES,
    Attendance,
    AttendanceStatus,
    Employee,
    EmployeeStatus,
    LeaveRequest,
    LeaveStatus,
    LeaveType,
)

EMPLOYEE_NUMBER = re.compile(r"(?:EMP-?)?(\d{1,18})", re.IGNORECASE)


# --- Employees ------------------------------------------------------------------------


def employee_list(*, q=None, department=None, category=None, status=None):
    employees = Employee.objects.select_related("department", "user")
    if department:
        employees = employees.filter(department=department)
    if category:
        employees = employees.filter(category=category)
    if status:
        employees = employees.filter(status=status)
    q = (q or "").strip()
    if q:
        matches = Q(nic__iexact=normalize_nic(q)) | Q(phone=normalize_phone(q))
        number = EMPLOYEE_NUMBER.fullmatch(q)
        if number:
            matches |= Q(pk=int(number.group(1)))
        name_match = Q()
        for term in q.split():
            name_match &= Q(first_name__icontains=term) | Q(last_name__icontains=term)
        employees = employees.filter(matches | name_match)
    return employees.order_by("first_name", "last_name")


def employed_on(day):
    """Employees working at the hospital on `day`: joined by then and not yet left."""
    return Employee.objects.filter(date_joined__lte=day).filter(
        Q(end_date__isnull=True) | Q(end_date__gte=day)
    )


def users_without_employee():
    """Active logins not yet linked to an employee record (for the link dropdown)."""
    return (
        get_user_model()
        .objects.filter(is_active=True, employee_profile__isnull=True)
        .order_by("first_name", "last_name", "username")
    )


# --- Leave ----------------------------------------------------------------------------


def overlaps(start, end):
    """Q for leave that shares at least one day with [start, end] (both inclusive):
    it starts on or before our last day AND ends on or after our first day."""
    return Q(start_date__lte=end, end_date__gte=start)


def overlapping_leave(employee, start, end, exclude=None):
    """Pending or approved leave of this employee that shares a day with the range."""
    leave = LeaveRequest.objects.filter(
        overlaps(start, end), employee=employee, status__in=ACTIVE_LEAVE_STATUSES
    )
    if exclude is not None:
        leave = leave.exclude(pk=exclude.pk)
    return leave.order_by("start_date")


def approved_leave_on(employee, day):
    return LeaveRequest.objects.filter(
        employee=employee, status=LeaveStatus.APPROVED, start_date__lte=day, end_date__gte=day
    ).first()


def leave_list(*, status=None, employee=None, date_from=None, date_to=None):
    leave = LeaveRequest.objects.select_related("employee", "employee__department")
    if status:
        leave = leave.filter(status=status)
    if employee:
        leave = leave.filter(employee=employee)
    if date_from:
        leave = leave.filter(end_date__gte=date_from)
    if date_to:
        leave = leave.filter(start_date__lte=date_to)
    return leave.order_by("start_date", "id")


def my_leave(user):
    return LeaveRequest.objects.filter(employee__user=user).select_related("decided_by")


def booked_appointments_during(employee, start, end):
    """Upcoming (booked or checked-in) appointments of a doctor-employee in the range.
    Empty for anyone without a doctor profile."""
    user = employee.user
    doctor = getattr(user, "doctor_profile", None) if user else None
    if doctor is None:
        return Appointment.objects.none()
    return (
        Appointment.objects.filter(
            doctor=doctor,
            date__range=(start, end),
            status__in=(AppointmentStatus.BOOKED, AppointmentStatus.CHECKED_IN),
        )
        .select_related("patient")
        .order_by("date", "start_time")
    )


# --- Attendance -------------------------------------------------------------------------


@dataclass
class SheetRow:
    employee: Employee
    attendance: Attendance | None
    leave: LeaveRequest | None


def attendance_sheet(day):
    """One row per employee working that day, with any saved attendance and any
    approved leave covering the day. Three queries in total."""
    employees = (
        employed_on(day)
        .select_related("department")
        .order_by("department__name", "first_name", "last_name")
    )
    attendance = {a.employee_id: a for a in Attendance.objects.filter(date=day)}
    leave = {
        leave.employee_id: leave
        for leave in LeaveRequest.objects.filter(
            status=LeaveStatus.APPROVED, start_date__lte=day, end_date__gte=day
        )
    }
    return [
        SheetRow(employee=e, attendance=attendance.get(e.pk), leave=leave.get(e.pk))
        for e in employees
    ]


def recent_attendance(employee, limit=14):
    return employee.attendance.order_by("-date")[:limit]


# --- Reports (used by the reports task) ---------------------------------------------


def headcount(*, by="department"):
    """Active employees per department name or per category label."""
    active = Employee.objects.filter(status=EmployeeStatus.ACTIVE)
    if by == "category":
        labels = dict(Employee._meta.get_field("category").choices)
        rows = active.values("category").annotate(total=Count("id")).order_by("category")
        return [(labels[row["category"]], row["total"]) for row in rows]
    rows = (
        active.values("department__name").annotate(total=Count("id")).order_by("department__name")
    )
    return [(row["department__name"], row["total"]) for row in rows]


def _clipped_days(start, end, range_start, range_end):
    """Days of [start, end] that fall inside [range_start, range_end] (inclusive)."""
    first, last = max(start, range_start), min(end, range_end)
    return (last - first).days + 1 if first <= last else 0


@dataclass
class MonthlyRow:
    employee: Employee
    present: int = 0
    half_day: int = 0
    absent: int = 0
    leave_days: int = 0


def monthly_attendance_summary(year, month):
    """Per employee who worked any part of the month: attendance counts by status and
    approved leave days, with leave spanning month boundaries clipped to the month."""
    first = date_type(year, month, 1)
    last = date_type(year, month, calendar.monthrange(year, month)[1])
    employees = (
        Employee.objects.filter(date_joined__lte=last)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=first))
        .select_related("department")
        .order_by("first_name", "last_name")
    )
    rows = {e.pk: MonthlyRow(employee=e) for e in employees}
    counts = (
        Attendance.objects.filter(date__range=(first, last), employee__in=rows)
        .values("employee", "status")
        .annotate(total=Count("id"))
    )
    field = {
        AttendanceStatus.PRESENT: "present",
        AttendanceStatus.HALF_DAY: "half_day",
        AttendanceStatus.ABSENT: "absent",
    }
    for row in counts:
        setattr(rows[row["employee"]], field[row["status"]], row["total"])
    approved = LeaveRequest.objects.filter(
        overlaps(first, last), status=LeaveStatus.APPROVED, employee__in=rows
    )
    for leave in approved:
        rows[leave.employee_id].leave_days += _clipped_days(
            leave.start_date, leave.end_date, first, last
        )
    return list(rows.values())


def leave_taken_between(start, end):
    """Approved leave days per leave type within [start, end], clipped to the range."""
    totals = Counter({value: 0 for value in LeaveType.values})
    approved = LeaveRequest.objects.filter(overlaps(start, end), status=LeaveStatus.APPROVED)
    for leave in approved:
        totals[leave.leave_type] += _clipped_days(leave.start_date, leave.end_date, start, end)
    return dict(totals)


def leave_taken_by_type(year):
    """Approved leave days per leave type within the year (clipped to the year)."""
    return leave_taken_between(date_type(year, 1, 1), date_type(year, 12, 31))


def leave_by_employee(employee):
    return employee.leave_requests.select_related("decided_by").order_by("-start_date")


def pending_leave_count():
    return LeaveRequest.objects.filter(status=LeaveStatus.PENDING).count()
