import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone

from common.validators import (
    DOCUMENT_EXTENSIONS,
    file_extension,
    normalize_nic,
    normalize_phone,
    sri_lanka_phone_validator,
    validate_sri_lanka_nic,
)

MAX_AGE_YEARS = 130


def years_between(start, end):
    """Whole years from start to end (an age)."""
    return end.year - start.year - ((end.month, end.day) < (start.month, start.day))


class Patient(models.Model):
    class Gender(models.TextChoices):
        MALE = "MALE", "Male"
        FEMALE = "FEMALE", "Female"
        OTHER = "OTHER", "Other"

    class BloodGroup(models.TextChoices):
        A_POS = "A+", "A+"
        A_NEG = "A-", "A-"
        B_POS = "B+", "B+"
        B_NEG = "B-", "B-"
        AB_POS = "AB+", "AB+"
        AB_NEG = "AB-", "AB-"
        O_POS = "O+", "O+"
        O_NEG = "O-", "O-"
        UNKNOWN = "UNKNOWN", "Unknown"

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    gender = models.CharField(max_length=10, choices=Gender.choices)
    # NULL (not "") when missing, so the unique constraint ignores patients without a NIC.
    nic = models.CharField("NIC", max_length=12, null=True, blank=True)  # noqa: DJ001
    phone = models.CharField(max_length=20, help_text="e.g. 0771234567 or +94771234567")
    email = models.EmailField(blank=True)
    address = models.TextField()
    blood_group = models.CharField(
        max_length=10, choices=BloodGroup.choices, default=BloodGroup.UNKNOWN
    )
    allergies = models.TextField(blank=True, help_text="Leave blank if none known")
    emergency_contact_name = models.CharField(max_length=200, blank=True)
    emergency_contact_phone = models.CharField(max_length=20, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                Lower("nic"),
                condition=models.Q(nic__isnull=False),
                name="patients_patient_nic_ci_unique",
                violation_error_message="A patient with this NIC is already registered.",
            ),
        ]

    def __str__(self):
        return f"{self.full_name} ({self.mrn})"

    @property
    def mrn(self):
        """Medical record number, e.g. P000123.

        Computed from the primary key rather than stored: the pk is already unique and
        never changes, so a separate column could only drift out of sync with it.
        """
        return f"P{self.pk:06d}" if self.pk else None

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def age(self):
        return years_between(self.date_of_birth, timezone.localdate())

    def clean(self):
        self.first_name = (self.first_name or "").strip()
        self.last_name = (self.last_name or "").strip()
        self.phone = normalize_phone(self.phone)
        self.emergency_contact_phone = normalize_phone(self.emergency_contact_phone)
        self.nic = normalize_nic(self.nic) or None

        errors = {}
        for field in ("phone", "emergency_contact_phone"):
            value = getattr(self, field)
            if value:  # phone itself is required; that's checked as a blank field
                try:
                    sri_lanka_phone_validator(value)
                except ValidationError as error:
                    errors[field] = error.messages
        if self.nic:
            try:
                validate_sri_lanka_nic(self.nic)
            except ValidationError as error:
                errors["nic"] = error.messages
        if self.date_of_birth:
            today = timezone.localdate()
            if self.date_of_birth > today:
                errors["date_of_birth"] = ["Date of birth cannot be in the future."]
            elif years_between(self.date_of_birth, today) > MAX_AGE_YEARS:
                errors["date_of_birth"] = [
                    f"Date of birth cannot be more than {MAX_AGE_YEARS} years ago."
                ]
        if errors:
            raise ValidationError(errors)


def patient_document_path(instance, filename):
    """Storage key like patients/12/3f2a...e9.pdf.

    A random name instead of the uploaded one: no personal data in storage keys, no
    clashes, and no tricks with names like "../../x". The original name is kept in
    the database for display and downloads.
    """
    extension = DOCUMENT_EXTENSIONS.get(file_extension(filename), "bin")
    return f"patients/{instance.patient_id}/{uuid.uuid4().hex}.{extension}"


class PatientDocument(models.Model):
    class Category(models.TextChoices):
        LAB_REPORT = "LAB_REPORT", "Lab report"
        PRESCRIPTION = "PRESCRIPTION", "Prescription"
        IMAGING = "IMAGING", "Imaging"
        REFERRAL = "REFERRAL", "Referral"
        DISCHARGE_SUMMARY = "DISCHARGE_SUMMARY", "Discharge summary"
        ID_DOCUMENT = "ID_DOCUMENT", "ID document"
        INSURANCE = "INSURANCE", "Insurance"
        OTHER = "OTHER", "Other"

    patient = models.ForeignKey(Patient, on_delete=models.PROTECT, related_name="documents")
    file = models.FileField(upload_to=patient_document_path, max_length=255)
    original_name = models.CharField(max_length=255)
    category = models.CharField(max_length=20, choices=Category.choices)
    description = models.CharField(max_length=255, blank=True)
    content_type = models.CharField(max_length=50)
    size_bytes = models.PositiveIntegerField()
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-uploaded_at", "-pk"]

    def __str__(self):
        return f"{self.original_name} ({self.patient.mrn})"
