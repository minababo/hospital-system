from decimal import Decimal

from django.conf import settings
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower


def _user_fk():
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )


def format_decimal(value):
    """12.500 -> "12.5", 100.000 -> "100" (no trailing zeros, no exponent)."""
    return f"{value.normalize():f}" if value is not None else ""


class Section(models.TextChoices):
    HAEMATOLOGY = "HAEMATOLOGY", "Haematology"
    BIOCHEMISTRY = "BIOCHEMISTRY", "Biochemistry"
    MICROBIOLOGY = "MICROBIOLOGY", "Microbiology"
    IMMUNOLOGY = "IMMUNOLOGY", "Immunology"
    URINALYSIS = "URINALYSIS", "Urinalysis"
    HISTOPATHOLOGY = "HISTOPATHOLOGY", "Histopathology"
    OTHER = "OTHER", "Other"


class SpecimenType(models.TextChoices):
    BLOOD = "BLOOD", "Blood"
    SERUM = "SERUM", "Serum"
    PLASMA = "PLASMA", "Plasma"
    URINE = "URINE", "Urine"
    STOOL = "STOOL", "Stool"
    SWAB = "SWAB", "Swab"
    SPUTUM = "SPUTUM", "Sputum"
    CSF = "CSF", "CSF"
    TISSUE = "TISSUE", "Tissue"
    OTHER = "OTHER", "Other"


class LabTest(models.Model):
    """A test in the lab catalog, e.g. FBC — Full Blood Count, with its parameters."""

    code = models.CharField(max_length=20, help_text="Short code, e.g. FBC")
    name = models.CharField(max_length=150)
    section = models.CharField(max_length=20, choices=Section.choices)
    specimen_type = models.CharField(max_length=20, choices=SpecimenType.choices)
    price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    turnaround_hours = models.PositiveSmallIntegerField(default=24)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["section", "name"]
        constraints = [
            models.UniqueConstraint(
                Lower("code"),
                name="laboratory_labtest_code_ci_unique",
                violation_error_message="A test with this code already exists.",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="laboratory_labtest_price_non_negative"
            ),
        ]

    def __str__(self):
        return f"{self.code} — {self.name}"

    def clean(self):
        self.code = (self.code or "").strip().upper()
        self.name = (self.name or "").strip()


class LabTestParameter(models.Model):
    """One measured value of a test, e.g. Haemoglobin (g/dL, 12–16) in FBC."""

    class ResultType(models.TextChoices):
        NUMERIC = "NUMERIC", "Number"
        TEXT = "TEXT", "Text"

    test = models.ForeignKey(LabTest, on_delete=models.CASCADE, related_name="parameters")
    name = models.CharField(max_length=100)
    unit = models.CharField(max_length=30, blank=True)
    result_type = models.CharField(
        max_length=10, choices=ResultType.choices, default=ResultType.NUMERIC
    )
    ref_low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    ref_text = models.CharField(
        "Reference (text)", max_length=100, blank=True, help_text='e.g. "Negative"'
    )
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["display_order", "id"]
        constraints = [
            models.UniqueConstraint(
                "test",
                Lower("name"),
                name="laboratory_parameter_name_unique_per_test",
                violation_error_message="This test already has a parameter with that name.",
            ),
            models.CheckConstraint(
                condition=models.Q(ref_low__isnull=True)
                | models.Q(ref_high__isnull=True)
                | models.Q(ref_low__lte=models.F("ref_high")),
                name="laboratory_parameter_range_order",
                violation_error_message="The low reference value must not be above the high one.",
            ),
        ]

    def __str__(self):
        return f"{self.test.code}: {self.name}"

    @property
    def reference_display(self):
        return reference_display(self.ref_low, self.ref_high, self.ref_text, self.unit)

    def clean(self):
        self.name = (self.name or "").strip()
        if self.result_type == self.ResultType.TEXT and (
            self.ref_low is not None or self.ref_high is not None
        ):
            raise ValidationError("Text parameters use the text reference, not low/high values.")


def reference_display(low, high, text, unit=""):
    """Human-readable reference range: "12–16 g/dL", "< 5", "> 1", "Negative" or "—"."""
    if low is not None and high is not None:
        shown = f"{format_decimal(low)}–{format_decimal(high)}"
    elif high is not None:
        shown = f"< {format_decimal(high)}"
    elif low is not None:
        shown = f"> {format_decimal(low)}"
    else:
        return text or "—"
    return f"{shown} {unit}".strip()


class OrderStatus(models.TextChoices):
    REQUESTED = "REQUESTED", "Requested"
    SAMPLE_COLLECTED = "SAMPLE_COLLECTED", "Sample collected"
    COMPLETED = "COMPLETED", "Completed"
    CANCELLED = "CANCELLED", "Cancelled"


class Priority(models.TextChoices):
    ROUTINE = "ROUTINE", "Routine"
    URGENT = "URGENT", "Urgent"


