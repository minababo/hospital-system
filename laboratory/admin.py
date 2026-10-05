from django.contrib import admin

from laboratory.models import LabOrder, LabOrderItem, LabResult, LabTest, LabTestParameter


class LabTestParameterInline(admin.TabularInline):
    model = LabTestParameter
    extra = 0


@admin.register(LabTest)
class LabTestAdmin(admin.ModelAdmin):
    """The catalog is editable here (it's reference data, not patient data)."""

    list_display = ("code", "name", "section", "specimen_type", "price", "is_active")
    list_filter = ("section", "is_active")
    search_fields = ("code", "name")
    inlines = [LabTestParameterInline]


class ReadOnlyAdmin(admin.ModelAdmin):
    # Orders and results change only through laboratory.services (status rules, billing).
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LabOrder)
class LabOrderAdmin(ReadOnlyAdmin):
    list_display = ("__str__", "status", "priority", "created_at", "released_at")
    list_filter = ("status", "priority")
    list_select_related = ("patient",)


@admin.register(LabOrderItem)
class LabOrderItemAdmin(ReadOnlyAdmin):
    list_display = ("order", "test", "price")
    list_select_related = ("order", "test")


@admin.register(LabResult)
class LabResultAdmin(ReadOnlyAdmin):
    list_display = ("item", "parameter", "value_numeric", "value_text", "flag")
    list_select_related = ("item__order", "item__test", "parameter")
