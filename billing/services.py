from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from audit.services import Action, log_action, snapshot, updated_changes
from billing.models import (
    CENT,
    Charge,
    ChargeType,
    Invoice,
    InvoiceStatus,
    Payment,
    PaymentMethod,
)
from billing.selectors import uninvoiced_completed_appointments
from common.dates import local_day_bounds

# Each public function records one audit entry as its last step (same transaction).
# Any change to an invoice's status or money locks the invoice row first
# (select_for_update), so two cashiers can't both take the last payment.


def money(value):
    """Decimal rounded to cents. Never pass floats around for money."""
    try:
        return Decimal(str(value)).quantize(CENT)
    except (InvalidOperation, TypeError) as error:
        raise ValidationError("Enter a valid amount.") from error


def _lock_invoice(invoice):
    # No select_related here: PostgreSQL can't lock the nullable side of a join.
    return Invoice.objects.select_for_update().get(pk=invoice.pk)


def _require_reason(reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError("Please give a reason.")
    return reason[:255]


def _require_draft(invoice):
    if invoice.status != InvoiceStatus.DRAFT:
        raise ValidationError("Only draft invoices can be changed.")


# --- Charges -----------------------------------------------------------------


@transaction.atomic
def post_charge(
    *,
    patient,
    charge_type,
    description,
    quantity,
    unit_price,
    source_type="",
    source_id=None,
    acting_user,
    audit_note="",
):
    """Record a billable item for a patient. This is the only way other modules bill.

    Contract for laboratory, pharmacy and admissions: pass a source_type naming your
    object ("lab_order", "dispense", "admission", ...) and its id. Calling again for
    the same source returns the existing live charge unchanged, so retries and double
    clicks never bill twice (idempotent).
    """
    if source_type and source_id is not None:
        existing = _live_charge_for(source_type, source_id)
        if existing:
            return existing

    unit_price = money(unit_price)
    charge = Charge(
        patient=patient,
        charge_type=charge_type,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        # Set before full_clean(): field checks run before clean() and amount can't be empty.
        amount=(Decimal(quantity or 0) * unit_price).quantize(CENT),
        source_type=source_type,
        source_id=source_id,
        created_by=acting_user,
    )
    # Field validators and clean() check the inputs and compute amount. The unique
    # source rule is left to the database so a race is caught below.
    charge.full_clean(validate_constraints=False)
    try:
        with transaction.atomic():  # savepoint: a failed insert doesn't break the outer transaction
            charge.save()
    except IntegrityError:
        existing = _live_charge_for(source_type, source_id) if source_type else None
        if existing is None:
            raise
        return existing  # another request created it a moment ago (nothing new to log)
    # Only a NEW charge is logged; returning an existing one above writes nothing.
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="billing.charge.posted",
        obj=charge,
        patient=patient,
        message=f"{charge.description}: Rs. {charge.amount:,.2f}{audit_note}",
    )
    return charge


def _live_charge_for(source_type, source_id):
    return Charge.objects.filter(
        source_type=source_type, source_id=source_id, is_voided=False
    ).first()


@transaction.atomic
def capture_consultation_charges(*, patient, acting_user):
    """One CONSULTATION charge per completed appointment not billed yet, at the fee
    that was agreed when it was booked (the appointment's snapshot)."""
    charges = []
    for appointment in uninvoiced_completed_appointments(patient):
        charges.append(
            post_charge(
                patient=patient,
                charge_type=ChargeType.CONSULTATION,
                description=f"Consultation — {appointment.doctor} ({appointment.date:%Y-%m-%d})",
                quantity=1,
                unit_price=appointment.consultation_fee,
                source_type="appointment",
                source_id=appointment.pk,
                acting_user=acting_user,
            )
        )
    return charges


def normalise_description(text):
    """Compare descriptions ignoring case and extra spaces: " X-ray  chest" = "x-ray chest"."""
    return " ".join((text or "").split()).casefold()


