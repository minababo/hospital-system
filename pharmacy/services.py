"""Catalog and stock services. Must not import the records app (see tests/test_dependencies.py);
dispensing lives in pharmacy/dispensing.py.

Stock rule: quantity_on_hand changes ONLY through _apply_movement(), which writes a
StockMovement at the same time. So for every batch, the sum of its movements equals
its quantity_on_hand (the ledger invariant)."""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from pharmacy.models import Medicine, MovementType, StockBatch, StockMovement

# Audit logging of these actions will be added in these services (audit app).


# --- Catalog ------------------------------------------------------------------------


@transaction.atomic
def create_medicine(*, acting_user, **fields):
    medicine = Medicine(**fields)
    medicine.full_clean()
    medicine.save()
    return medicine


@transaction.atomic
def update_medicine(medicine, *, acting_user, **fields):
    for name, value in fields.items():
        setattr(medicine, name, value)
    medicine.full_clean()
    medicine.save()
    return medicine


@transaction.atomic
def set_medicine_active(medicine, active, *, acting_user):
    medicine.is_active = active
    medicine.save(update_fields=["is_active", "updated_at"])
    return medicine


# --- Stock --------------------------------------------------------------------------


def _lock_batch(batch):
    return StockBatch.objects.select_for_update().get(pk=batch.pk)


def _apply_movement(batch, quantity, movement_type, *, acting_user, reason="", dispense_item=None):
    """Change a (locked) batch's stock and write the matching ledger row."""
    new_on_hand = batch.quantity_on_hand + quantity
    if new_on_hand < 0:
        raise ValidationError(
            f"Batch {batch.batch_number} has only {batch.quantity_on_hand} on hand."
        )
    if new_on_hand > batch.quantity_received:
        raise ValidationError(
            f"Batch {batch.batch_number} can't hold more than the {batch.quantity_received} "
            "units received."
        )
    batch.quantity_on_hand = new_on_hand
    batch.save(update_fields=["quantity_on_hand"])
    movement = StockMovement(
        batch=batch,
        movement_type=movement_type,
        quantity=quantity,
        reason=(reason or "").strip()[:255],
        dispense_item=dispense_item,
        created_by=acting_user,
    )
    movement.full_clean()
    movement.save()
    return movement


@transaction.atomic
def receive_stock(
    *, medicine, batch_number, expiry_date, quantity, supplier, notes, acting_user, now=None
):
    now = now or timezone.now()
    if not medicine.is_active:
        raise ValidationError("This medicine is inactive. Activate it before receiving stock.")
    if expiry_date < timezone.localdate(now):
        raise ValidationError({"expiry_date": ["This batch has already expired."]})
    if not quantity or quantity <= 0:
        raise ValidationError({"quantity": ["Enter a quantity of at least 1."]})

    batch = StockBatch(
        medicine=medicine,
        batch_number=batch_number,
        expiry_date=expiry_date,
        quantity_received=quantity,
        quantity_on_hand=0,  # the RECEIVE movement below brings it up to `quantity`
        supplier=(supplier or "").strip(),
        notes=(notes or "").strip(),
        received_at=now,
        received_by=acting_user,
    )
    batch.full_clean()  # includes the "batch number unique per medicine" check
    try:
        with transaction.atomic():
            batch.save()
    except IntegrityError as error:
        raise ValidationError(
            "This batch number already exists for this medicine. Adding more to an existing "
            "batch is not supported; record the delivery with its own batch number."
        ) from error
    _apply_movement(batch, quantity, MovementType.RECEIVE, acting_user=acting_user)
    return batch


@transaction.atomic
def adjust_stock(*, batch, quantity_change, reason, acting_user, now=None):
    """Correct a batch's count after a stock check (e.g. breakage, miscount)."""
    if not quantity_change:
        raise ValidationError({"quantity_change": ["Enter a change other than zero."]})
    if not (reason or "").strip():
        raise ValidationError({"reason": ["Give a reason for the adjustment."]})
    batch = _lock_batch(batch)
    return _apply_movement(
        batch, quantity_change, MovementType.ADJUSTMENT, acting_user=acting_user, reason=reason
    )


@transaction.atomic
def write_off_expired(*, batch, acting_user, now=None):
    batch = _lock_batch(batch)
    today = timezone.localdate(now or timezone.now())
    if not batch.is_expired(today):
        raise ValidationError("Only expired batches can be written off.")
    if batch.quantity_on_hand == 0:
        raise ValidationError("This batch has no stock left to write off.")
    return _apply_movement(
        batch,
        -batch.quantity_on_hand,
        MovementType.EXPIRY_WRITE_OFF,
        acting_user=acting_user,
        reason=f"Expired on {batch.expiry_date:%Y-%m-%d}",
    )


def allocate_and_issue(medicine, quantity, *, dispense_item, acting_user, today):
    """Take `quantity` units of a medicine out of stock, FEFO (earliest expiry first),
    spreading over several batches if needed. Used by pharmacy.dispensing.

    The usable batches are locked in a fixed order (expiry, id), so two dispenses at
    the same moment wait for each other instead of both taking the last units, and
    always lock rows in the same order (which avoids deadlocks).
    Must be called inside a transaction (dispensing provides it).
    """
    batches = list(
        StockBatch.objects.select_for_update()
        .filter(medicine=medicine, quantity_on_hand__gt=0, expiry_date__gte=today)
        .order_by("expiry_date", "id")
    )
    available = sum(batch.quantity_on_hand for batch in batches)
    if available < quantity:
        raise ValidationError(
            f"Not enough stock of {medicine}: {available} available, {quantity} needed."
        )
    movements, remaining = [], quantity
    for batch in batches:
        take = min(batch.quantity_on_hand, remaining)
        movements.append(
            _apply_movement(
                batch,
                -take,
                MovementType.DISPENSE,
                acting_user=acting_user,
                dispense_item=dispense_item,
            )
        )
        remaining -= take
        if remaining == 0:
            break
    return movements
