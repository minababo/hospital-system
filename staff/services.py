from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Role
from audit.services import (
    Action,
    created_changes,
    log_action,
    saved_snapshot,
    snapshot,
    updated_changes,
)
from staff.models import (
    Attendance,
    Employee,
    EmployeeStatus,
    LeaveRequest,
    LeaveStatus,
)
from staff.selectors import (
    booked_appointments_during,
    employed_on,
    overlapping_leave,
)

# Each public write records one audit entry as its last step (same transaction);
# the attendance sheet records one entry per changed row.
#
# One source of truth per day: a day is either covered by approved leave or has an
# attendance record, never both. The sheet refuses employees on approved leave, and
# approving leave refuses days that already have attendance.

MAX_FUTURE_END_DAYS = 30


def _today(now):
    return timezone.localdate(now or timezone.now())


def _save_or_raise(instance, message):
    """Save in a savepoint and turn a unique-constraint race into a friendly error."""
    try:
        with transaction.atomic():
            instance.save()
    except IntegrityError as error:
        raise ValidationError(message) from error


# --- Employees ------------------------------------------------------------------------


def _check_user_link(user, employee=None):
    if user is None:
        return
    linked = Employee.objects.filter(user=user)
    if employee is not None and employee.pk:
        linked = linked.exclude(pk=employee.pk)
    other = linked.first()
    if other:
        raise ValidationError({"user": [f"This login is already linked to {other}."]})


@transaction.atomic
def create_employee(*, acting_user, **fields):
    _check_user_link(fields.get("user"))
    employee = Employee(**fields)
    employee.full_clean(validate_unique=False)  # the login link is checked above
    _save_or_raise(employee, {"user": ["This login was just linked to another employee."]})
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="staff.employee.created",
        obj=employee,
        changes=created_changes(employee),
    )
    return employee


@transaction.atomic
def update_employee(employee, *, acting_user, **fields):
    _check_user_link(fields.get("user"), employee)
    before = saved_snapshot(employee)
    for name, value in fields.items():
        setattr(employee, name, value)
    employee.full_clean(validate_unique=False)
    _save_or_raise(employee, {"user": ["This login was just linked to another employee."]})
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="staff.employee.updated",
        obj=employee,
        changes=updated_changes(employee, before),
    )
    return employee


@transaction.atomic
def end_employment(employee, *, status, end_date, acting_user, now=None):
    """Record a resignation or termination. Pending leave is cancelled."""
    now = now or timezone.now()
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    if employee.status != EmployeeStatus.ACTIVE:
        raise ValidationError("This employee has already left.")
    if status not in (EmployeeStatus.RESIGNED, EmployeeStatus.TERMINATED):
        raise ValidationError({"status": ["Choose resigned or terminated."]})
    if end_date < employee.date_joined:
        raise ValidationError({"end_date": ["The last day can't be before the joining date."]})
    if end_date > _today(now) + timedelta(days=MAX_FUTURE_END_DAYS):
        raise ValidationError(
            {"end_date": [f"The last day can be at most {MAX_FUTURE_END_DAYS} days ahead."]}
        )
    later = employee.attendance.filter(date__gt=end_date).order_by("date").first()
    if later:
        raise ValidationError(
            {"end_date": [f"Attendance is recorded after this date ({later.date:%d %b %Y})."]}
        )

    old_status = employee.status
    employee.status = status
    employee.end_date = end_date
    employee.full_clean(validate_unique=False)
    employee.save()
    cancelled = employee.leave_requests.filter(status=LeaveStatus.PENDING).update(
        status=LeaveStatus.CANCELLED, cancelled_at=now
    )
    message = f"Last day {end_date:%Y-%m-%d}"
    if cancelled:
        message += f"; {cancelled} pending leave request(s) cancelled"
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="staff.employee.employment_ended",
        obj=employee,
        changes={"status": [old_status, status], "end_date": [None, end_date]},
        message=message,
    )
    return employee


# --- Attendance -------------------------------------------------------------------------