def find_possible_duplicates(
    *, patient, charge_type, description, unit_price, now=None, exclude=None
):
    """Live manual charges that look the same as a new one: same patient, type,
    description (ignoring case and spacing) and unit price, and still open to correction
    (unbilled or on a draft) or entered today. An older charge on an issued invoice is
    usually a real repeat (e.g. a second dressing next week), so it doesn't count."""
    today_start, today_end = local_day_bounds(timezone.localdate(now or timezone.now()))
    candidates = (
        Charge.objects.filter(
            patient=patient,
            charge_type=charge_type,
            unit_price=money(unit_price),
            is_voided=False,
            source_id__isnull=True,
        )
        .filter(
            Q(invoice__isnull=True)
            | Q(invoice__status=InvoiceStatus.DRAFT)
            | Q(created_at__gte=today_start, created_at__lt=today_end)
        )
        .select_related("invoice")
    )
    if exclude is not None:
        candidates = candidates.exclude(pk=exclude.pk)
    # Descriptions are compared in Python: simple and exact, and a patient has few
    # manual charges at this price, so the list is short.
    wanted = normalise_description(description)
    ids = [c.pk for c in candidates if normalise_description(c.description) == wanted]
    return candidates.filter(pk__in=ids).order_by("created_at", "pk")


def _describe_match(charge):
    created = timezone.localtime(charge.created_at)
    where = charge.invoice.number if charge.invoice_id else "unbilled"
    return (
        f"{charge.description} × {charge.quantity}, Rs. {charge.amount:,.2f}, "
        f"added {created:%d %b %Y %H:%M} ({where})"
    )


@transaction.atomic
def add_manual_charge(
    *,
    patient,
    charge_type,
    description,
    quantity,
    unit_price,
    acting_user,
    invoice=None,
    confirm_duplicate=False,
    now=None,
):
    if invoice is not None:
        invoice = _lock_invoice(invoice)
        _require_draft(invoice)
        if invoice.patient_id != patient.pk:
            raise ValidationError("The invoice belongs to another patient.")
    matches = list(
        find_possible_duplicates(
            patient=patient,
            charge_type=charge_type,
            description=description,
            unit_price=unit_price,
            now=now,
        )
    )
    if matches and not confirm_duplicate:
        # The code lets the view show the "Add anyway" checkbox.
        listed = "; ".join(_describe_match(match) for match in matches)
        raise ValidationError(
            f"This looks like a charge that is already there: {listed}. "
            'Tick "Add anyway" if it is a separate charge.',
            code="possible_duplicate",
        )
    charge = post_charge(
        patient=patient,
        charge_type=charge_type,
        description=description,
        quantity=quantity,
        unit_price=unit_price,
        acting_user=acting_user,
        audit_note=" (added despite possible duplicate)" if matches else "",
    )
    if invoice is not None:
        charge.invoice = invoice
        charge.save(update_fields=["invoice"])
        # post_charge logged the charge; this entry records adding it to the draft.
        log_action(
            actor=acting_user,
            action=Action.UPDATE,
            event="billing.invoice.charge_added",
            obj=invoice,
            patient=patient,
            message=f"Added {charge.description} (Rs. {charge.amount:,.2f})",
        )
    return charge


CHARGE_ALREADY_VOIDED = "This charge is already voided."
CHARGE_ON_ISSUED_INVOICE = "This charge is on an issued invoice. Void the invoice first."
SYSTEM_CHARGE_NOT_EDITABLE = (
    "System charges can't be edited — void the charge or apply a discount instead."
)


def void_blocked_reason(charge):
    """Why this charge can't be voided, or None. Used by the void page and void_charge."""
    if charge.is_voided:
        return CHARGE_ALREADY_VOIDED
    if charge.invoice_id and charge.invoice.status != InvoiceStatus.DRAFT:
        return CHARGE_ON_ISSUED_INVOICE
    return None


def edit_blocked_reason(charge):
    """Why this charge can't be edited, or None (the same rules as Charge.is_editable)."""
    if not charge.is_manual:
        return SYSTEM_CHARGE_NOT_EDITABLE
    if charge.is_voided:
        return "Voided charges can't be edited."
    if charge.invoice_id and charge.invoice.status != InvoiceStatus.DRAFT:
        return "This charge is on an issued invoice and can't be edited. Void the invoice first."
    return None


def _lock_charge_and_invoice(charge):
    """Lock the charge, then its invoice (if any), and return the fresh charge."""
    charge = Charge.objects.select_for_update().get(pk=charge.pk)
    if charge.invoice_id:
        # Attach the locked invoice so the checks below see its current status.
        charge.invoice = _lock_invoice(charge.invoice)
    return charge


@transaction.atomic
def void_charge(charge, *, reason, acting_user, now=None):
    reason = _require_reason(reason)
    charge = _lock_charge_and_invoice(charge)
    blocked = void_blocked_reason(charge)
    if blocked:
        raise ValidationError(blocked)
    charge.invoice = None
    charge.is_voided = True
    charge.voided_at = now or timezone.now()
    charge.voided_by = acting_user
    charge.void_reason = reason
    charge.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="billing.charge.voided",
        obj=charge,
        patient=charge.patient,
        changes={"is_voided": [False, True]},
        message=f"Voided: {reason}",
    )
    return charge


