"""Dispensing queries. Unlike pharmacy/selectors.py, this module may import records."""

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Prefetch, Sum
from django.urls import reverse

from patients.selectors import HistoryEvent, search_patients
from pharmacy.models import Dispense, DispenseItem
from pharmacy.selectors import usable_stock
from records.models import Prescription, PrescriptionItem, PrescriptionStatus

DISPENSABLE_STATUSES = (PrescriptionStatus.ISSUED, PrescriptionStatus.PARTIALLY_DISPENSED)


def dispensing_queue(q=None, status=None):
    """Prescriptions waiting at the pharmacy, oldest issued first."""
    prescriptions = (
        Prescription.objects.filter(status__in=[status] if status else DISPENSABLE_STATUSES)
        .select_related("patient", "doctor__user")
        .annotate(item_count=Count("items"))
        .order_by("issued_at", "pk")
    )
    if q:
        prescriptions = prescriptions.filter(patient__in=search_patients(q.strip()))
    return prescriptions


def dispensed_quantities(prescription):
    """{prescription_item_id: units dispensed so far}."""
    rows = (
        DispenseItem.objects.filter(prescription_item__prescription=prescription)
        .values("prescription_item")
        .annotate(total=Sum("quantity"))
    )
    return {row["prescription_item"]: row["total"] for row in rows}


@dataclass
class ItemProgress:
    item: PrescriptionItem
    prescribed: int
    dispensed: int
    usable_stock: int
    unit_price: Decimal

    @property
    def remaining(self):
        return max(self.prescribed - self.dispensed, 0)

    @property
    def default_quantity(self):
        """What the form suggests: everything left, as far as stock allows."""
        return min(self.remaining, self.usable_stock)


def item_progress(prescription, today=None):
    """One ItemProgress per prescription item (a few small queries per item; prescriptions
    only have a handful of items)."""
    dispensed = dispensed_quantities(prescription)
    return [
        ItemProgress(
            item=item,
            prescribed=item.quantity,
            dispensed=dispensed.get(item.pk, 0),
            usable_stock=usable_stock(item.medicine, today),
            unit_price=item.medicine.unit_price,
        )
        for item in prescription.items.select_related("medicine").order_by("pk")
    ]


def prescription_dispenses(prescription):
    return (
        prescription.dispenses.select_related("dispensed_by")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=DispenseItem.objects.select_related("prescription_item__medicine"),
            )
        )
        .order_by("dispensed_at", "id")
    )


def dispense_history_events(patient):
    """Registered in patients.selectors.PROVIDERS by PharmacyConfig.ready()."""
    dispenses = (
        Dispense.objects.filter(patient=patient)
        .select_related("prescription")
        .annotate(item_count=Count("items"))
    )
    return [
        HistoryEvent(
            timestamp=dispense.dispensed_at,
            kind="Pharmacy",
            title=(
                f"Medicines dispensed ({dispense.number}, {dispense.item_count} "
                f"item{'s' if dispense.item_count != 1 else ''})"
            ),
            detail=dispense.notes,
            # Link to the consultation (visible to the clinical roles who see history).
            url=reverse("records:record_detail", args=[dispense.prescription.record_id]),
        )
        for dispense in dispenses
    ]
