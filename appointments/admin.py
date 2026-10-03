from django.contrib import admin

from appointments.models import Appointment


@admin.register(Appointment)
class AppointmentAdmin(admin.ModelAdmin):
    list_display = ("date", "start_time", "patient", "doctor", "status")
    list_filter = ("status", "date", "doctor")
    list_select_related = ("patient", "doctor__user")
    search_fields = ("patient__first_name", "patient__last_name", "doctor__user__last_name")
    date_hierarchy = "date"
    # Status history is written by appointments.services; don't edit it by hand here.
    readonly_fields = (
        "status",
        "reschedule_count",
        "cancel_reason",
        "cancelled_at",
        "cancelled_by",
        "checked_in_at",
        "checked_in_by",
        "completed_at",
        "completed_by",
        "created_by",
        "created_at",
        "updated_at",
    )
