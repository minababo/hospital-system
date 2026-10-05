"""Catalog and stock queries. Must not import the records app (see tests/test_dependencies.py);
dispensing queries live in pharmacy/dispensing_selectors.py."""

from datetime import timedelta

from django.conf import settings
from django.db.models import Case, F, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from pharmacy.models import Medicine, StockBatch, StockMovement, StockStatus


def medicine_list(search=None, form=None, is_active=None):
    medicines = Medicine.objects.all()
    if search:
        medicines = medicines.filter(Q(name__icontains=search) | Q(generic_name__icontains=search))
    if form:
        medicines = medicines.filter(form=form)
    if is_active is not None:
        medicines = medicines.filter(is_active=is_active)
    return medicines


def active_medicines():
    return Medicine.objects.filter(is_active=True)


# --- Stock --------------------------------------------------------------------------


def _today(today):
    return today or timezone.localdate()


def usable_batches(medicine, today=None):
    """Batches that can be dispensed, in FEFO order (first expiry, first out).
    A batch expiring today is still usable; one that expired yesterday is not."""
    return StockBatch.objects.filter(
        medicine=medicine, quantity_on_hand__gt=0, expiry_date__gte=_today(today)
    ).order_by("expiry_date", "id")


def usable_stock(medicine, today=None):
    total = usable_batches(medicine, today).aggregate(total=Sum("quantity_on_hand"))["total"]
    return total or 0


def inventory_list(search=None, status=None, today=None):
    """Medicines with usable_stock, total_on_hand, nearest_expiry and stock_status.

    Each figure is a correlated subquery over that medicine's batches, so the list
    is one query and the sums can't be inflated by joins.
    """
    today = _today(today)
    batches = StockBatch.objects.filter(medicine=OuterRef("pk")).order_by()
    usable = batches.filter(quantity_on_hand__gt=0, expiry_date__gte=today)

    def total(queryset):
        summed = queryset.values("medicine").annotate(t=Sum("quantity_on_hand")).values("t")
        return Coalesce(Subquery(summed, output_field=IntegerField()), Value(0))

    medicines = (
        Medicine.objects.annotate(
            usable_stock=total(usable),
            total_on_hand=total(batches),
            nearest_expiry=Subquery(usable.order_by("expiry_date").values("expiry_date")[:1]),
        )
        .annotate(
            stock_status=Case(
                When(usable_stock=0, then=Value(StockStatus.OUT_OF_STOCK)),
                When(usable_stock__lte=F("reorder_level"), then=Value(StockStatus.LOW)),
                default=Value(StockStatus.OK),
            )
        )
        .order_by("name", "strength")
    )
    if search:
        medicines = medicines.filter(Q(name__icontains=search) | Q(generic_name__icontains=search))
    if status:
        medicines = medicines.filter(stock_status=status)
    return medicines


def batches_for_medicine(medicine):
    return medicine.batches.select_related("received_by").order_by("expiry_date", "id")


def movements_for_batch(batch):
    return batch.movements.select_related("created_by").order_by("created_at", "id")


def recent_movements(medicine, limit=50):
    return (
        StockMovement.objects.filter(batch__medicine=medicine)
        .select_related("batch", "created_by")
        .order_by("-created_at", "-id")[:limit]
    )


def alerts(today=None, warning_days=None):
    """The four stock alert lists for the alerts page."""
    today = _today(today)
    if warning_days is None:
        warning_days = settings.PHARMACY_EXPIRY_WARNING_DAYS
    in_stock = StockBatch.objects.filter(quantity_on_hand__gt=0).select_related("medicine")
    active = inventory_list(today=today).filter(is_active=True)
    return {
        "expired": in_stock.filter(expiry_date__lt=today).order_by("expiry_date", "id"),
        "expiring_soon": in_stock.filter(
            expiry_date__gte=today, expiry_date__lte=today + timedelta(days=warning_days)
        ).order_by("expiry_date", "id"),
        "low_stock": active.filter(usable_stock__gt=0, usable_stock__lte=F("reorder_level")),
        "out_of_stock": active.filter(usable_stock=0),
    }


def pharmacy_alerts_summary(today=None):
    """Counts for the dashboard's "Pharmacy Alerts" card."""
    return {name: queryset.count() for name, queryset in alerts(today).items()}
