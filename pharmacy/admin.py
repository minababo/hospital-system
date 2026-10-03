from django.contrib import admin

from pharmacy.models import Medicine


@admin.register(Medicine)
class MedicineAdmin(admin.ModelAdmin):
    list_display = ("name", "strength", "form", "generic_name", "is_active")
    list_filter = ("form", "is_active")
    search_fields = ("name", "generic_name")
