from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


class Medicine(models.Model):
    """A medicine in the hospital's catalog."""

    class Form(models.TextChoices):
        TABLET = "TABLET", "Tablet"
        CAPSULE = "CAPSULE", "Capsule"
        SYRUP = "SYRUP", "Syrup"
        SUSPENSION = "SUSPENSION", "Suspension"
        INJECTION = "INJECTION", "Injection"
        INFUSION = "INFUSION", "Infusion"
        CREAM = "CREAM", "Cream"
        OINTMENT = "OINTMENT", "Ointment"
        DROPS = "DROPS", "Drops"
        INHALER = "INHALER", "Inhaler"
        OTHER = "OTHER", "Other"

    name = models.CharField(max_length=150)
    generic_name = models.CharField(max_length=150, blank=True)
    form = models.CharField(max_length=20, choices=Form.choices)
    strength = models.CharField(max_length=50, help_text="e.g. 500 mg or 5 mg/5 ml")
    unit_price = models.DecimalField(
        "Unit price (Rs.)",
        max_digits=10,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
        help_text="Price per tablet, capsule, bottle, ...",
    )
    reorder_level = models.PositiveIntegerField(
        default=10, help_text="Warn when usable stock falls to this many units"
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name", "strength"]
        constraints = [
            # Each name + strength + form combination exists once, ignoring case.
            models.UniqueConstraint(
                Lower("name"),
                Lower("strength"),
                "form",
                name="pharmacy_medicine_unique",
                violation_error_message="This medicine (name, strength and form) already exists.",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name="pharmacy_medicine_unit_price_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.name} {self.strength} ({self.get_form_display()})"

    def clean(self):
        self.name = (self.name or "").strip()
        self.generic_name = (self.generic_name or "").strip()
        self.strength = (self.strength or "").strip()


class StockStatus(models.TextChoices):
    OK = "OK", "In stock"
    LOW = "LOW", "Low"
    OUT_OF_STOCK = "OUT_OF_STOCK", "Out of stock"


class StockBatch(models.Model):
    """One delivery of a medicine with its own batch number and expiry date.

    quantity_on_hand is only ever changed by pharmacy.services together with a
    StockMovement, so the movements always add up to it (the ledger invariant).
    """

    medicine = models.ForeignKey(Medicine, on_delete=models.PROTECT, related_name="batches")
    batch_number = models.CharField(max_length=50)
    expiry_date = models.DateField()
    quantity_received = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    quantity_on_hand = models.PositiveIntegerField()
    supplier = models.CharField(max_length=150, blank=True)
    received_at = models.DateTimeField(default=timezone.now)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["expiry_date", "id"]
        verbose_name_plural = "stock batches"
        constraints = [
            models.UniqueConstraint(
                "medicine",
                Lower("batch_number"),
                name="pharmacy_batch_number_unique_per_medicine",
                violation_error_message=(
                    "This batch number already exists for this medicine. Adding more to an "
                    "existing batch is not supported; record the delivery with its own "
                    "batch number."
                ),
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_received__gt=0),
                name="pharmacy_batch_received_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(quantity_on_hand__lte=models.F("quantity_received")),
                name="pharmacy_batch_on_hand_within_received",
            ),
        ]

    def __str__(self):
        return f"{self.medicine} batch {self.batch_number}"

    def clean(self):
        self.batch_number = (self.batch_number or "").strip().upper()

    def is_expired(self, today):
        return self.expiry_date < today

    def days_to_expiry(self, today):
        return (self.expiry_date - today).days


class MovementType(models.TextChoices):
    RECEIVE = "RECEIVE", "Received"
    DISPENSE = "DISPENSE", "Dispensed"
    ADJUSTMENT = "ADJUSTMENT", "Adjustment"
    EXPIRY_WRITE_OFF = "EXPIRY_WRITE_OFF", "Expiry write-off"


class StockMovement(models.Model):
    """An append-only ledger row: every change to a batch's stock, signed
    (+ in, − out). Rows are never updated or deleted after they are created."""

    batch = models.ForeignKey(StockBatch, on_delete=models.PROTECT, related_name="movements")
    movement_type = models.CharField(max_length=20, choices=MovementType.choices)
    quantity = models.IntegerField()
    reason = models.CharField(max_length=255, blank=True)
    dispense_item = models.ForeignKey(
        "pharmacy.DispenseItem",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="allocations",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(quantity=0), name="pharmacy_movement_non_zero"
            ),
            models.CheckConstraint(
                condition=~models.Q(movement_type="RECEIVE") | models.Q(quantity__gt=0),
                name="pharmacy_movement_receive_positive",
            ),
            models.CheckConstraint(
                condition=~models.Q(movement_type__in=["DISPENSE", "EXPIRY_WRITE_OFF"])
                | models.Q(quantity__lt=0),
                name="pharmacy_movement_out_negative",
            ),
            models.CheckConstraint(
                condition=~models.Q(movement_type="DISPENSE")
                | models.Q(dispense_item__isnull=False),
                name="pharmacy_movement_dispense_has_item",
            ),
            models.CheckConstraint(
                condition=~models.Q(movement_type__in=["ADJUSTMENT", "EXPIRY_WRITE_OFF"])
                | ~models.Q(reason=""),
                name="pharmacy_movement_reason_required",
            ),
        ]

    def __str__(self):
        return f"{self.get_movement_type_display()} {self.quantity:+d} ({self.batch})"


class Dispense(models.Model):
    """Medicines handed over for a prescription in one go. A prescription can be
    dispensed in several parts (partial dispensing)."""

    # String references: pharmacy/models.py never imports the records app.
    prescription = models.ForeignKey(
        "records.Prescription", on_delete=models.PROTECT, related_name="dispenses"
    )
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="dispenses"
    )
    dispensed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+"
    )
    dispensed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-dispensed_at", "-id"]

    def __str__(self):
        return f"{self.number} ({self.patient})"

    @property
    def number(self):
        return f"DSP-{self.pk:06d}" if self.pk else None


class DispenseItem(models.Model):
    dispense = models.ForeignKey(Dispense, on_delete=models.CASCADE, related_name="items")
    prescription_item = models.ForeignKey(
        "records.PrescriptionItem", on_delete=models.PROTECT, related_name="dispense_items"
    )
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    # Snapshot of the medicine's price when dispensed (what the patient is charged).
    unit_price = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="pharmacy_dispense_item_quantity_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0),
                name="pharmacy_dispense_item_price_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.quantity} × {self.prescription_item.medicine}"

    @property
    def amount(self):
        return self.quantity * self.unit_price
