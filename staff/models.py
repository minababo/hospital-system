from datetime import timedelta

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from common.validators import (
    normalize_nic,
    normalize_phone,
    sri_lanka_phone_validator,
    validate_sri_lanka_nic,
)


class StaffCategory(models.TextChoices):
    MEDICAL = "MEDICAL", "Medical"
    NURSING = "NURSING", "Nursing"
    ALLIED_HEALTH = "ALLIED_HEALTH", "Allied health"
    ADMINISTRATIVE = "ADMINISTRATIVE", "Administrative"
    SUPPORT = "SUPPORT", "Support"


class EmploymentType(models.TextChoices):
    PERMANENT = "PERMANENT", "Permanent"
    CONTRACT = "CONTRACT", "Contract"
    TEMPORARY = "TEMPORARY", "Temporary"


class EmployeeStatus(models.TextChoices):
    ACTIVE = "ACTIVE", "Active"
    RESIGNED = "RESIGNED", "Resigned"
    TERMINATED = "TERMINATED", "Terminated"


class Employee(models.Model):
    """A person employed by the hospital. Separate from the login account: porters and
    cleaners have no login, and a login (user) can be linked to at most one employee."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="employee_profile",
        verbose_name="Login account",
        help_text="Optional. Lets this person request leave themselves.",
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    # NULL (not "") when missing, so the unique NIC index ignores employees without one.
    nic = models.CharField("NIC", max_length=12, null=True, blank=True)  # noqa: DJ001
    phone = models.CharField(max_length=15)
    email = models.EmailField(blank=True)
    address = models.TextField(blank=True)
    designation = models.CharField(max_length=100, help_text="e.g. Staff Nurse, Porter")
    category = models.CharField(max_length=20, choices=StaffCategory.choices)
    employment_type = models.CharField(max_length=20, choices=EmploymentType.choices)
    department = models.ForeignKey(
        "doctors.Department", on_delete=models.PROTECT, related_name="employees"
    )
    date_joined = models.DateField()
    status = models.CharField(
        max_length=20, choices=EmployeeStatus.choices, default=EmployeeStatus.ACTIVE
    )
    end_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["first_name", "last_name"]
        constraints = [
            models.UniqueConstraint(
                Lower("nic"),
                condition=models.Q(nic__isnull=False),
                name="staff_employee_nic_ci_unique",
                violation_error_message="An employee with this NIC already exists.",
            ),
            # Active employees have no end date; ended ones always have one.
            models.CheckConstraint(
                condition=models.Q(status="ACTIVE", end_date__isnull=True)
                | (~models.Q(status="ACTIVE") & models.Q(end_date__isnull=False)),
                name="staff_employee_end_date_matches_status",
            ),
            models.CheckConstraint(
                condition=models.Q(end_date__isnull=True)
                | models.Q(end_date__gte=models.F("date_joined")),
                name="staff_employee_end_after_join",
                violation_error_message="The end date can't be before the joining date.",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.number})"

    @property
    def number(self):
        return f"EMP-{self.pk:06d}" if self.pk else None

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def is_active(self):
        return self.status == EmployeeStatus.ACTIVE

    def clean(self):
        # Normalised here (not with field validators) so "077 123 4567" is accepted.
        self.first_name = (self.first_name or "").strip()
        self.last_name = (self.last_name or "").strip()
        self.phone = normalize_phone(self.phone)
        self.nic = normalize_nic(self.nic) or None
        errors = {}
        if self.phone:
            try:
                sri_lanka_phone_validator(self.phone)
            except ValidationError as error:
                errors["phone"] = error.messages
        if self.nic:
            try:
                validate_sri_lanka_nic(self.nic)
            except ValidationError as error:
                errors["nic"] = error.messages
        if self.date_joined and self.date_joined > timezone.localdate():
            errors["date_joined"] = ["The joining date can't be in the future."]
        if errors:
            raise ValidationError(errors)


class AttendanceStatus(models.TextChoices):
    PRESENT = "PRESENT", "Present"
    HALF_DAY = "HALF_DAY", "Half day"
    ABSENT = "ABSENT", "Absent"


class Attendance(models.Model):
    """One employee's attendance on one day."""

    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="attendance")
    date = models.DateField()
    status = models.CharField(max_length=10, choices=AttendanceStatus.choices)
    check_in = models.TimeField(null=True, blank=True)
    check_out = models.TimeField(null=True, blank=True)
    notes = models.CharField(max_length=255, blank=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-date", "employee__first_name"]
        verbose_name_plural = "attendance"
        constraints = [
            models.UniqueConstraint(
                fields=["employee", "date"],
                name="staff_attendance_one_per_day",
                violation_error_message="Attendance for this employee and day already exists.",
            ),
            models.CheckConstraint(
                condition=models.Q(check_in__isnull=True)
                | models.Q(check_out__isnull=True)
                | models.Q(check_out__gt=models.F("check_in")),
                name="staff_attendance_out_after_in",
                violation_error_message="Check-out must be after check-in.",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="ABSENT")
                | models.Q(check_in__isnull=True, check_out__isnull=True),
                name="staff_attendance_absent_has_no_times",
                violation_error_message="An absent employee has no check-in or check-out time.",
            ),
        ]

    def __str__(self):
        return f"{self.employee.full_name} on {self.date:%Y-%m-%d}: {self.get_status_display()}"


class LeaveType(models.TextChoices):
    ANNUAL = "ANNUAL", "Annual"
    CASUAL = "CASUAL", "Casual"
    SICK = "SICK", "Sick"
    MATERNITY = "MATERNITY", "Maternity"
    NO_PAY = "NO_PAY", "No pay"
    OTHER = "OTHER", "Other"


class LeaveStatus(models.TextChoices):
    PENDING = "PENDING", "Pending"
    APPROVED = "APPROVED", "Approved"
    REJECTED = "REJECTED", "Rejected"
    CANCELLED = "CANCELLED", "Cancelled"


# Leave that blocks the same days for other leave (pending requests hold their dates).
ACTIVE_LEAVE_STATUSES = (LeaveStatus.PENDING, LeaveStatus.APPROVED)


class LeaveRequest(models.Model):
    employee = models.ForeignKey(Employee, on_delete=models.PROTECT, related_name="leave_requests")
    leave_type = models.CharField(max_length=20, choices=LeaveType.choices)
    start_date = models.DateField()
    end_date = models.DateField()
    reason = models.TextField()
    status = models.CharField(
        max_length=20, choices=LeaveStatus.choices, default=LeaveStatus.PENDING
    )
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    requested_at = models.DateTimeField(default=timezone.now)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=255, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-start_date", "-id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="staff_leave_end_after_start",
                violation_error_message="The last day can't be before the first day.",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="REJECTED") | ~models.Q(decision_note=""),
                name="staff_leave_rejection_needs_note",
                violation_error_message="Give a reason for rejecting the leave.",
            ),
        ]

    def __str__(self):
        return f"{self.get_leave_type_display()} leave for {self.employee.full_name}"

    @property
    def days(self):
        """Calendar days, counting both the first and the last day."""
        return (self.end_date - self.start_date).days + 1

    def dates(self):
        return [self.start_date + timedelta(days=i) for i in range(self.days)]