@transaction.atomic
def save_attendance_sheet(*, date, rows, acting_user, now=None):
    """Save a day's attendance. rows = [{"employee_id", "status", "check_in",
    "check_out", "notes"}, ...]. All or nothing: any problem saves no row.

    Rows with a blank status are skipped and any existing record for that employee is
    left as it is (the sheet never deletes attendance).
    """
    if date > _today(now):
        raise ValidationError("Attendance can't be recorded for a future date.")
    rows = [row for row in rows if row.get("status")]
    working = {e.pk: e for e in employed_on(date).filter(pk__in=[r["employee_id"] for r in rows])}
    on_leave = set(
        LeaveRequest.objects.filter(
            status=LeaveStatus.APPROVED,
            start_date__lte=date,
            end_date__gte=date,
            employee__in=working.values(),
        ).values_list("employee_id", flat=True)
    )
    existing = {
        a.employee_id: a
        for a in Attendance.objects.select_for_update().filter(date=date, employee__in=working)
    }
    sheet_fields = ["status", "check_in", "check_out", "notes"]
    before = {pk: snapshot(a, sheet_fields) for pk, a in existing.items()}

    errors = []
    to_save = []
    for row in rows:
        employee = working.get(row["employee_id"])
        if employee is None:
            errors.append("A selected employee wasn't working at the hospital on that date.")
            continue
        if employee.pk in on_leave:
            errors.append(f"{employee.full_name} is on approved leave that day.")
            continue
        record = existing.get(employee.pk) or Attendance(
            employee=employee, date=date, recorded_by=acting_user
        )
        record.status = row["status"]
        record.check_in = row.get("check_in")
        record.check_out = row.get("check_out")
        record.notes = (row.get("notes") or "").strip()[:255]
        try:
            record.full_clean(validate_unique=False)
        except ValidationError as error:
            errors.extend(f"{employee.full_name}: {message}" for message in error.messages)
            continue
        to_save.append(record)
    if errors:
        raise ValidationError(errors)
    for record in to_save:
        is_new = record.pk is None
        if not is_new:
            changes = updated_changes(record, before[record.employee_id], sheet_fields)
            if not changes:
                continue  # an unchanged row is neither saved again nor logged
        _save_or_raise(record, "Someone saved this sheet at the same time. Reload and try again.")
        # One entry per row that was created or changed.
        log_action(
            actor=acting_user,
            action=Action.CREATE if is_new else Action.UPDATE,
            event="staff.attendance.recorded" if is_new else "staff.attendance.updated",
            obj=record,
            changes=created_changes(record, sheet_fields) if is_new else changes,
            message=f"{record.employee.full_name}: {record.get_status_display()}",
        )
    return to_save


# --- Leave ------------------------------------------------------------------------------


def _check_dates(employee, start, end, *, exclude=None):
    if end < start:
        raise ValidationError({"end_date": ["The last day can't be before the first day."]})
    if start < employee.date_joined:
        raise ValidationError({"start_date": ["This is before the employee joined."]})
    clash = overlapping_leave(employee, start, end, exclude=exclude).first()
    if clash:
        raise ValidationError(
            f"This overlaps {clash.get_status_display().lower()} leave from "
            f"{clash.start_date:%d %b %Y} to {clash.end_date:%d %b %Y}."
        )


def _approval_checks(employee, start, end, *, exclude, confirm_doctor_conflicts):
    """What must hold before leave counts as approved."""
    approved_clash = (
        overlapping_leave(employee, start, end, exclude=exclude)
        .filter(status=LeaveStatus.APPROVED)
        .first()
    )
    if approved_clash:
        raise ValidationError(
            f"This overlaps approved leave from {approved_clash.start_date:%d %b %Y} "
            f"to {approved_clash.end_date:%d %b %Y}."
        )
    recorded = list(
        employee.attendance.filter(date__range=(start, end))
        .order_by("date")
        .values_list("date", flat=True)
    )
    if recorded:
        dates = ", ".join(f"{day:%d %b}" for day in recorded)
        raise ValidationError(f"Attendance is already recorded on {dates}. Correct that first.")
    appointments = booked_appointments_during(employee, start, end).count()
    if appointments and not confirm_doctor_conflicts:
        # code lets the view show the "approve anyway" checkbox.
        raise ValidationError(
            f"{employee.full_name} has {appointments} booked appointment(s) during this "
            "leave. They are not cancelled automatically. Tick the box to approve anyway.",
            code="doctor_conflicts",
        )


