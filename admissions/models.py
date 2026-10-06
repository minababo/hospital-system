from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


def nights_between(start, end):
    """Nights between two aware datetimes, counted on Sri Lankan calendar dates
    (00:00 Asia/Colombo), never negative. 23:50 → 00:10 the next day is 1 night;
    a same-day stay is 0 nights."""
    days = (timezone.localtime(end).date() - timezone.localtime(start).date()).days
    return max(days, 0)


class WardType(models.TextChoices):
    GENERAL = "GENERAL", "General"
    ICU = "ICU", "ICU"
    MATERNITY = "MATERNITY", "Maternity"
    PAEDIATRIC = "PAEDIATRIC", "Paediatric"
    SURGICAL = "SURGICAL", "Surgical"
    PRIVATE = "PRIVATE", "Private"


class Ward(models.Model):
    name = models.CharField(max_length=100)
    ward_type = models.CharField(max_length=20, choices=WardType.choices)
    department = models.ForeignKey(
        "doctors.Department",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="wards",
    )
    daily_rate = models.DecimalField(
        "Daily rate (Rs.)",
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(0)],
        help_text="Charged per night in a bed of this ward",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(
                Lower("name"),
                name="admissions_ward_name_ci_unique",
                violation_error_message="A ward with this name already exists.",
            ),
            models.CheckConstraint(
                condition=models.Q(daily_rate__gte=0),
                name="admissions_ward_daily_rate_non_negative",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        self.name = (self.name or "").strip()


class Bed(models.Model):
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, related_name="beds")
    bed_number = models.CharField(max_length=20)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["ward__name", "bed_number"]
        constraints = [
            models.UniqueConstraint(
                "ward",
                Lower("bed_number"),
                name="admissions_bed_number_unique_per_ward",
                violation_error_message="This ward already has a bed with that number.",
            ),
        ]

    def __str__(self):
        return f"{self.ward.name} — {self.bed_number}"

    def clean(self):
        self.bed_number = (self.bed_number or "").strip().upper()


class Source(models.TextChoices):
    OPD = "OPD", "Outpatient appointment"
    EMERGENCY = "EMERGENCY", "Emergency"
    REFERRAL = "REFERRAL", "Referral"
    DIRECT = "DIRECT", "Direct"


class DischargeType(models.TextChoices):
    HOME = "HOME", "Discharged home"
    TRANSFERRED_OUT = "TRANSFERRED_OUT", "Transferred to another hospital"
    AGAINST_MEDICAL_ADVICE = "AGAINST_MEDICAL_ADVICE", "Left against medical advice"
    DECEASED = "DECEASED", "Deceased"


class AdmissionStatus(models.TextChoices):
    ADMITTED = "ADMITTED", "Admitted"
    DISCHARGED = "DISCHARGED", "Discharged"


class Admission(models.Model):
    """One inpatient stay, from admission to discharge. The bed(s) used are recorded
    as BedAssignments; occupancy is derived from them, never stored on the bed."""

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="admissions"
    )
    admitting_doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.PROTECT, related_name="admissions"
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="admissions",
    )
    source = models.CharField(max_length=20, choices=Source.choices)
    reason = models.TextField("Reason for admission")
    status = models.CharField(
        max_length=20, choices=AdmissionStatus.choices, default=AdmissionStatus.ADMITTED
    )
    admitted_at = models.DateTimeField()
    admitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    discharged_at = models.DateTimeField(null=True, blank=True)
    discharged_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    discharge_type = models.CharField(max_length=30, choices=DischargeType.choices, blank=True)
    discharge_summary = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-admitted_at"]
        constraints = [
            # Partial unique index: among ADMITTED rows a patient appears at most once.
            models.UniqueConstraint(
                fields=["patient"],
                condition=models.Q(status="ADMITTED"),
                name="admissions_one_current_admission_per_patient",
                violation_error_message="This patient is already admitted.",
            ),
            # The discharge fields are all set exactly when the status is DISCHARGED.
            models.CheckConstraint(
                condition=(
                    models.Q(status="ADMITTED", discharged_at__isnull=True, discharge_type="")
                    | (
                        models.Q(status="DISCHARGED", discharged_at__isnull=False)
                        & ~models.Q(discharge_type="")
                    )
                ),
                name="admissions_discharge_fields_consistent",
            ),
            models.CheckConstraint(
                condition=models.Q(discharged_at__isnull=True)
                | models.Q(discharged_at__gt=models.F("admitted_at")),
                name="admissions_discharged_after_admitted",
                violation_error_message="Discharge must be after admission.",
            ),
            models.CheckConstraint(
                condition=~models.Q(source="OPD") | models.Q(appointment__isnull=False),
                name="admissions_opd_needs_appointment",
                violation_error_message="Admissions from an outpatient visit need the appointment.",
            ),
        ]

    def __str__(self):
        return f"{self.number} ({self.patient})"

    @property
    def number(self):
        return f"ADM-{self.pk:06d}" if self.pk else None

    @property
    def current_assignment(self):
        # .all() so prefetched assignments are reused.
        return next((a for a in self.assignments.all() if a.ended_at is None), None)

    @property
    def current_bed(self):
        assignment = self.current_assignment
        return assignment.bed if assignment else None

    @property
    def final_bed(self):
        """The last bed used (the current one while admitted). list(...) because a
        QuerySet doesn't support negative indexing; with assignments prefetched (as in
        admissions.selectors) this runs no extra query."""
        assignments = list(self.assignments.all())
        return assignments[-1].bed if assignments else None

    def length_of_stay_days(self, now=None):
        """Nights so far (or in total once discharged), counting a same-day stay as 1."""
        end = self.discharged_at or now or timezone.now()
        return max(nights_between(self.admitted_at, end), 1)

    def clean(self):
        if self.appointment_id and self.appointment.patient_id != self.patient_id:
            raise ValidationError({"appointment": ["That appointment is for another patient."]})


class BedAssignment(models.Model):
    """A period in one bed. An admission has one open assignment (ended_at empty) at a
    time; a transfer closes it and opens the next. Bed charges are posted per
    assignment when it closes, at the ward rate copied here when it opened."""

    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="assignments")
    bed = models.ForeignKey(Bed, on_delete=models.PROTECT, related_name="assignments")
    daily_rate = models.DecimalField(max_digits=10, decimal_places=2)
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["started_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["bed"],
                condition=models.Q(ended_at__isnull=True),
                name="admissions_one_open_assignment_per_bed",
                violation_error_message="This bed is occupied.",
            ),
            models.UniqueConstraint(
                fields=["admission"],
                condition=models.Q(ended_at__isnull=True),
                name="admissions_one_open_assignment_per_admission",
            ),
            # >= (not >) so a transfer made in the same minute as the admission is allowed.
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True)
                | models.Q(ended_at__gte=models.F("started_at")),
                name="admissions_assignment_ends_after_start",
            ),
        ]

    def __str__(self):
        return f"{self.admission.number} in {self.bed}"

    def nights(self, now=None):
        return nights_between(self.started_at, self.ended_at or now or timezone.now())


class NoteType(models.TextChoices):
    DOCTOR_ROUND = "DOCTOR_ROUND", "Doctor's round"
    NURSING = "NURSING", "Nursing"
    OTHER = "OTHER", "Other"


class ProgressNote(models.Model):
    """Append-only: notes are never edited or deleted (there are no views for it)."""

    admission = models.ForeignKey(Admission, on_delete=models.CASCADE, related_name="notes")
    note_type = models.CharField(max_length=20, choices=NoteType.choices)
    text = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"{self.get_note_type_display()} note on {self.admission.number}"
