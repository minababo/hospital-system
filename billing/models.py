from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models.functions import Round
from django.utils import timezone

CENT = Decimal("0.01")


def _user_fk(on_delete=models.SET_NULL):
    return models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=on_delete,
        null=on_delete == models.SET_NULL,
        blank=on_delete == models.SET_NULL,
        related_name="+",
    )


class ChargeType(models.TextChoices):
    CONSULTATION = "CONSULTATION", "Consultation"
    LABORATORY = "LABORATORY", "Laboratory"
    PHARMACY = "PHARMACY", "Pharmacy"
    ADMISSION = "ADMISSION", "Admission"
    OTHER = "OTHER", "Other"


class Charge(models.Model):
    """One billable item for a patient (a consultation, a lab test, medicines, ...).

    Charges exist before invoices: they are collected as "unbilled" and then grouped
    into an invoice. source_type/source_id point at what caused the charge (e.g. an
    appointment) so the same thing is never billed twice.
    """

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="charges"
    )
    charge_type = models.CharField(max_length=20, choices=ChargeType.choices)
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    unit_price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    source_type = models.CharField(max_length=30, blank=True)
    source_id = models.PositiveBigIntegerField(null=True, blank=True)
    invoice = models.ForeignKey(
        "billing.Invoice",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="charges",
    )
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = _user_fk()
    void_reason = models.CharField(max_length=255, blank=True)
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0), name="billing_charge_quantity_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(unit_price__gte=0), name="billing_charge_unit_price_non_negative"
            ),
            # amount is stored for reporting, so the database guarantees it always equals
            # quantity x unit_price. Round() keeps SQLite (which multiplies as floats) exact.
            models.CheckConstraint(
                condition=models.Q(amount=Round(models.F("quantity") * models.F("unit_price"), 2)),
                name="billing_charge_amount_matches",
            ),
            models.CheckConstraint(
                condition=models.Q(source_type="", source_id__isnull=True)
                | (~models.Q(source_type="") & models.Q(source_id__isnull=False)),
                name="billing_charge_source_both_or_neither",
            ),
            # The same source (e.g. appointment 12) can have only one live charge.
            # A voided charge doesn't count, so it can be billed again correctly.
            models.UniqueConstraint(
                fields=["source_type", "source_id"],
                condition=models.Q(source_id__isnull=False) & models.Q(is_voided=False),
                name="billing_charge_unique_source",
            ),
        ]

    def __str__(self):
        return f"{self.description} ({self.amount})"

    @property
    def is_manual(self):
        """Typed in by billing staff. System charges (consultation, lab, pharmacy, bed)
        have a source and mirror a clinical record, so they are never edited."""
        return self.source_id is None

    @property
    def is_editable(self):
        """Can billing.services.edit_charge change this charge? (Its error says why not.)"""
        return (
            self.is_manual
            and not self.is_voided
            and (self.invoice is None or self.invoice.status == InvoiceStatus.DRAFT)
        )

    def clean(self):
        self.description = (self.description or "").strip()
        if self.quantity is not None and self.unit_price is not None:
            self.amount = (Decimal(self.quantity) * self.unit_price).quantize(CENT)
        if bool(self.source_type) != (self.source_id is not None):
            raise ValidationError("A charge source needs both a type and an id, or neither.")


class InvoiceStatus(models.TextChoices):
    DRAFT = "DRAFT", "Draft"
    ISSUED = "ISSUED", "Issued"
    PARTIALLY_PAID = "PARTIALLY_PAID", "Partially paid"
    PAID = "PAID", "Paid"
    VOID = "VOID", "Void"


class Invoice(models.Model):
    """A bill made of charges. Totals are computed from charges and payments, never
    stored, so they can't get out of step with the rows they come from."""

    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.PROTECT, related_name="invoices"
    )
    status = models.CharField(
        max_length=20, choices=InvoiceStatus.choices, default=InvoiceStatus.DRAFT
    )
    discount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    discount_reason = models.CharField(max_length=255, blank=True)
    # Who set the current discount and when. Empty for invoices discounted before these
    # fields existed (and whenever the discount is 0), so there is no constraint on them.
    discount_set_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
    )
    discount_set_at = models.DateTimeField(null=True, blank=True)
    notes = models.TextField(blank=True)
    issued_at = models.DateTimeField(null=True, blank=True)
    issued_by = _user_fk()
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = _user_fk()
    void_reason = models.CharField(max_length=255, blank=True)
    created_by = _user_fk()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(discount__gte=0), name="billing_invoice_discount_non_negative"
            ),
            models.CheckConstraint(
                condition=models.Q(discount=0) | ~models.Q(discount_reason=""),
                name="billing_invoice_discount_needs_reason",
                violation_error_message="Give a reason for the discount.",
            ),
        ]

    def __str__(self):
        return f"{self.number} ({self.patient})"

    # The properties below are computed on demand. They use .all(), so they reuse
    # prefetched charges/payments (see selectors.get_invoice) instead of querying again.

    @property
    def number(self):
        return f"INV-{self.pk:06d}" if self.pk else None

    @property
    def subtotal(self):
        return sum(
            (charge.amount for charge in self.charges.all() if not charge.is_voided),
            Decimal("0.00"),
        )

    @property
    def total(self):
        return self.subtotal - self.discount

    @property
    def amount_paid(self):
        return sum(
            (payment.amount for payment in self.payments.all() if not payment.is_voided),
            Decimal("0.00"),
        )

    @property
    def balance(self):
        return self.total - self.amount_paid


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Cash"
    CARD = "CARD", "Card"
    BANK_TRANSFER = "BANK_TRANSFER", "Bank transfer"
    ONLINE = "ONLINE", "Online"


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(
        max_digits=12, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))]
    )
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    reference = models.CharField(
        max_length=100, blank=True, help_text="Card slip, transfer or transaction number"
    )
    received_by = _user_fk(on_delete=models.PROTECT)
    received_at = models.DateTimeField(default=timezone.now)
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = _user_fk()
    void_reason = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["received_at", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="billing_payment_amount_positive"
            ),
            models.CheckConstraint(
                condition=models.Q(method="CASH") | ~models.Q(reference=""),
                name="billing_payment_reference_for_non_cash",
                violation_error_message="A reference is required for non-cash payments.",
            ),
        ]

    def __str__(self):
        return f"{self.receipt_number} ({self.amount})"

    @property
    def receipt_number(self):
        return f"RCT-{self.pk:06d}" if self.pk else None
