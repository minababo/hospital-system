from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST

from accounts.permissions import RoleRequiredMixin
from appointments.models import Status as AppointmentStatus
from appointments.selectors import visible_appointments
from common.forms import add_service_errors
from common.urls import redirect_to_section
from patients.forms import DocumentUploadForm
from patients.models import Patient
from records import selectors, services
from records.forms import (
    AddendumForm,
    CancelPrescriptionForm,
    DiagnosisForm,
    MedicalRecordForm,
    PrescriptionItemForm,
    VitalsForm,
)
from records.models import RecordStatus, Vitals
from records.permissions import DOCTOR_ONLY, RECORD_VITALS, VIEW_RECORDS


def flash_errors(request, error):
    for message in error.messages:
        messages.error(request, message)


class RecordMixin(RoleRequiredMixin):
    """Loads the record through visible_records(), so another doctor's draft is a 404."""

    allowed_roles = VIEW_RECORDS

    def get_record(self):
        return get_object_or_404(selectors.visible_records(self.request.user), pk=self.kwargs["pk"])

    def render_consultation(self, record, **forms):
        user = self.request.user
        is_owner = record.doctor.user_id == user.pk
        prescription = selectors.get_prescription(record)
        context = {
            "record": record,
            "patient": record.patient,
            "is_owner": is_owner,
            # The editable workspace is only for the record's own doctor while it's a draft.
            "editable": is_owner and record.status == RecordStatus.DRAFT,
            "vitals": getattr(record.appointment, "vitals", None),
            "diagnoses": record.diagnoses.all(),
            "prescription": prescription,
            "items": prescription.items.select_related("medicine") if prescription else [],
            "reports": record.reports.select_related("document", "document__uploaded_by"),
            "addenda": record.addenda.select_related("author"),
            "record_form": MedicalRecordForm(instance=record),
            "diagnosis_form": DiagnosisForm(),
            "item_form": PrescriptionItemForm(),
            "report_form": DocumentUploadForm(),
            "addendum_form": AddendumForm(),
            "cancel_form": CancelPrescriptionForm(),
            "show_allergy_override": False,
        }
        context.update(forms)
        return render(self.request, "records/consultation.html", context)

    def back(self, record, section=None):
        """Back to the consultation; with a section, straight to that card (#notes, ...)."""
        if section:
            return redirect_to_section("records:record_detail", section, record.pk)
        return redirect("records:record_detail", pk=record.pk)


class RecordDetailView(RecordMixin, View):
    def get(self, request, pk):
        return self.render_consultation(self.get_record())


@method_decorator(require_POST, name="dispatch")
class StartConsultationView(RoleRequiredMixin, View):
    allowed_roles = DOCTOR_ONLY

    def post(self, request, appt_pk):
        appointment = get_object_or_404(visible_appointments(request.user), pk=appt_pk)
        try:
            record = services.start_consultation(appointment=appointment, acting_user=request.user)
        except ValidationError as error:
            flash_errors(request, error)
            return redirect("appointments:appointment_detail", pk=appointment.pk)
        return redirect("records:record_detail", pk=record.pk)


# --- Consultation actions (POST only, record's own doctor) -------------------


@method_decorator(require_POST, name="dispatch")
class RecordActionView(RecordMixin, View):
    """Base for the consultation buttons. Services check ownership and DRAFT status;
    a doctor posting to someone else's finalized record gets a 403 from the service."""

    allowed_roles = DOCTOR_ONLY

    def post(self, request, pk, **kwargs):
        return self.handle(self.get_record(), **kwargs)

    # The card each consultation form lives in (its id on the page).
    FORM_SECTIONS = {
        "record_form": "notes",
        "diagnosis_form": "diagnoses",
        "item_form": "prescription",
    }

    def form_failed(self, record, form, form_name, error=None, **extra):
        """Re-show the workspace with the bound form and its errors, keeping what the
        doctor typed. If the record is no longer editable, show a message instead."""
        section = self.FORM_SECTIONS.get(form_name)
        if error is not None:
            add_service_errors(error, form)
        if record.status == RecordStatus.DRAFT and record.doctor.user_id == self.request.user.pk:
            # scroll_section marks the card with the errors so the page scrolls to it.
            return self.render_consultation(
                record, **{form_name: form}, scroll_section=section, **extra
            )
        if error is not None:
            flash_errors(self.request, error)
        return self.back(record, section)


