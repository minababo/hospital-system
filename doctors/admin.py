from django.contrib import admin

from doctors.models import Department, Doctor, DoctorSchedule


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name",)


class DoctorScheduleInline(admin.TabularInline):
    model = DoctorSchedule
    extra = 0


@admin.register(Doctor)
class DoctorAdmin(admin.ModelAdmin):
    list_display = ("__str__", "department", "specialization", "registration_number")
    list_filter = ("department",)
    list_select_related = ("user", "department")
    search_fields = (
        "user__first_name",
        "user__last_name",
        "specialization",
        "registration_number",
    )
    inlines = [DoctorScheduleInline]


@admin.register(DoctorSchedule)
class DoctorScheduleAdmin(admin.ModelAdmin):
    list_display = ("doctor", "weekday", "start_time", "end_time", "slot_minutes", "is_active")
    list_filter = ("weekday", "is_active")
    list_select_related = ("doctor__user",)
