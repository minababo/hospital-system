from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from accounts.models import User


@admin.register(User)
class HMSUserAdmin(UserAdmin):
    fieldsets = (*UserAdmin.fieldsets, ("Role", {"fields": ("role",)}))
    add_fieldsets = (*UserAdmin.add_fieldsets, ("Role", {"fields": ("role",)}))
    list_display = (*UserAdmin.list_display, "role")
    list_filter = ("role", *UserAdmin.list_filter)
