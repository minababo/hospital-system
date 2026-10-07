from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.generic import ListView, TemplateView

from accounts.models import Role
from accounts.permissions import RoleRequiredMixin
from audit.forms import AuditFilterForm
from audit.models import AuditLog
from audit.selectors import audit_entries, filtered_entries
from common.csv_export import csv_response

AUDIT_ROLES = (Role.ADMIN,)
CSV_MAX_ROWS = 10_000

CSV_HEADER = [
    "Time",
    "Actor",
    "Role",
    "Action",
    "Event",
    "Object type",
    "Object ID",
    "Object",
    "Patient MRN",
    "Message",
    "IP address",
]


def csv_row(entry):
    return [
        timezone.localtime(entry.created_at).strftime("%Y-%m-%d %H:%M:%S"),
        entry.actor_name,
        entry.actor_role,
        entry.get_action_display(),
        entry.event,
        entry.content_type.model if entry.content_type else "",
        entry.object_id,
        entry.object_repr,
        entry.patient.mrn if entry.patient else "",
        entry.message,
        entry.ip_address or "",
    ]


class AuditLogListView(RoleRequiredMixin, ListView):
    allowed_roles = AUDIT_ROLES
    template_name = "audit/log_list.html"
    context_object_name = "entries"
    paginate_by = 50

    def get_queryset(self):
        self.filter_form = AuditFilterForm(self.request.GET or None)
        if not self.request.GET:
            return audit_entries()
        if not self.filter_form.is_valid():
            return AuditLog.objects.none()  # errors are shown next to the filters
        return filtered_entries(self.filter_form.cleaned_data)

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            entries = self.get_queryset()[:CSV_MAX_ROWS]
            return csv_response("audit-log.csv", CSV_HEADER, (csv_row(e) for e in entries))
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        context["csv_max_rows"] = CSV_MAX_ROWS
        return context


class AuditLogDetailView(RoleRequiredMixin, TemplateView):
    allowed_roles = AUDIT_ROLES
    template_name = "audit/log_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        entry = get_object_or_404(audit_entries(), pk=self.kwargs["pk"])
        context["entry"] = entry
        context["changes"] = [(field, old, new) for field, (old, new) in entry.changes.items()]
        return context
