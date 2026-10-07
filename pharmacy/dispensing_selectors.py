"""Dispensing queries. Unlike pharmacy/selectors.py, this module may import records."""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from django.db.models import Count, Prefetch, Sum

from common.urls import section_url
from patients.selectors import HistoryEvent, search_patients
from pharmacy.models import Dispense, DispenseItem, StockMovement
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


# --- Dispense log (shown to pharmacists and clinicians) ------------------------------


@dataclass
class Allocation:
    """The units of one dispensed medicine that came from one stock batch."""

    batch_number: str
    expiry_date: date
    quantity: int


@dataclass
class DispenseLogItem:
    medicine: str
    quantity: int
    unit_price: Decimal
    allocations: list[Allocation] = field(default_factory=list)

    @property
    def amount(self):
        return self.quantity * self.unit_price


@dataclass
class DispenseLogEntry:
    number: str
    dispensed_at: datetime
    dispensed_by: str
    notes: str
    items: list[DispenseLogItem] = field(default_factory=list)


def dispense_log(prescription):
    """Every dispense of a prescription, oldest first, with what was given and from which
    batches. Three queries however many dispenses, items or batches there are."""
    dispenses = (
        prescription.dispenses.select_related("dispensed_by")
        .prefetch_related(
            Prefetch(
                "items",
                queryset=DispenseItem.objects.select_related(
                    "prescription_item__medicine"
                ).order_by("pk"),
            ),
            Prefetch(
                "items__allocations",
                # Issue movements are negative stock changes; the log shows units given.
                queryset=StockMovement.objects.select_related("batch").order_by(
                    "batch__expiry_date", "pk"
                ),
            ),
        )
        .order_by("dispensed_at", "id")
    )
    return [
        DispenseLogEntry(
            number=dispense.number,
            dispensed_at=dispense.dispensed_at,
            dispensed_by=dispense.dispensed_by.get_full_name() or dispense.dispensed_by.username,
            notes=dispense.notes,
            items=[
                DispenseLogItem(
                    medicine=str(item.prescription_item.medicine),
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                    allocations=[
                        Allocation(
                            batch_number=movement.batch.batch_number,
                            expiry_date=movement.batch.expiry_date,
                            quantity=abs(movement.quantity),
                        )
                        for movement in item.allocations.all()
                    ],
                )
                for item in dispense.items.all()
            ],
        )
        for dispense in dispenses
    ]


@dataclass
class DispensingRow:
    medicine: str
    prescribed: int
    dispensed: int

    @property
    def remaining(self):
        return max(self.prescribed - self.dispensed, 0)


def dispensing_summary(prescription):
    """Prescribed / dispensed / remaining per medicine. Unlike item_progress() it doesn't
    look up stock, so it stays at two queries (for pages outside the pharmacy)."""
    dispensed = dispensed_quantities(prescription)
    return [
        DispensingRow(
            medicine=str(item.medicine),
            prescribed=item.quantity,
            dispensed=dispensed.get(item.pk, 0),
        )
        for item in prescription.items.select_related("medicine").order_by("pk")
    ]


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
            # Link to the consultation's dispensing card (visible to the clinical roles
            # who see history).
            url=section_url("records:record_detail", "dispensing", dispense.prescription.record_id),
        )
        for dispense in dispenses
    ]
