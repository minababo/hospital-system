from django.contrib import admin

from records.models import (
    Diagnosis,
    MedicalRecord,
    Prescription,
    PrescriptionItem,
    RecordAddendum,
    RecordReport,
    Vitals,
)

# Read-mostly: clinical records are written through the consultation pages so the
# finalize/addendum rules apply. The admin is for looking things up.


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class DiagnosisInline(admin.TabularInline):
    model = Diagnosis
    extra = 0
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(MedicalRecord)
class MedicalRecordAdmin(ReadOnlyAdmin):
    list_display = ("patient", "doctor", "status", "created_at", "finalized_at")
    list_filter = ("status",)
    list_select_related = ("patient", "doctor__user")
    search_fields = ("patient__first_name", "patient__last_name")
    inlines = [DiagnosisInline]


class PrescriptionItemInline(admin.TabularInline):
    model = PrescriptionItem
    extra = 0
    can_delete = False

    def has_change_permission(self, request, obj=None):
        return False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Prescription)
class PrescriptionAdmin(ReadOnlyAdmin):
    list_display = ("patient", "doctor", "status", "issued_at")
    list_filter = ("status",)
    list_select_related = ("patient", "doctor__user")
    inlines = [PrescriptionItemInline]


@admin.register(Vitals)
class VitalsAdmin(ReadOnlyAdmin):
    list_display = ("patient", "recorded_at", "bp_systolic", "bp_diastolic", "pulse_bpm")
    list_select_related = ("patient",)


@admin.register(RecordAddendum)
class RecordAddendumAdmin(ReadOnlyAdmin):
    list_display = ("record", "author", "created_at")


@admin.register(RecordReport)
class RecordReportAdmin(ReadOnlyAdmin):
    list_display = ("record", "document")
