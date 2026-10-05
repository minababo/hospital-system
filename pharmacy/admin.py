from django.contrib import admin

from pharmacy.models import Dispense, DispenseItem, Medicine, StockBatch, StockMovement


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ("name", "strength", "form", "unit_price", "reorder_level", "is_active")
    list_filter = ("form", "is_active")
    search_fields = ("name", "generic_name")


class ReadOnlyAdmin(admin.ModelAdmin):
    # Stock and dispensing change only through pharmacy services, so the ledger
    # (movements) always matches quantity_on_hand. Movements are never edited.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(StockBatch)
class StockBatchAdmin(ReadOnlyAdmin):
    list_display = ("medicine", "batch_number", "expiry_date", "quantity_on_hand", "supplier")
    list_filter = ("expiry_date",)
    list_select_related = ("medicine",)
    search_fields = ("batch_number", "medicine__name")


@admin.register(StockMovement)
class StockMovementAdmin(ReadOnlyAdmin):
    list_display = ("batch", "movement_type", "quantity", "reason", "created_by", "created_at")
    list_filter = ("movement_type",)
    list_select_related = ("batch__medicine", "created_by")


@admin.register(Dispense)
class DispenseAdmin(ReadOnlyAdmin):
    list_display = ("__str__", "dispensed_by", "dispensed_at")
    list_select_related = ("patient", "dispensed_by")


@admin.register(DispenseItem)
class DispenseItemAdmin(ReadOnlyAdmin):
    list_display = ("dispense", "prescription_item", "quantity", "unit_price")
