import re
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

ICD10_PATTERN = re.compile(r"^[A-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$")


class RecordStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    FINALIZED = "FINALIZED", "Finalized"


class MedicalRecord(models.Model):
    """The doctor's notes for one consultation (one appointment).

    A record is edited while DRAFT. Once FINALIZED it can't be changed; corrections
    are added as addenda so the original stays as it was signed off.
    """

    appointment = models.OneToOneField(
        "appointments.Appointment", on_delete=models.PROTECT, related_name="medical_record"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="medical_records"
    )
    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.PROTECT, related_name="medical_records"
    )
    status = models.CharField(
        max_length=10, choices=RecordStatus.choices, default=RecordStatus.DRAFT
    )
    presenting_complaint = models.TextField()
    clinical_notes = models.TextField(blank=True)
    examination_findings = models.TextField(blank=True)
    treatment_plan = models.TextField(blank=True)
    follow_up_date = models.DateField(null=True, blank=True)
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Consultation for {self.patient} on {self.appointment.date:%Y-%m-%d}"

    @property
    def primary_diagnosis(self):
        return next((d for d in self.diagnoses.all() if d.diagnosis_type == "PRIMARY"), None)

    def clean(self):
        if not self.appointment_id:
            return
        errors = {}
        appointment = self.appointment
        # patient/doctor are copied from the appointment; they must never disagree.
        if self.patient_id != appointment.patient_id or self.doctor_id != appointment.doctor_id:
            errors[NON_FIELD_ERRORS] = [
                "The record's patient and doctor must match the appointment's."
            ]
        if self.follow_up_date and self.follow_up_date <= appointment.date:
            errors["follow_up_date"] = ["The follow-up date must be after the appointment date."]
        if errors:
            raise ValidationError(errors)


class Diagnosis(models.Model):
    class Type(models.TextChoices):
        PRIMARY = "PRIMARY", "Primary"
        SECONDARY = "SECONDARY", "Secondary"

    record = models.ForeignKey(MedicalRecord, on_delete=models.CASCADE, related_name="diagnoses")
    description = models.CharField(max_length=255)
    icd10_code = models.CharField(
        "ICD-10 code", max_length=10, blank=True, help_text="Optional, e.g. J45 or J45.909"
    )
    diagnosis_type = models.CharField(max_length=10, choices=Type.choices, default=Type.PRIMARY)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["diagnosis_type", "pk"]  # PRIMARY sorts before SECONDARY
        verbose_name_plural = "diagnoses"
        constraints = [
            # Partial unique index: among a record's PRIMARY rows, "record" must be unique,
            # i.e. at most one primary diagnosis per record. Secondary ones are unlimited.
            models.UniqueConstraint(
                fields=["record"],
                condition=models.Q(diagnosis_type="PRIMARY"),
                name="records_one_primary_diagnosis",
                violation_error_message="This consultation already has a primary diagnosis.",
            ),
        ]

    def __str__(self):
        return f"{self.icd10_code} {self.description}".strip()

    def clean(self):
        self.description = (self.description or "").strip()
        self.icd10_code = (self.icd10_code or "").strip().upper()
        if self.icd10_code and not ICD10_PATTERN.match(self.icd10_code):
            raise ValidationError(
                {"icd10_code": ["Enter a valid ICD-10 code, e.g. J45 or J45.909."]}
            )


# (min, max) for each vital sign. Used for both form validators and DB constraints.
VITAL_RANGES = {
    "bp_systolic": (50, 260),
    "bp_diastolic": (30, 160),
    "pulse_bpm": (20, 250),
    "respiratory_rate": (5, 60),
    "spo2_percent": (50, 100),
    "temperature_c": (Decimal("30.0"), Decimal("45.0")),
    "weight_kg": (Decimal("0.5"), Decimal("500")),
    "height_cm": (Decimal("30"), Decimal("250")),
}


def _range(name):
    low, high = VITAL_RANGES[name]
    return [MinValueValidator(low), MaxValueValidator(high)]


