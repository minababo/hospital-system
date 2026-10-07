from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView, UpdateView

from accounts.models import Role
from accounts.permissions import RoleRequiredMixin, user_has_role
from appointments.permissions import BOOK_ROLES
from appointments.selectors import patient_appointments
from audit.services import Action, log_action
from common.forms import add_service_errors
from common.urls import redirect_to_section
from patients import selectors, services
from patients.forms import DocumentUploadForm, PatientForm
from patients.models import Patient
from records.permissions import VIEW_RECORDS

# Same as billing.permissions.VIEW_BILLING. Copied, not imported, because the patients
# app must not depend on billing (a test in billing checks they stay equal).
BILLING_VIEW_ROLES = (Role.ADMIN, Role.ACCOUNTANT, Role.RECEPTIONIST)
# Same as laboratory.permissions.VIEW_LAB, copied for the same reason (checked by a test).
LAB_VIEW_ROLES = (Role.ADMIN, Role.DOCTOR, Role.NURSE, Role.LAB_STAFF)
EDIT_ROLES = (Role.ADMIN, Role.RECEPTIONIST)
VIEW_ROLES = (Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR, Role.NURSE)
HISTORY_ROLES = (Role.ADMIN, Role.DOCTOR, Role.NURSE)
DOC_DELETE_ROLES = (Role.ADMIN,)


class PatientListView(RoleRequiredMixin, ListView):
    allowed_roles = VIEW_ROLES
    template_name = "patients/patient_list.html"
    context_object_name = "patients"
    paginate_by = 20

    def get_queryset(self):
        return selectors.search_patients(self.request.GET.get("q"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["q"] = self.request.GET.get("q", "")
        context["can_edit"] = user_has_role(self.request.user, *EDIT_ROLES)
        return context


class PatientCreateView(RoleRequiredMixin, FormView):
    allowed_roles = EDIT_ROLES
    form_class = PatientForm
    template_name = "patients/patient_form.html"
    success_url = reverse_lazy("patients:patient_list")

    def form_valid(self, form):
        try:
            patient = services.register_patient(
                data=form.cleaned_data, acting_user=self.request.user
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(
            self.request, f"{patient.full_name} was registered with MRN {patient.mrn}."
        )
        return redirect("patients:patient_detail", pk=patient.pk)


class PatientUpdateView(RoleRequiredMixin, UpdateView):
    allowed_roles = EDIT_ROLES
    model = Patient
    form_class = PatientForm
    template_name = "patients/patient_form.html"

    def form_valid(self, form):
        try:
            services.update_patient(
                self.object, data=form.cleaned_data, acting_user=self.request.user
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, f"{self.object.full_name}'s details were updated.")
        return redirect("patients:patient_detail", pk=self.object.pk)


class PatientDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = VIEW_ROLES
    model = Patient
    template_name = "patients/patient_detail.html"
    context_object_name = "patient"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["documents"] = self.object.documents.select_related("uploaded_by")
        context["upload_form"] = DocumentUploadForm()
        context["can_edit"] = user_has_role(user, *EDIT_ROLES)
        context["can_view_history"] = user_has_role(user, *HISTORY_ROLES)
        context["can_delete_documents"] = user_has_role(user, *DOC_DELETE_ROLES)
        context["appointments"] = patient_appointments(self.object)
        context["can_book"] = user_has_role(user, *BOOK_ROLES)
        context["can_view_treatment"] = user_has_role(user, *VIEW_RECORDS)
        context["can_view_billing"] = user_has_role(user, *BILLING_VIEW_ROLES)
        context["can_view_lab"] = user_has_role(user, *LAB_VIEW_ROLES)
        # Same as audit.views.AUDIT_ROLES (admin only); the link is just a URL, no import.
        context["can_view_audit"] = user_has_role(user, Role.ADMIN)
        return context


class PatientHistoryView(RoleRequiredMixin, DetailView):
    allowed_roles = HISTORY_ROLES
    model = Patient
    template_name = "patients/patient_history.html"
    context_object_name = "patient"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["events"] = selectors.patient_history(self.object)
        return context


@method_decorator(require_POST, name="dispatch")
class DocumentUploadView(RoleRequiredMixin, View):
    allowed_roles = VIEW_ROLES

    def post(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        form = DocumentUploadForm(request.POST, request.FILES)
        # Errors are shown as messages on the detail page (redirect after POST),
        # which avoids rebuilding the whole detail page here.
        if not form.is_valid():
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, f"Upload failed: {error}")
            return redirect_to_section("patients:patient_detail", "documents", patient.pk)

        try:
            document = services.upload_document(
                patient=patient,
                file=form.cleaned_data["file"],
                category=form.cleaned_data["category"],
                description=form.cleaned_data["description"],
                acting_user=request.user,
            )
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, f"Upload failed: {message}")
        else:
            messages.success(request, f"{document.original_name} was uploaded.")
        return redirect_to_section("patients:patient_detail", "documents", patient.pk)


class DocumentView(RoleRequiredMixin, View):
    """Streams a document from storage after the role check. Storage URLs are never
    shown to users, so this is the only way to read a file."""

    allowed_roles = VIEW_ROLES

    def get(self, request, pk, doc_pk):
        document = selectors.get_patient_document(pk, doc_pk)
        download = request.GET.get("download") == "1"
        response = FileResponse(
            document.file.open("rb"),
            as_attachment=download,
            filename=document.original_name,
            content_type=document.content_type,
        )
        # Reading a patient's file is recorded too (after it opened successfully).
        log_action(
            actor=request.user,
            action=Action.VIEW,
            event="patients.document.downloaded" if download else "patients.document.viewed",
            obj=document,
            patient=document.patient,
            message=document.original_name,
        )
        response["Cache-Control"] = "private, no-store"
        response["X-Content-Type-Options"] = "nosniff"
        return response


@method_decorator(require_POST, name="dispatch")
class DocumentDeleteView(RoleRequiredMixin, View):
    allowed_roles = DOC_DELETE_ROLES

    def post(self, request, pk, doc_pk):
        document = selectors.get_patient_document(pk, doc_pk)
        name = document.original_name
        services.delete_document(document, acting_user=request.user)
        messages.success(request, f"{name} was deleted.")
        return redirect_to_section("patients:patient_detail", "documents", pk)
