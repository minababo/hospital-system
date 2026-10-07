from django.contrib import admin

from audit.models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only: the audit log is append-only, so the admin can't add, edit or delete."""

    list_display = ("created_at", "actor_name", "actor_role", "action", "event", "object_repr")
    list_filter = ("action", "event")
    search_fields = ("actor_name", "object_repr")
    list_select_related = ("actor",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
