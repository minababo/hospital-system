from datetime import timedelta

from django.db.models import Q

from audit.models import AuditLog
from common.dates import local_day_bounds


def audit_entries():
    # patient is a string FK; select_related still works without importing patients.
    return AuditLog.objects.select_related("actor", "content_type", "patient")


def filtered_entries(filters):
    """Entries matching the cleaned AuditFilterForm data, newest first."""
    entries = audit_entries()
    if filters.get("date_from"):
        entries = entries.filter(created_at__gte=local_day_bounds(filters["date_from"])[0])
    if filters.get("date_to"):
        # Half-open: before the local midnight that starts the next day.
        end = local_day_bounds(filters["date_to"] + timedelta(days=1))[0]
        entries = entries.filter(created_at__lt=end)
    if filters.get("actor"):
        entries = entries.filter(actor=filters["actor"])
    if filters.get("role"):
        entries = entries.filter(actor_role=filters["role"])
    if filters.get("action"):
        entries = entries.filter(action=filters["action"])
    if filters.get("module"):
        entries = entries.filter(event__startswith=f"{filters['module']}.")
    if filters.get("event"):
        entries = entries.filter(event__icontains=filters["event"].strip())
    # The MRN box and the patient_id link both mean "this patient's pk".
    patient_id = filters.get("patient") or filters.get("patient_id")
    if patient_id:
        entries = entries.filter(patient_id=patient_id)
    if filters.get("q"):
        q = filters["q"].strip()
        entries = entries.filter(Q(object_repr__icontains=q) | Q(message__icontains=q))
    return entries