EDITABLE_CHARGE_FIELDS = ["description", "charge_type", "quantity", "unit_price", "amount"]


@transaction.atomic
def edit_charge(charge, *, description, charge_type, quantity, unit_price, reason, acting_user):
    """Correct a manual charge (wrong price, quantity, description or type) while it is
    unbilled or on a draft invoice. System charges are never edited: they mirror a
    consultation, lab test, dispense or bed stay, so the fix is to void or discount."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": ["Give a reason for the correction."]})
    charge = _lock_charge_and_invoice(charge)
    blocked = edit_blocked_reason(charge)
    if blocked:
        raise ValidationError(blocked)
    if charge_type == ChargeType.CONSULTATION:
        raise ValidationError(
            {"charge_type": ["Consultation charges come from completed appointments."]}
        )

    before = snapshot(charge, EDITABLE_CHARGE_FIELDS)
    charge.description = (description or "").strip()
    charge.charge_type = charge_type
    charge.quantity = quantity
    charge.unit_price = money(unit_price)
    charge.full_clean()  # validates the fields and recomputes amount
    changes = updated_changes(charge, before, EDITABLE_CHARGE_FIELDS)
    if not changes:
        raise ValidationError("Nothing to change: the charge already has these values.")
    charge.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="billing.charge.edited",
        obj=charge,
        patient=charge.patient,
        changes=changes,
        message=f"Correction: {reason[:200]}",
    )
    return charge


# --- Invoices ----------------------------------------------------------------


@transaction.atomic
def create_invoice(*, patient, charge_ids, acting_user):
    """Create a DRAFT invoice from the selected unbilled charges. Completed
    consultations that weren't billed yet are captured and included automatically."""
    captured = capture_consultation_charges(patient=patient, acting_user=acting_user)
    wanted = {int(pk) for pk in charge_ids} | {charge.pk for charge in captured}
    if not wanted:
        raise ValidationError("Select at least one charge for the invoice.")

    charges = list(Charge.objects.select_for_update().filter(pk__in=wanted))
    if len(charges) != len(wanted):
        raise ValidationError("Some of the selected charges no longer exist.")
    for charge in charges:
        if charge.patient_id != patient.pk:
            raise ValidationError("A selected charge belongs to another patient.")
        if charge.is_voided:
            raise ValidationError(f'"{charge.description}" has been voided.')
        if charge.invoice_id:
            raise ValidationError(f'"{charge.description}" is already on an invoice.')

    invoice = Invoice(patient=patient, created_by=acting_user)
    invoice.full_clean()
    invoice.save()
    Charge.objects.filter(pk__in=wanted).update(invoice=invoice)
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="billing.invoice.created",
        obj=invoice,
        patient=patient,
        message=f"Draft with {len(wanted)} charge(s), subtotal Rs. {invoice.subtotal:,.2f}",
    )
    return invoice


@transaction.atomic
def remove_charge_from_invoice(invoice, charge, *, acting_user):
    invoice = _lock_invoice(invoice)
    _require_draft(invoice)
    if charge.invoice_id != invoice.pk:
        raise ValidationError("That charge isn't on this invoice.")
    Charge.objects.filter(pk=charge.pk).update(invoice=None)
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="billing.invoice.charge_removed",
        obj=invoice,
        patient=invoice.patient,
        message=f"Removed {charge.description} (Rs. {charge.amount:,.2f})",
    )


@transaction.atomic
def set_discount(invoice, *, amount, reason, acting_user, now=None):
    invoice = _lock_invoice(invoice)
    _require_draft(invoice)
    amount = money(amount)
    reason = (reason or "").strip()
    if amount < 0:
        raise ValidationError("The discount can't be negative.")
    if amount > invoice.subtotal:
        raise ValidationError("The discount can't be more than the subtotal.")
    if amount > 0 and not reason:
        raise ValidationError("Give a reason for the discount.")
    old_discount = invoice.discount
    invoice.discount = amount
    invoice.discount_reason = reason[:255] if amount > 0 else ""
    # Accountability on the invoice itself (the audit log keeps the full history).
    invoice.discount_set_by = acting_user if amount > 0 else None
    invoice.discount_set_at = (now or timezone.now()) if amount > 0 else None
    invoice.full_clean()
    invoice.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="billing.invoice.discount_set",
        obj=invoice,
        patient=invoice.patient,
        changes={"discount": [old_discount, amount]},
        message=invoice.discount_reason,
    )
    return invoice