def _range_constraint(name):
    low, high = VITAL_RANGES[name]
    return models.CheckConstraint(
        condition=models.Q(**{f"{name}__isnull": True})
        | models.Q(**{f"{name}__gte": low, f"{name}__lte": high}),
        name=f"records_vitals_{name}_range",
    )


class Vitals(models.Model):
    """Vital signs taken at one appointment (usually by a nurse after check-in)."""

    appointment = models.OneToOneField(
        "appointments.Appointment", on_delete=models.PROTECT, related_name="vitals"
    )
    patient = models.ForeignKey("patients.Patient", on_delete=models.PROTECT, related_name="+")
    bp_systolic = models.PositiveSmallIntegerField(
        "BP systolic (mmHg)", null=True, blank=True, validators=_range("bp_systolic")
    )
    bp_diastolic = models.PositiveSmallIntegerField(
        "BP diastolic (mmHg)", null=True, blank=True, validators=_range("bp_diastolic")
    )
    pulse_bpm = models.PositiveSmallIntegerField(
        "Pulse (bpm)", null=True, blank=True, validators=_range("pulse_bpm")
    )
    respiratory_rate = models.PositiveSmallIntegerField(
        "Respiratory rate (/min)", null=True, blank=True, validators=_range("respiratory_rate")
    )
    spo2_percent = models.PositiveSmallIntegerField(
        "SpO₂ (%)", null=True, blank=True, validators=_range("spo2_percent")
    )
    temperature_c = models.DecimalField(
        "Temperature (°C)",
        max_digits=4,
        decimal_places=1,
        null=True,
        blank=True,
        validators=_range("temperature_c"),
    )
    weight_kg = models.DecimalField(
        "Weight (kg)",
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        validators=_range("weight_kg"),
    )
    height_cm = models.DecimalField(
        "Height (cm)",
        max_digits=5,
        decimal_places=1,
        null=True,
        blank=True,
        validators=_range("height_cm"),
    )
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name_plural = "vitals"
        constraints = [
            *[_range_constraint(name) for name in VITAL_RANGES],
            models.CheckConstraint(
                condition=models.Q(bp_systolic__isnull=True)
                | models.Q(bp_diastolic__isnull=True)
                | models.Q(bp_systolic__gt=models.F("bp_diastolic")),
                name="records_vitals_systolic_above_diastolic",
                violation_error_message="Systolic pressure must be higher than diastolic.",
            ),
        ]

    def __str__(self):
        return f"Vitals for {self.patient} at {self.recorded_at:%Y-%m-%d %H:%M}"

    @property
    def bmi(self):
        """Body mass index: weight (kg) / height (m)², 1 decimal place."""
        if not (self.weight_kg and self.height_cm):
            return None
        height_m = self.height_cm / 100
        return (self.weight_kg / (height_m * height_m)).quantize(Decimal("0.1"))

    def clean(self):
        if all(getattr(self, name) is None for name in VITAL_RANGES):
            raise ValidationError("Enter at least one vital sign.")
        if (self.bp_systolic is None) != (self.bp_diastolic is None):
            raise ValidationError("Enter both systolic and diastolic blood pressure, or neither.")


class PrescriptionStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    # Set by the pharmacy module when medicines are handed over (later task).
    PARTIALLY_DISPENSED = "PARTIALLY_DISPENSED", "Partially dispensed"
    DISPENSED = "DISPENSED", "Dispensed"
    CANCELLED = "CANCELLED", "Cancelled"