class LabOrder(models.Model):
    """A lab request: from a doctor's consultation (record + ordering doctor) or a
    walk-in with an external referrer."""

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="lab_orders"
    )
    record = models.ForeignKey(
        "records.MedicalRecord",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lab_orders",
    )
    ordering_doctor = models.ForeignKey(
        "doctors.Doctor",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="lab_orders",
    )
    referred_by = models.CharField(
        max_length=150, blank=True, help_text="Referring doctor or clinic for walk-in requests"
    )
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.ROUTINE)
    clinical_notes = models.TextField(blank=True)
    status = models.CharField(
        max_length=20, choices=OrderStatus.choices, default=OrderStatus.REQUESTED
    )
    sample_collected_at = models.DateTimeField(null=True, blank=True)
    sample_collected_by = _user_fk()
    sample_notes = models.CharField(max_length=255, blank=True)
    released_at = models.DateTimeField(null=True, blank=True)
    released_by = _user_fk()
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = _user_fk()
    cancel_reason = models.CharField(max_length=255, blank=True)
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Every order says who asked for it: a consultation's doctor, or a referrer.
            models.CheckConstraint(
                condition=models.Q(record__isnull=False, ordering_doctor__isnull=False)
                | ~models.Q(referred_by=""),
                name="laboratory_order_has_requester",
                violation_error_message="An order needs a consultation and doctor, or a referrer.",
            ),
        ]

    def __str__(self):
        return f"{self.number} ({self.patient})"

    @property
    def number(self):
        return f"LAB-{self.pk:06d}" if self.pk else None

    @property
    def requested_by(self):
        return str(self.ordering_doctor) if self.ordering_doctor_id else self.referred_by

    def clean(self):
        self.referred_by = (self.referred_by or "").strip()
        if self.record_id and (
            self.patient_id != self.record.patient_id
            or self.ordering_doctor_id != self.record.doctor_id
        ):
            raise ValidationError(
                {NON_FIELD_ERRORS: ["The order's patient and doctor must match the consultation."]}
            )


class LabOrderItem(models.Model):
    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="items")
    # PROTECT: a test that appears in past orders can be deactivated, never deleted.
    test = models.ForeignKey(LabTest, on_delete=models.PROTECT, related_name="order_items")
    # Snapshot of the catalog price when ordered; later price changes don't alter it.
    price = models.DecimalField(max_digits=10, decimal_places=2)
    comment = models.TextField("Interpretation / comment", blank=True)

    class Meta:
        ordering = ["pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["order", "test"],
                name="laboratory_item_unique_test",
                violation_error_message="This test is already on the order.",
            ),
            models.CheckConstraint(
                condition=models.Q(price__gte=0), name="laboratory_item_price_non_negative"
            ),
        ]

    def __str__(self):
        return f"{self.test} on {self.order.number}"


class ResultFlag(models.TextChoices):
    LOW = "L", "Low"
    HIGH = "H", "High"
    NORMAL = "N", "Normal"
    NONE = "", "—"


def compute_flag(value, low, high):
    """Compare a numeric result with its reference range.

    Below low -> "L", above high -> "H", otherwise "N". Values exactly on a boundary
    are normal. A missing low or high is simply not checked. With no range at all, or
    no numeric value, there is nothing to compare: "".
    """
    if value is None or (low is None and high is None):
        return ResultFlag.NONE
    if low is not None and value < low:
        return ResultFlag.LOW
    if high is not None and value > high:
        return ResultFlag.HIGH
    return ResultFlag.NORMAL


class LabResult(models.Model):
    """One parameter's value for one ordered test. Unit and reference range are copied
    from the catalog when entered, so editing the catalog later doesn't change what a
    released report said."""

    item = models.ForeignKey(LabOrderItem, on_delete=models.CASCADE, related_name="results")
    parameter = models.ForeignKey(
        LabTestParameter, on_delete=models.PROTECT, related_name="results"
    )
    value_numeric = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    value_text = models.CharField(max_length=255, blank=True)
    unit = models.CharField(max_length=30, blank=True)
    ref_low = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    ref_high = models.DecimalField(max_digits=12, decimal_places=3, null=True, blank=True)
    ref_text = models.CharField(max_length=100, blank=True)
    flag = models.CharField(max_length=1, choices=ResultFlag.choices, blank=True)
    entered_by = _user_fk()
    entered_at = models.DateTimeField()

    class Meta:
        ordering = ["parameter__display_order", "parameter_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["item", "parameter"], name="laboratory_result_unique_parameter"
            ),
        ]

    def __str__(self):
        return f"{self.parameter.name}: {self.display_value}"

    @property
    def display_value(self):
        if self.value_numeric is not None:
            return format_decimal(self.value_numeric)
        return self.value_text

    @property
    def reference_display(self):
        return reference_display(self.ref_low, self.ref_high, self.ref_text, self.unit)

    @property
    def is_abnormal(self):
        return self.flag in (ResultFlag.LOW, ResultFlag.HIGH)


class LabOrderReport(models.Model):
    """Links an uploaded report file (stored by the patients app) to a lab order."""

    order = models.ForeignKey(LabOrder, on_delete=models.CASCADE, related_name="reports")
    document = models.OneToOneField(
        "patients.PatientDocument", on_delete=models.PROTECT, related_name="lab_order_link"
    )

    def __str__(self):
        return f"{self.document.original_name} ({self.order.number})"