def _require_reason(reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": ["Give a reason for the leave."]})
    return reason


@transaction.atomic
def record_leave(
    *,
    employee,
    leave_type,
    start_date,
    end_date,
    reason,
    acting_user,
    confirm_doctor_conflicts=False,
    now=None,
):
    """HR records leave directly: it is approved at once, after the same checks as
    approving a request."""
    now = now or timezone.now()
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    if not employee.is_active:
        raise ValidationError("Leave can only be recorded for current employees.")
    reason = _require_reason(reason)
    _check_dates(employee, start_date, end_date)
    _approval_checks(
        employee,
        start_date,
        end_date,
        exclude=None,
        confirm_doctor_conflicts=confirm_doctor_conflicts,
    )
    leave = LeaveRequest(
        employee=employee,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        status=LeaveStatus.APPROVED,
        requested_by=acting_user,
        requested_at=now,
        decided_by=acting_user,
        decided_at=now,
    )
    leave.full_clean()
    leave.save()
    _log_leave(leave, Action.CREATE, "staff.leave.recorded", acting_user=acting_user)
    return leave


@transaction.atomic
def request_leave(*, user, leave_type, start_date, end_date, reason, now=None):
    """Self-service request by a logged-in employee; it waits for HR approval."""
    now = now or timezone.now()
    employee = getattr(user, "employee_profile", None)
    if employee is None or not employee.is_active:
        raise ValidationError("Your login isn't linked to a current employee record.")
    employee = Employee.objects.select_for_update().get(pk=employee.pk)
    if start_date < _today(now):
        raise ValidationError({"start_date": ["Leave can't start in the past."]})
    reason = _require_reason(reason)
    _check_dates(employee, start_date, end_date)
    leave = LeaveRequest(
        employee=employee,
        leave_type=leave_type,
        start_date=start_date,
        end_date=end_date,
        reason=reason,
        requested_by=user,
        requested_at=now,
    )
    leave.full_clean()
    leave.save()
    _log_leave(leave, Action.CREATE, "staff.leave.requested", acting_user=user)
    return leave


def _lock_leave(leave):
    return LeaveRequest.objects.select_for_update().get(pk=leave.pk)


def _log_leave(leave, action, event, *, acting_user, old_status=None, message=""):
    dates = (
        f"{leave.get_leave_type_display()} {leave.start_date:%Y-%m-%d} to {leave.end_date:%Y-%m-%d}"
    )
    log_action(
        actor=acting_user,
        action=action,
        event=event,
        obj=leave,
        changes={"status": [old_status, leave.status]},
        message=f"{leave.employee.full_name}: {dates}" + (f" ({message})" if message else ""),
    )


@transaction.atomic
def approve_leave(leave, *, note="", confirm_doctor_conflicts=False, acting_user, now=None):
    leave = _lock_leave(leave)
    if leave.status != LeaveStatus.PENDING:
        raise ValidationError("Only pending leave can be approved.")
    _approval_checks(
        leave.employee,
        leave.start_date,
        leave.end_date,
        exclude=leave,
        confirm_doctor_conflicts=confirm_doctor_conflicts,
    )
    leave.status = LeaveStatus.APPROVED
    leave.decided_by = acting_user
    leave.decided_at = now or timezone.now()
    leave.decision_note = (note or "").strip()[:255]
    leave.save()
    _log_leave(
        leave,
        Action.STATUS_CHANGE,
        "staff.leave.approved",
        acting_user=acting_user,
        old_status=LeaveStatus.PENDING,
        message=leave.decision_note,
    )
    return leave


@transaction.atomic
def reject_leave(leave, *, note, acting_user, now=None):
    leave = _lock_leave(leave)
    if leave.status != LeaveStatus.PENDING:
        raise ValidationError("Only pending leave can be rejected.")
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": ["Give a reason for rejecting the leave."]})
    leave.status = LeaveStatus.REJECTED
    leave.decided_by = acting_user
    leave.decided_at = now or timezone.now()
    leave.decision_note = note[:255]
    leave.save()
    _log_leave(
        leave,
        Action.STATUS_CHANGE,
        "staff.leave.rejected",
        acting_user=acting_user,
        old_status=LeaveStatus.PENDING,
        message=leave.decision_note,
    )
    return leave


@transaction.atomic
def cancel_leave(leave, *, acting_user, now=None):
    """The employee (through their login) or the admin can cancel leave that hasn't
    started yet."""
    now = now or timezone.now()
    leave = _lock_leave(leave)
    is_owner = leave.employee.user_id is not None and leave.employee.user_id == acting_user.pk
    if not (is_owner or acting_user.role == Role.ADMIN):
        raise PermissionDenied("Only the employee or the admin can cancel this leave.")
    if leave.status not in (LeaveStatus.PENDING, LeaveStatus.APPROVED):
        raise ValidationError("Only pending or approved leave can be cancelled.")
    if _today(now) >= leave.start_date:
        raise ValidationError("Leave can only be cancelled before it starts.")
    old_status = leave.status
    leave.status = LeaveStatus.CANCELLED
    leave.cancelled_at = now
    leave.save()
    _log_leave(
        leave,
        Action.STATUS_CHANGE,
        "staff.leave.cancelled",
        acting_user=acting_user,
        old_status=old_status,
    )
    return leave