class Prescription(models.Model):
    """Medicines prescribed in one consultation. DRAFT while the record is a draft,
    ISSUED when the record is finalized; the pharmacy then dispenses ISSUED ones."""

    record = models.OneToOneField(
        MedicalRecord, on_delete=models.PROTECT, related_name="prescription"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="prescriptions"
    )
    doctor = models.ForeignKey(
        "doctors.Doctor", on_delete=models.PROTECT, related_name="prescriptions"
    )
    status = models.CharField(
        max_length=20, choices=PrescriptionStatus.choices, default=PrescriptionStatus.DRAFT
    )
    issued_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    cancel_reason = models.CharField(max_length=255, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Prescription for {self.patient} ({self.get_status_display()})"


class Frequency(models.TextChoices):
    OD = "OD", "Once daily"
    BD = "BD", "Twice daily"
    TDS = "TDS", "Three times daily"
    QDS = "QDS", "Four times daily"
    NOCTE = "NOCTE", "At night"
    MANE = "MANE", "In the morning"
    STAT = "STAT", "Immediately (once)"
    PRN = "PRN", "When required"
    Q4H = "Q4H", "Every 4 hours"
    Q6H = "Q6H", "Every 6 hours"
    Q8H = "Q8H", "Every 8 hours"
    WEEKLY = "WEEKLY", "Once weekly"


class Route(models.TextChoices):
    ORAL = "ORAL", "Oral"
    SUBLINGUAL = "SUBLINGUAL", "Sublingual"
    IV = "IV", "Intravenous (IV)"
    IM = "IM", "Intramuscular (IM)"
    SC = "SC", "Subcutaneous (SC)"
    TOPICAL = "TOPICAL", "Topical"
    INHALED = "INHALED", "Inhaled"
    RECTAL = "RECTAL", "Rectal"
    OPHTHALMIC = "OPHTHALMIC", "Eye (ophthalmic)"
    OTIC = "OTIC", "Ear (otic)"
    NASAL = "NASAL", "Nasal"
    OTHER = "OTHER", "Other"


class PrescriptionItem(models.Model):
    prescription = models.ForeignKey(Prescription, on_delete=models.CASCADE, related_name="items")
    medicine = models.ForeignKey(
        "pharmacy.Medicine", on_delete=models.PROTECT, related_name="prescription_items"
    )
    dose = models.CharField(max_length=50, help_text="e.g. 1 tablet or 5 ml")
    frequency = models.CharField(max_length=10, choices=Frequency.choices)
    route = models.CharField(max_length=12, choices=Route.choices, default=Route.ORAL)
    duration_days = models.PositiveSmallIntegerField(
        "Duration (days)", validators=[MinValueValidator(1)]
    )
    quantity = models.PositiveIntegerField(
        help_text="Total units to dispense", validators=[MinValueValidator(1)]
    )
    instructions = models.CharField(max_length=255, blank=True, help_text="e.g. after meals")
    # True when the doctor prescribed despite a matching allergy note.
    allergy_override = models.BooleanField(default=False)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="records_item_quantity_positive",
                violation_error_message="Quantity must be at least 1.",
            ),
            models.CheckConstraint(
                condition=models.Q(duration_days__gt=0),
                name="records_item_duration_positive",
                violation_error_message="Duration must be at least 1 day.",
            ),
            models.UniqueConstraint(
                fields=["prescription", "medicine"],
                name="records_item_unique_medicine",
                violation_error_message="This medicine is already on the prescription.",
            ),
        ]

    def __str__(self):
        return f"{self.medicine} {self.dose} {self.frequency}"


class RecordAddendum(models.Model):
    """A note added after finalizing, e.g. a correction or a late result."""

    record = models.ForeignKey(MedicalRecord, on_delete=models.CASCADE, related_name="addenda")
    text = models.TextField()
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]
        verbose_name_plural = "record addenda"

    def __str__(self):
        return f"Addendum to {self.record} at {self.created_at:%Y-%m-%d %H:%M}"


class RecordReport(models.Model):
    """Links a patient document (stored by the patients app) to a consultation."""

    record = models.ForeignKey(MedicalRecord, on_delete=models.CASCADE, related_name="reports")
    document = models.OneToOneField(
        "patients.PatientDocument", on_delete=models.PROTECT, related_name="record_link"
    )

    def __str__(self):
        return f"{self.document.original_name} ({self.record})"
