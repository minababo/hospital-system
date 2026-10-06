from django.contrib import admin

from staff.models import Attendance, Employee, LeaveRequest


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    """Read-mostly: the Staff pages apply the business rules (ending employment
    cancels pending leave), so ending employment isn't done here."""

    list_display = ("__str__", "designation", "department", "category", "status")
    list_filter = ("status", "category", "department")
    search_fields = ("first_name", "last_name", "nic", "phone")
    readonly_fields = ("status", "end_date", "created_at", "updated_at")

    def has_delete_permission(self, request, obj=None):
        return False


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Attendance)
class AttendanceAdmin(ReadOnlyAdmin):
    list_display = ("employee", "date", "status", "check_in", "check_out")
    list_filter = ("status", "date")
    list_select_related = ("employee",)


@admin.register(LeaveRequest)
class LeaveRequestAdmin(ReadOnlyAdmin):
    list_display = ("employee", "leave_type", "start_date", "end_date", "status")
    list_filter = ("status", "leave_type")
    list_select_related = ("employee",)
