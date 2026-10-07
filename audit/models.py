from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import models


class Action(models.TextChoices):
    CREATE = "CREATE", "Create"
    UPDATE = "UPDATE", "Update"
    DELETE = "DELETE", "Delete"
    STATUS_CHANGE = "STATUS_CHANGE", "Status change"
    LOGIN = "LOGIN", "Login"
    LOGOUT = "LOGOUT", "Logout"
    LOGIN_FAILED = "LOGIN_FAILED", "Failed login"
    VIEW = "VIEW", "View"


class ImmutableError(Exception):
    """Raised when code tries to change or remove an audit entry."""


# Links Django itself clears (on_delete=SET_NULL) when a user, patient or content type
# is deleted. The name/description snapshots on the row still say who and what it was.
NULLABLE_LINKS = {"actor", "patient", "content_type"}


class AuditLogQuerySet(models.QuerySet):
    # Bulk changes are refused too. Raw SQL could still bypass this; a database trigger
    # that rejects UPDATE/DELETE on the table is the next step (see README).
    def update(self, **kwargs):
        # The one exception: Django's SET_NULL runs queryset.update(<link>=None).
        if kwargs and set(kwargs) <= NULLABLE_LINKS and all(v is None for v in kwargs.values()):
            return super().update(**kwargs)
        raise ImmutableError("Audit log entries can't be changed.")

    def delete(self):
        raise ImmutableError("Audit log entries can't be deleted.")


class AuditLog(models.Model):
    """One recorded action. Append-only: rows are written once and never changed."""

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    # Copies of who it was at the time, kept even if the user is later renamed.
    actor_name = models.CharField(max_length=150, blank=True)
    actor_role = models.CharField(max_length=20, blank=True)
    action = models.CharField(max_length=20, choices=Action.choices)
    event = models.CharField(max_length=80, help_text='e.g. "appointments.appointment.cancelled"')
    content_type = models.ForeignKey(
        ContentType, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    object_id = models.CharField(max_length=64, blank=True)
    object_repr = models.CharField(max_length=255, blank=True)
    # String reference: audit never imports the patients app.
    patient = models.ForeignKey(
        "patients.Patient", on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )
    changes = models.JSONField(default=dict, blank=True, help_text="{field: [old, new]}")
    message = models.CharField(max_length=255, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    objects = AuditLogQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["actor", "created_at"], name="audit_actor_created_idx"),
            models.Index(fields=["patient", "created_at"], name="audit_patient_created_idx"),
            models.Index(fields=["content_type", "object_id"], name="audit_object_idx"),
            models.Index(fields=["event"], name="audit_event_idx"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.actor_name or 'anonymous'}: {self.event}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ImmutableError("Audit log entries can't be changed.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ImmutableError("Audit log entries can't be deleted.")

    @property
    def module(self):
        return self.event.split(".", 1)[0]
