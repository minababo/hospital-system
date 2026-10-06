from django.contrib import admin

from admissions.models import Admission, Bed, BedAssignment, ProgressNote, Ward


class BedInline(admin.TabularInline):
    model = Bed
    extra = 0


@admin.register(Ward)
class WardAdmin(admin.ModelAdmin):
    """Wards and beds are reference data, editable here."""

    list_display = ("name", "ward_type", "daily_rate", "is_active")
    list_filter = ("ward_type", "is_active")
    inlines = [BedInline]


@admin.register(Bed)
class BedAdmin(admin.ModelAdmin):
    list_display = ("bed_number", "ward", "is_active")
    list_filter = ("ward", "is_active")
    list_select_related = ("ward",)


class ReadOnlyAdmin(admin.ModelAdmin):
    # Admissions, bed periods and notes change only through admissions.services,
    # which also posts bed charges; progress notes are append-only.
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Admission)
class AdmissionAdmin(ReadOnlyAdmin):
    list_display = ("__str__", "status", "admitted_at", "discharged_at", "admitting_doctor")
    list_filter = ("status", "source")
    list_select_related = ("patient", "admitting_doctor__user")


@admin.register(BedAssignment)
class BedAssignmentAdmin(ReadOnlyAdmin):
    list_display = ("admission", "bed", "daily_rate", "started_at", "ended_at")
    list_select_related = ("admission", "bed__ward")


@admin.register(ProgressNote)
class ProgressNoteAdmin(ReadOnlyAdmin):
    list_display = ("admission", "note_type", "author", "created_at")
    list_select_related = ("admission", "author")
