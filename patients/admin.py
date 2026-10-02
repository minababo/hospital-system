from django.contrib import admin

from patients.models import Patient, PatientDocument


@admin.register(Patient)
class PatientAdmin(admin.ModelAdmin):
    list_display = ("__str__", "nic", "phone", "gender", "date_of_birth", "created_at")
    list_filter = ("gender", "blood_group")
    search_fields = ("first_name", "last_name", "nic", "phone")
    readonly_fields = ("created_by", "created_at", "updated_at")


@admin.register(PatientDocument)
class PatientDocumentAdmin(admin.ModelAdmin):
    # Read-only and no file links: the admin would show a signed storage URL that
    # bypasses the app's role checks. Documents are viewed through the patients pages.
    list_display = ("original_name", "patient", "category", "size_bytes", "uploaded_at")
    list_filter = ("category",)
    list_select_related = ("patient",)
    search_fields = ("original_name", "patient__first_name", "patient__last_name")
    fields = (
        "patient",
        "original_name",
        "category",
        "description",
        "content_type",
        "size_bytes",
        "stored_as",
        "uploaded_by",
        "uploaded_at",
    )
    readonly_fields = fields

    @admin.display(description="Storage key")
    def stored_as(self, document):
        return document.file.name

    def has_add_permission(self, request):
        return False  # uploads go through the app so they are validated
