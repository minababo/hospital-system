from datetime import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone


class Status(models.TextChoices):
    BOOKED = "BOOKED", "Booked"
    CHECKED_IN = "CHECKED_IN", "Checked in"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"
    NO_SHOW = "NO_SHOW", "No-show"


# Every status except CANCELLED keeps the slot taken (a no-show still used the doctor's time).
SLOT_OCCUPYING_STATUSES = (Status.BOOKED, Status.CHECKED_IN, Status.COMPLETED, Status.NO_SHOW)

# The only status changes allowed. COMPLETED, CANCELLED and NO_SHOW are final.
ALLOWED_TRANSITIONS = {
    Status.BOOKED: {Status.CHECKED_IN, Status.CANCELLED, Status.NO_SHOW},
    Status.CHECKED_IN: {Status.COMPLETED},
}


def _user_fk():
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )


class Appointment(models.Model):
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="appointments"
    )
    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.PROTECT, related_name="appointments"
    )
    date = models.DateField()
    start_time = models.TimeField()
    end_time = models.TimeField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.BOOKED)
    reason = models.CharField("Reason for visit", max_length=255)
    # Copied from the doctor when booking, so later fee changes don't alter old appointments.
    consultation_fee = models.DecimalField(max_digits=10, decimal_places=2)
    reschedule_count = models.PositiveSmallIntegerField(default=0)

    cancel_reason = models.CharField(max_length=255, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = _user_fk()
    checked_in_at = models.DateTimeField(null=True, blank=True)
    checked_in_by = _user_fk()
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = _user_fk()
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "start_time"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_time__gt=models.F("start_time")),
                name="appointments_end_after_start",
            ),
            models.CheckConstraint(
                condition=models.Q(consultation_fee__gte=0),
                name="appointments_fee_non_negative",
            ),
            # Partial unique indexes: cancelled appointments are ignored, so a cancelled
            # slot can be booked again, but two live bookings can never share it.
            models.UniqueConstraint(
                fields=["doctor", "date", "start_time"],
                condition=~models.Q(status="CANCELLED"),
                name="appointments_unique_doctor_slot",
                violation_error_message="This doctor already has an appointment at that time.",
            ),
            models.UniqueConstraint(
                fields=["patient", "date", "start_time"],
                condition=~models.Q(status="CANCELLED"),
                name="appointments_unique_patient_time",
                violation_error_message="This patient already has an appointment at that time.",
            ),
        ]
        indexes = [
            models.Index(fields=["date", "doctor"], name="appointments_date_doctor_idx"),
            models.Index(fields=["patient", "date"], name="appointments_patient_date_idx"),
        ]

    def __str__(self):
        return f"{self.patient} with {self.doctor} on {self.date:%Y-%m-%d} {self.start_time:%H:%M}"

    @property
    def start_datetime(self):
        return timezone.make_aware(
            datetime.combine(self.date, self.start_time), timezone.get_default_timezone()
        )

    @property
    def end_datetime(self):
        return timezone.make_aware(
            datetime.combine(self.date, self.end_time), timezone.get_default_timezone()
        )

    def is_past(self, now=None):
        """True once the appointment's start time has passed."""
        return self.start_datetime <= (now or timezone.now())

    def can_transition_to(self, status):
        return status in ALLOWED_TRANSITIONS.get(self.status, set())