class RecordUpdateView(RecordActionView):
    def handle(self, record):
        form = MedicalRecordForm(self.request.POST, instance=record)
        if not form.is_valid():
            return self.form_failed(record, form, "record_form")
        try:
            services.update_record(record, data=form.cleaned_data, acting_user=self.request.user)
        except ValidationError as error:
            record.refresh_from_db()
            return self.form_failed(record, form, "record_form", error)
        messages.success(self.request, "Draft saved.")
        return self.back(record, "notes")


class DiagnosisAddView(RecordActionView):
    def handle(self, record):
        form = DiagnosisForm(self.request.POST)
        if not form.is_valid():
            return self.form_failed(record, form, "diagnosis_form")
        try:
            services.add_diagnosis(record, data=form.cleaned_data, acting_user=self.request.user)
        except ValidationError as error:
            return self.form_failed(record, form, "diagnosis_form", error)
        messages.success(self.request, "Diagnosis added.")
        return self.back(record, "diagnoses")


class DiagnosisRemoveView(RecordActionView):
    def handle(self, record, d_pk):
        diagnosis = get_object_or_404(record.diagnoses, pk=d_pk)
        try:
            services.remove_diagnosis(diagnosis, acting_user=self.request.user)
        except ValidationError as error:
            flash_errors(self.request, error)
        else:
            messages.success(self.request, "Diagnosis removed.")
        return self.back(record, "diagnoses")


class ItemAddView(RecordActionView):
    def handle(self, record):
        form = PrescriptionItemForm(self.request.POST)
        if not form.is_valid():
            return self.form_failed(
                record,
                form,
                "item_form",
                show_allergy_override=bool(form.data.get("allergy_override")),
            )
        data = dict(form.cleaned_data)
        allergy_override = data.pop("allergy_override")
        try:
            services.add_prescription_item(
                record, data=data, allergy_override=allergy_override, acting_user=self.request.user
            )
        except ValidationError as error:
            is_allergy = getattr(error, "code", None) == "allergy"
            return self.form_failed(
                record, form, "item_form", error, show_allergy_override=is_allergy
            )
        messages.success(self.request, f"{data['medicine']} added to the prescription.")
        return self.back(record, "prescription")


class ItemRemoveView(RecordActionView):
    def handle(self, record, i_pk):
        prescription = selectors.get_prescription(record)
        if prescription is None:
            raise Http404
        item = get_object_or_404(prescription.items, pk=i_pk)
        try:
            services.remove_prescription_item(item, acting_user=self.request.user)
        except ValidationError as error:
            flash_errors(self.request, error)
        else:
            messages.success(self.request, "Medicine removed from the prescription.")
        return self.back(record, "prescription")


class ReportUploadView(RecordActionView):
    def handle(self, record):
        form = DocumentUploadForm(self.request.POST, self.request.FILES)
        if not form.is_valid():
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(self.request, f"Upload failed: {error}")
            return self.back(record, "reports")
        try:
            report = services.attach_report(
                record,
                file=form.cleaned_data["file"],
                category=form.cleaned_data["category"],
                description=form.cleaned_data["description"],
                acting_user=self.request.user,
            )
        except ValidationError as error:
            for message in error.messages:
                messages.error(self.request, f"Upload failed: {message}")
        else:
            messages.success(self.request, f"{report.document.original_name} was uploaded.")
        return self.back(record, "reports")


