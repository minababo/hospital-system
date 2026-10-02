from decimal import Decimal

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from accounts.models import Role
from common.validators import normalize_phone, sri_lanka_phone_validator


class Department(models.Model):
    """A hospital department. Generic on purpose: staff are assigned to departments too."""

    name = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                name="doctors_department_name_ci_unique",
                violation_error_message="A department with this name already exists.",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()


class Doctor(models.Model):
    """Doctor profile. Every user with the DOCTOR role has exactly one."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="doctor_profile"
    )
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="doctors")
    specialization = models.CharField(max_length=100)
    registration_number = models.CharField("SLMC registration number", max_length=30)
    qualification = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=20, help_text="e.g. 0771234567 or +94771234567")
    consultation_fee = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["user__last_name", "user__first_name"]
        constraints = [
            models.UniqueConstraint(
                Lower("registration_number"),
                name="doctors_doctor_registration_number_ci_unique",
                violation_error_message="A doctor with this registration number already exists.",
            ),
            models.CheckConstraint(
                condition=models.Q(consultation_fee__gte=0),
                name="doctors_doctor_consultation_fee_non_negative",
                violation_error_message="Consultation fee cannot be negative.",
            ),
        ]

    def __str__(self):
        return f"Dr. {self.full_name}"

    @property
    def full_name(self):
        return self.user.get_full_name() or self.user.username

    @property
    def is_active(self):
        return self.user.is_active

    def clean(self):
        self.registration_number = (self.registration_number or "").strip().upper()
        self.phone = normalize_phone(self.phone)

        errors = {}
        if self.phone:
            try:
                sri_lanka_phone_validator(self.phone)
            except ValidationError as error:
                errors["phone"] = error.messages
        # Non-field error: "user" is never a field on the doctor forms.
        if self.user_id and self.user.role != Role.DOCTOR:
            errors[NON_FIELD_ERRORS] = [
                "Only users with the Doctor role can have a doctor profile."
            ]
        if self.department_id and self._department_changed() and not self.department.is_active:
            errors["department"] = ["This department is inactive."]
        if errors:
            raise ValidationError(errors)

    def _department_changed(self):
        # A doctor already in a department that is later deactivated can still be edited.
        if self.pk is None:
            return True
        saved = Doctor.objects.filter(pk=self.pk).values_list("department_id", flat=True).first()
        return saved != self.department_id


class Weekday(models.IntegerChoices):
    # Same numbering as Python's date.weekday(), so date.weekday() can be used directly.
    MONDAY = 0, "Monday"
    TUESDAY = 1, "Tuesday"
    WEDNESDAY = 2, "Wednesday"
    THURSDAY = 3, "Thursday"
    FRIDAY = 4, "Friday"
    SATURDAY = 5, "Saturday"
    SUNDAY = 6, "Sunday"


class DoctorSchedule(models.Model):
    """A weekly working block, e.g. Monday 09:00-12:00 in 15-minute slots.

    The block does not have to divide evenly into slots: a final slot that would run
    past end_time is simply not offered (see selectors.slots_for_date).
    """

    SLOT_MINUTE_CHOICES = [
        (10, "10 minutes"),
        (15, "15 minutes"),
        (20, "20 minutes"),
        (30, "30 minutes"),
    ]

    doctor = models.ForeignKey(Doctor, on_delete=models.CASCADE, related_name="schedules")
    weekday = models.PositiveSmallIntegerField(choices=Weekday.choices)
    start_time = models.TimeField()
    end_time = models.TimeField()
    slot_minutes = models.PositiveSmallIntegerField(choices=SLOT_MINUTE_CHOICES, default=15)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["weekday", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="doctors_schedule_end_after_start",
                violation_error_message="End time must be after start time.",
            ),
        ]

    def __str__(self):
        hours = f"{self.start_time:%H:%M}-{self.end_time:%H:%M}"
        return f"{self.doctor} {self.get_weekday_display()} {hours}"

    def clean(self):
        if not (self.doctor_id and self.start_time and self.end_time and self.is_active):
            return
        if self.end_time <= self.start_time:
            return  # reported by the CheckConstraint
        # Two blocks overlap if each starts before the other ends; touching blocks
        # (one ends at 12:00, the next starts at 12:00) are fine.
        clash = (
            DoctorSchedule.objects.filter(
                doctor_id=self.doctor_id,
                weekday=self.weekday,
                is_active=True,
                start_time__lt=self.end_time,
                end_time__gt=self.start_time,
            )
            .exclude(pk=self.pk)
            .first()
        )
        if clash:
            raise ValidationError(
                f"This overlaps the existing {clash.get_weekday_display()} block "
                f"{clash.start_time:%H:%M}-{clash.end_time:%H:%M}."
            )
