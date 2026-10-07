import re
from decimal import Decimal

from django.db.models import (
    DecimalField,
    ExpressionWrapper,
    F,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404

from appointments.models import Appointment
from appointments.models import Status as AppointmentStatus
from billing.models import Charge, ChargeType, Invoice, InvoiceStatus, Payment
from patients.selectors import search_patients

MONEY = DecimalField(max_digits=12, decimal_places=2)
ZERO = Value(Decimal("0.00"), output_field=MONEY)
OPEN_STATUSES = (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID)
INVOICE_NUMBER = re.compile(r"(?:INV-?)?(\d{1,18})", re.IGNORECASE)


def unbilled_charges(patient):
    return Charge.objects.filter(patient=patient, is_voided=False, invoice__isnull=True)


def uninvoiced_completed_appointments(patient):
    """Completed appointments that don't have a live consultation charge yet."""
    charged = Charge.objects.filter(
        source_type="appointment", source_id__isnull=False, is_voided=False
    ).values("source_id")
    return (
        Appointment.objects.filter(patient=patient, status=AppointmentStatus.COMPLETED)
        .exclude(pk__in=charged)
        .select_related("doctor__user")
        .order_by("date", "start_time")
    )


def _sum_per_invoice(queryset):
    """A correlated subquery: the sum of `amount` for rows belonging to each invoice.

    Using a subquery per total (instead of Sum("charges__amount") and
    Sum("payments__amount") in one annotate) avoids the join double-count: joining
    3 charges and 2 payments produces 6 rows, so each sum would be multiplied.
    """
    summed = (
        queryset.filter(invoice=OuterRef("pk"))
        .order_by()
        .values("invoice")
        .annotate(total=Sum("amount"))
        .values("total")
    )
    return Coalesce(Subquery(summed, output_field=MONEY), ZERO)


def with_totals(invoices):
    """Annotate subtotal_amount, paid_amount, total_amount and balance_amount.
    (Names differ from the model's properties, which Django couldn't overwrite.)"""
    return invoices.annotate(
        subtotal_amount=_sum_per_invoice(Charge.objects.filter(is_voided=False)),
        paid_amount=_sum_per_invoice(Payment.objects.filter(is_voided=False)),
    ).annotate(
        total_amount=ExpressionWrapper(F("subtotal_amount") - F("discount"), output_field=MONEY),
        balance_amount=ExpressionWrapper(
            F("subtotal_amount") - F("discount") - F("paid_amount"), output_field=MONEY
        ),
    )


def invoice_list(*, status=None, q=None, date_from=None, date_to=None, outstanding_only=False):
    invoices = with_totals(Invoice.objects.select_related("patient"))
    if status:
        invoices = invoices.filter(status=status)
    if q:
        q = q.strip()
        # "INV-000123" or "123" finds the invoice; anything also searches patients.
        matches = Q(patient__in=search_patients(q))
        number = INVOICE_NUMBER.fullmatch(q)
        if number:
            matches |= Q(pk=int(number.group(1)))
        invoices = invoices.filter(matches)
    if date_from:
        invoices = invoices.filter(created_at__date__gte=date_from)
    if date_to:
        invoices = invoices.filter(created_at__date__lte=date_to)
    if outstanding_only:
        invoices = invoices.filter(status__in=OPEN_STATUSES, balance_amount__gt=0)
    return invoices


def get_invoice(pk):
    return get_object_or_404(
        Invoice.objects.select_related(
            "patient", "issued_by", "voided_by", "created_by", "discount_set_by"
        ).prefetch_related(
            Prefetch("charges", queryset=Charge.objects.order_by("created_at", "pk")),
            Prefetch(
                "payments",
                queryset=Payment.objects.select_related("received_by", "voided_by"),
            ),
        ),
        pk=pk,
    )


def balance_after_payment(payment):
    """The invoice balance right after this payment (for the receipt). Later payments
    and voided payments are ignored."""
    invoice = payment.invoice
    paid_up_to_here = sum(
        (p.amount for p in invoice.payments.all() if not p.is_voided and p.pk <= payment.pk),
        Decimal("0.00"),
    )
    return invoice.total - paid_up_to_here


def patient_billing_summary(patient):
    invoices = list(
        with_totals(Invoice.objects.filter(patient=patient)).exclude(status=InvoiceStatus.VOID)
    )
    outstanding = sum(
        (i.balance_amount for i in invoices if i.status in OPEN_STATUSES), Decimal("0.00")
    )
    return {
        "unbilled_charges": list(unbilled_charges(patient)),
        "pending_consultations": list(uninvoiced_completed_appointments(patient)),
        "invoices": invoices,
        "total_outstanding": outstanding,
    }


# --- Figures for dashboards and reports --------------------------------------


def payments_received(date_from, date_to):
    """Total received in the date range, and per payment method. Voided payments excluded."""
    payments = Payment.objects.filter(
        is_voided=False, received_at__date__gte=date_from, received_at__date__lte=date_to
    )
    by_method = {
        row["method"]: row["total"]
        for row in payments.order_by().values("method").annotate(total=Sum("amount"))
    }
    total = payments.aggregate(total=Coalesce(Sum("amount"), ZERO))["total"]
    return {"total": total, "by_method": by_method}


def invoiced_by_charge_type(date_from, date_to):
    """Charge amounts on issued (non-void) invoices, per charge type, by issue date.
    Invoice-level discounts are not split across charge types."""
    rows = (
        Charge.objects.filter(
            is_voided=False,
            invoice__status__in=(*OPEN_STATUSES, InvoiceStatus.PAID),
            invoice__issued_at__date__gte=date_from,
            invoice__issued_at__date__lte=date_to,
        )
        .order_by()
        .values("charge_type")
        .annotate(total=Sum("amount"))
    )
    totals = {charge_type: Decimal("0.00") for charge_type in ChargeType.values}
    totals.update({row["charge_type"]: row["total"] for row in rows})
    return totals