class FinalizeView(RecordActionView):
    def handle(self, record):
        try:
            services.finalize_record(record, acting_user=self.request.user)
        except ValidationError as error:
            flash_errors(self.request, error)
        else:
            messages.success(self.request, "Consultation finalized.")
        return self.back(record)


class AddendumAddView(RecordActionView):
    def handle(self, record):
        form = AddendumForm(self.request.POST)
        try:
            if not form.is_valid():
                raise ValidationError("Please write the addendum.")
            services.add_addendum(
                record, text=form.cleaned_data["text"], acting_user=self.request.user
            )
        except ValidationError as error:
            flash_errors(self.request, error)
        else:
            messages.success(self.request, "Addendum added.")
        return self.back(record, "addenda")


class PrescriptionCancelView(RecordActionView):
    def handle(self, record):
        prescription = selectors.get_prescription(record)
        if prescription is None:
            raise Http404
        try:
            services.cancel_prescription(
                prescription,
                reason=self.request.POST.get("reason", ""),
                acting_user=self.request.user,
            )
        except ValidationError as error:
            flash_errors(self.request, error)
        else:
            messages.success(self.request, "Prescription cancelled.")
        return self.back(record, "prescription")


# --- Vitals, history, print -------------------------------------------------


class VitalsView(RoleRequiredMixin, View):
    allowed_roles = RECORD_VITALS
    template_name = "records/vitals_form.html"

    def get_appointment(self, appt_pk):
        # Doctors only see their own appointments here (others get a 404).
        return get_object_or_404(visible_appointments(self.request.user), pk=appt_pk)

    def get_instance(self, appointment):
        return Vitals.objects.filter(appointment=appointment).first() or Vitals(
            appointment=appointment, patient_id=appointment.patient_id
        )

    def get(self, request, appt_pk):
        appointment = self.get_appointment(appt_pk)
        if appointment.status != AppointmentStatus.CHECKED_IN:
            messages.error(request, "Vitals can only be recorded for a checked-in patient.")
            return redirect("appointments:appointment_detail", pk=appointment.pk)
        form = VitalsForm(instance=self.get_instance(appointment))
        return render(request, self.template_name, {"appointment": appointment, "form": form})

    def post(self, request, appt_pk):
        appointment = self.get_appointment(appt_pk)
        form = VitalsForm(request.POST, instance=self.get_instance(appointment))
        if form.is_valid():
            try:
                services.record_vitals(
                    appointment=appointment, data=form.cleaned_data, acting_user=request.user
                )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, "Vitals saved.")
                return redirect("appointments:appointment_detail", pk=appointment.pk)
        return render(request, self.template_name, {"appointment": appointment, "form": form})


class TreatmentHistoryView(RoleRequiredMixin, View):
    allowed_roles = VIEW_RECORDS

    def get(self, request, patient_pk):
        patient = get_object_or_404(Patient, pk=patient_pk)
        records = selectors.treatment_history(patient, request.user)
        return render(
            request, "records/treatment_history.html", {"patient": patient, "records": records}
        )


class FinalizedRecordMixin(RecordMixin):
    def get_record(self):
        records = selectors.visible_records(self.request.user).filter(status=RecordStatus.FINALIZED)
        return get_object_or_404(records, pk=self.kwargs["pk"])


class PrintRecordView(FinalizedRecordMixin, View):
    def get(self, request, pk):
        record = self.get_record()
        return render(
            request,
            "records/print_record.html",
            {
                "record": record,
                "vitals": getattr(record.appointment, "vitals", None),
                "prescription": selectors.get_prescription(record),
            },
        )


class PrintPrescriptionView(FinalizedRecordMixin, View):
    def get(self, request, pk):
        record = self.get_record()
        prescription = selectors.get_prescription(record)
        if prescription is None:
            raise Http404("This consultation has no prescription.")
        return render(
            request,
            "records/print_prescription.html",
            {"record": record, "prescription": prescription},
        )