@transaction.atomic
def issue_invoice(invoice, *, acting_user, now=None):
    invoice = _lock_invoice(invoice)
    _require_draft(invoice)
    if not invoice.charges.filter(is_voided=False).exists():
        raise ValidationError("Add at least one charge before issuing the invoice.")
    if invoice.total < 0:
        raise ValidationError("The discount is larger than the subtotal. Adjust it first.")
    invoice.status = InvoiceStatus.ISSUED
    invoice.issued_at = now or timezone.now()
    invoice.issued_by = acting_user
    _recalculate_status(invoice)  # a fully discounted (Rs. 0) invoice is PAID straight away
    invoice.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="billing.invoice.issued",
        obj=invoice,
        patient=invoice.patient,
        changes={"status": [InvoiceStatus.DRAFT, invoice.status]},
        message=f"Issued for Rs. {invoice.total:,.2f}",
    )
    return invoice


@transaction.atomic
def void_invoice(invoice, *, reason, acting_user, now=None):
    reason = _require_reason(reason)
    invoice = _lock_invoice(invoice)
    if invoice.status == InvoiceStatus.VOID:
        raise ValidationError("This invoice is already void.")
    if invoice.payments.filter(is_voided=False).exists():
        raise ValidationError("This invoice has payments. Void the payments first.")
    # The charges go back to "unbilled" so they can be invoiced again correctly.
    invoice.charges.update(invoice=None)
    old_status = invoice.status
    invoice.status = InvoiceStatus.VOID
    invoice.voided_at = now or timezone.now()
    invoice.voided_by = acting_user
    invoice.void_reason = reason
    invoice.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="billing.invoice.voided",
        obj=invoice,
        patient=invoice.patient,
        changes={"status": [old_status, InvoiceStatus.VOID]},
        message=f"Voided: {reason}",
    )
    return invoice


# --- Payments ----------------------------------------------------------------


@transaction.atomic
def record_payment(*, invoice, amount, method, reference, acting_user, now=None):
    invoice = _lock_invoice(invoice)
    if invoice.status not in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID):
        raise ValidationError("Payments can only be taken for issued, unpaid invoices.")
    amount = money(amount)
    if amount <= 0:
        raise ValidationError({"amount": ["Enter an amount greater than zero."]})
    balance = invoice.balance
    if amount > balance:
        raise ValidationError(
            {"amount": [f"The payment is more than the balance of Rs. {balance:,.2f}."]}
        )
    reference = (reference or "").strip()
    if method != PaymentMethod.CASH and not reference:
        raise ValidationError({"reference": ["A reference is required for non-cash payments."]})

    payment = Payment(
        invoice=invoice,
        amount=amount,
        method=method,
        reference=reference,
        received_by=acting_user,
        received_at=now or timezone.now(),
    )
    payment.full_clean()
    payment.save()
    old_status = invoice.status
    _recalculate_status(invoice)
    invoice.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="billing.payment.recorded",
        obj=payment,
        patient=invoice.patient,
        changes={"invoice_status": [old_status, invoice.status]},
        message=f"Paid Rs. {amount:,.2f} by {payment.get_method_display()} on {invoice.number}",
    )
    return payment


@transaction.atomic
def void_payment(payment, *, reason, acting_user, now=None):
    reason = _require_reason(reason)
    invoice = _lock_invoice(payment.invoice)
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_voided:
        raise ValidationError("This payment is already voided.")
    payment.is_voided = True
    payment.voided_at = now or timezone.now()
    payment.voided_by = acting_user
    payment.void_reason = reason
    payment.save()
    old_status = invoice.status
    _recalculate_status(invoice)
    invoice.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="billing.payment.voided",
        obj=payment,
        patient=invoice.patient,
        changes={"is_voided": [False, True], "invoice_status": [old_status, invoice.status]},
        message=f"Voided Rs. {payment.amount:,.2f}: {reason}",
    )
    return payment


def _recalculate_status(invoice):
    """Set ISSUED / PARTIALLY_PAID / PAID from the money actually received."""
    if invoice.status in (InvoiceStatus.DRAFT, InvoiceStatus.VOID):
        return
    if invoice.balance <= 0:
        invoice.status = InvoiceStatus.PAID
    elif invoice.amount_paid > 0:
        invoice.status = InvoiceStatus.PARTIALLY_PAID
    else:
        invoice.status = InvoiceStatus.ISSUED
