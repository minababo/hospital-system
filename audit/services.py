"""The one way to write to the audit log: log_action().

Convention: call it as the LAST step inside the service's transaction.atomic() block,
after the write succeeded. If anything later rolls the transaction back, the audit entry
disappears with it, so the log never records something that didn't happen.
"""

from datetime import date, datetime, time
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.db import models
from django.db.models.fields.files import FieldFile

from audit.context import get_request_context
from audit.models import Action, AuditLog

# Never stored, whatever the caller passes.
AUDIT_EXCLUDED_FIELDS = {"password", "last_login"}

__all__ = [
    "Action",
    "created_changes",
    "diff",
    "log_action",
    "saved_snapshot",
    "snapshot",
    "tracked_fields",
    "updated_changes",
]


def json_safe(value):
    """A value the JSON column can hold: money, dates and times become strings, a
    related object becomes "<its name> (#<pk>)", an uploaded file its name only."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, FieldFile):
        return value.name or ""
    if isinstance(value, models.Model):
        return f"{value} (#{value.pk})"
    if isinstance(value, list | tuple | set):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    try:
        return str(value)
    except Exception:  # an odd object must never stop the real action from being saved
        return f"<{type(value).__name__}>"


def _change_pair(value):
    """[old, new], also when a caller passed a single value instead of a pair."""
    if isinstance(value, list | tuple) and len(value) == 2:
        return [json_safe(value[0]), json_safe(value[1])]
    return [None, json_safe(value)]


def snapshot(instance, fields):
    """{field: value} for the named fields, JSON-safe, sensitive fields left out."""
    values = {}
    for name in fields:
        if name in AUDIT_EXCLUDED_FIELDS:
            continue
        values[name] = json_safe(getattr(instance, name))
    return values


# Bookkeeping columns that change on every save; not interesting in a diff.
UNTRACKED_FIELDS = {"id", "created_at", "updated_at"}


def tracked_fields(model):
    """Names of the model's own columns worth recording (FKs by their field name)."""
    return [
        field.name
        for field in model._meta.concrete_fields
        if field.name not in UNTRACKED_FIELDS and field.name not in AUDIT_EXCLUDED_FIELDS
    ]


def saved_snapshot(instance, fields=None):
    """Snapshot of the row as it is in the database. Use this for "before": a ModelForm
    may already have changed the in-memory object before the service runs."""
    fields = fields or tracked_fields(type(instance))
    return snapshot(type(instance)._base_manager.get(pk=instance.pk), fields)


def created_changes(instance, fields=None):
    """changes for a CREATE entry: each field from nothing to its value."""
    fields = fields or tracked_fields(type(instance))
    return {name: [None, value] for name, value in snapshot(instance, fields).items()}


def updated_changes(instance, before, fields=None):
    """changes for an UPDATE entry: only the fields that differ from `before`."""
    fields = fields or tracked_fields(type(instance))
    return diff(before, snapshot(instance, fields))


def diff(before, after):
    """{field: [old, new]} for fields whose value changed."""
    return {
        name: [before.get(name), after.get(name)]
        for name in sorted(set(before) | set(after))
        if name not in AUDIT_EXCLUDED_FIELDS and before.get(name) != after.get(name)
    }


def _actor(user):
    """The user, or None for anonymous visitors (e.g. a failed login)."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def log_action(*, actor, action, event, obj=None, patient=None, changes=None, message=""):
    actor = _actor(actor)
    context = get_request_context()
    clean_changes = {
        str(name): _change_pair(value)
        for name, value in (changes or {}).items()
        if name not in AUDIT_EXCLUDED_FIELDS
    }
    entry = AuditLog(
        actor=actor,
        actor_name=(actor.get_full_name() or actor.username)[:150] if actor else "",
        actor_role=getattr(actor, "role", "") if actor else "",
        action=action,
        event=event[:80],
        changes=clean_changes,
        message=(message or "")[:255],
        patient=patient,
        ip_address=context["ip"],
        user_agent=context["user_agent"],
    )
    if obj is not None and obj.pk is not None:
        entry.content_type = ContentType.objects.get_for_model(obj)
        entry.object_id = str(obj.pk)
        entry.object_repr = str(obj)[:255]
    entry.save()
    return entry
