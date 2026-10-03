from django.contrib import admin

from billing.models import Charge, Invoice, Payment

# Read-only: money is changed only through billing.services so every rule and
# status change is applied. The admin is for looking things up.


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Charge)
class ChargeAdmin(ReadOnlyAdmin):
    list_display = ("description", "patient", "charge_type", "amount", "invoice", "is_voided")
    list_filter = ("charge_type", "is_voided")
    list_select_related = ("patient", "invoice")
    search_fields = ("description", "patient__first_name", "patient__last_name")


@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyAdmin):
    list_display = ("__str__", "status", "discount", "created_at", "issued_at")
    list_filter = ("status",)
    list_select_related = ("patient",)


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("__str__", "invoice", "method", "amount", "received_at", "is_voided")
    list_filter = ("method", "is_voided")
    list_select_related = ("invoice",)
