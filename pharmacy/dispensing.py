"""Dispensing services. Unlike pharmacy/services.py, this module may import records and
billing: it connects the prescription (records), the stock (pharmacy.services) and the
bill (billing)."""

from django.core.exceptions import NON_FIELD_ERRORS, PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from billing import services as billing_services
from billing.models import ChargeType
from pharmacy import services as stock_services
from pharmacy.dispensing_selectors import DISPENSABLE_STATUSES, item_progress
from pharmacy.models import Dispense, DispenseItem
from records import services as records_services
from records.models import Prescription

# Audit logging of these actions will be added in these services (audit app).

CHARGE_SOURCE = "dispense_item"


def field_name(item_id):
    """Form field name for an item's quantity, so errors appear next to the right box."""
    return f"qty_{item_id}"


@transaction.atomic
def dispense_prescription(*, prescription, quantities, notes, acting_user, now=None):
    """Hand over some or all of a prescription's medicines.

    quantities = {prescription_item_id: units}. All or nothing: if any item fails
    (not enough stock, more than prescribed, ...), no stock moves and nothing is billed.
    """
    if acting_user.role != Role.PHARMACIST:
        raise PermissionDenied("Only pharmacists can dispense medicines.")
    now = now or timezone.now()
    today = timezone.localdate(now)
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if prescription.status not in DISPENSABLE_STATUSES:
        raise ValidationError("Only issued prescriptions can be dispensed.")

    progress = {row.item.pk: row for row in item_progress(prescription, today)}
    quantities = {int(item_id): qty or 0 for item_id, qty in quantities.items()}
    if set(quantities) - set(progress):
        raise ValidationError("A selected item isn't on this prescription.")

    errors = {}
    for item_id, qty in quantities.items():
        row = progress[item_id]
        medicine = row.item.medicine
        if qty < 0:
            errors[field_name(item_id)] = ["Enter zero or more."]
        elif qty and qty > row.remaining:
            errors[field_name(item_id)] = [f"Only {row.remaining} left to dispense."]
        elif qty and not medicine.is_active:
            errors[field_name(item_id)] = [f"{medicine} is no longer in the catalog."]
        elif qty and qty > row.usable_stock:
            errors[field_name(item_id)] = [
                f"Not enough stock of {medicine}: {row.usable_stock} available."
            ]
    wanted = {item_id: qty for item_id, qty in quantities.items() if qty > 0}
    if not errors and not wanted:
        errors[NON_FIELD_ERRORS] = ["Enter a quantity for at least one medicine."]
    if errors:
        raise ValidationError(errors)

    dispense = Dispense(
        prescription=prescription,
        patient_id=prescription.patient_id,
        dispensed_by=acting_user,
        dispensed_at=now,
        notes=(notes or "").strip(),
    )
    dispense.full_clean()
    dispense.save()

    for item_id, qty in wanted.items():
        medicine = progress[item_id].item.medicine
        dispense_item = DispenseItem(
            dispense=dispense,
            prescription_item=progress[item_id].item,
            quantity=qty,
            unit_price=medicine.unit_price,
        )
        dispense_item.full_clean()
        dispense_item.save()
        # Re-checks stock under row locks; raising here rolls everything back.
        stock_services.allocate_and_issue(
            medicine, qty, dispense_item=dispense_item, acting_user=acting_user, today=today
        )
        # Billed when handed over, for what was actually given (not what was prescribed).
        if dispense_item.unit_price > 0:
            billing_services.post_charge(
                patient=prescription.patient,
                charge_type=ChargeType.PHARMACY,
                description=f"{medicine} × {qty} ({dispense.number})",
                quantity=qty,
                unit_price=dispense_item.unit_price,
                source_type=CHARGE_SOURCE,
                source_id=dispense_item.pk,
                acting_user=acting_user,
            )

    fully_dispensed = all(
        row.remaining - wanted.get(item_id, 0) == 0 for item_id, row in progress.items()
    )
    records_services.update_dispensing_status(
        prescription, fully_dispensed=fully_dispensed, acting_user=acting_user, now=now
    )
    return dispense
