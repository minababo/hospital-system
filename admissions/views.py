from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST

from accounts.permissions import RoleRequiredMixin, user_has_role
from admissions import selectors, services
from admissions.forms import (
    AdmissionFilterForm,
    AdmitForm,
    BedForm,
    DischargeForm,
    ProgressNoteForm,
    TransferForm,
    WardForm,
)
from admissions.models import AdmissionStatus, Bed, BedAssignment, Source, Ward
from admissions.permissions import ADMIT, CARE, CLINICAL, DISCHARGE, VIEW, WARDS
from appointments.models import Appointment
from common.forms import add_service_errors
from patients.models import Patient
from patients.selectors import search_patients


def flash_errors(request, error):
    for message in error.messages:
        messages.error(request, message)


# --- Lists ----------------------------------------------------------------------------


class AdmissionListView(RoleRequiredMixin, View):
    """Three tabs: current inpatients, discharged, and today's outpatients."""

    allowed_roles = VIEW
    TABS = ("inpatients", "discharged", "outpatients")

    def get(self, request):
        tab = request.GET.get("tab", "inpatients")
        if tab not in self.TABS:
            tab = "inpatients"
        form = AdmissionFilterForm(request.GET)
        filters = form.cleaned_data if form.is_valid() else {}
        context = {"tab": tab, "form": form, "can_admit": user_has_role(request.user, *ADMIT)}
        if tab == "inpatients":
            context["admissions"] = selectors.current_admissions(
                ward=filters.get("ward"), q=filters.get("q")
            )
        elif tab == "discharged":
            context["admissions"] = selectors.discharged_admissions(
                date_from=filters.get("date_from"),
                date_to=filters.get("date_to"),
                q=filters.get("q"),
            )
        else:
            context["appointments"] = selectors.outpatients_today(request.user)
        return render(request, "admissions/admission_list.html", context)


class BedBoardView(RoleRequiredMixin, View):
    allowed_roles = VIEW

    def get(self, request):
        return render(
            request,
            "admissions/bed_board.html",
            {"board": selectors.bed_board(), "summary": selectors.occupancy_summary()},
        )


# --- Admitting ----------------------------------------------------------------------


def get_id(value):
    return int(value) if str(value).isdigit() else None


class AdmitView(RoleRequiredMixin, View):
    """GET ?q= finds the patient, GET ?patient=<pk>[&appointment=<pk>] shows the form."""

    allowed_roles = ADMIT
    template_name = "admissions/admit.html"

    def get(self, request):
        patient = Patient.objects.filter(pk=get_id(request.GET.get("patient", ""))).first()
        if patient is None:
            query = request.GET.get("q", "").strip()
            return render(
                request,
                self.template_name,
                {"q": query, "patients": search_patients(query)[:20] if query else []},
            )
        appointment = Appointment.objects.filter(
            pk=get_id(request.GET.get("appointment", "")), patient=patient
        ).first()
        initial = {"patient": patient, "appointment": appointment, "source": Source.DIRECT}
        if appointment:
            initial.update(source=Source.OPD, admitting_doctor=appointment.doctor)
        return self.render_form(patient, appointment, AdmitForm(initial=initial))

    def post(self, request):
        form = AdmitForm(request.POST)
        patient = Patient.objects.filter(pk=get_id(request.POST.get("patient", ""))).first()
        if patient is None:
            return redirect("admissions:admit")
        if form.is_valid():
            data = form.cleaned_data
            try:
                admission = services.admit_patient(
                    patient=data["patient"],
                    bed=data["bed"],
                    admitting_doctor=data["admitting_doctor"],
                    reason=data["reason"],
                    source=data["source"],
                    appointment=data["appointment"],
                    admitted_at=data["admitted_at"],
                    acting_user=request.user,
                )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(
                    request, f"{patient.full_name} admitted ({admission.number}) to {data['bed']}."
                )
                return redirect("admissions:admission_detail", pk=admission.pk)
        appointment = form.cleaned_data.get("appointment")
        return self.render_form(patient, appointment, form)

    def render_form(self, patient, appointment, form):
        return render(
            self.request,
            self.template_name,
            {
                "patient": patient,
                "appointment": appointment,
                "current": selectors.patient_current_admission(patient),
                "form": form,
            },
        )


# --- Admission detail and actions ------------------------------------------------------


def detail_context(request, admission):
    user = request.user
    is_admitted = admission.status == AdmissionStatus.ADMITTED
    now = timezone.now()
    return {
        "admission": admission,
        "patient": admission.patient,
        "assignments": [(a, a.nights(now)) for a in admission.assignments.all()],
        "length_of_stay": admission.length_of_stay_days(now),
        "show_notes": user_has_role(user, *CLINICAL),
        "can_care": is_admitted and user_has_role(user, *CARE),
        "can_discharge": is_admitted and user_has_role(user, *DISCHARGE),
        "can_print": not is_admitted and user_has_role(user, *CLINICAL),
        "note_form": ProgressNoteForm(),
    }


class AdmissionDetailView(RoleRequiredMixin, View):
    allowed_roles = VIEW

    def get(self, request, pk):
        admission = selectors.get_admission(pk)
        return render(
            request, "admissions/admission_detail.html", detail_context(request, admission)
        )


class TransferView(RoleRequiredMixin, View):
    allowed_roles = CARE
    template_name = "admissions/transfer.html"

    def get(self, request, pk):
        admission = selectors.get_admission(pk)
        if admission.status != AdmissionStatus.ADMITTED:
            messages.error(request, "Only current inpatients can be moved.")
            return redirect("admissions:admission_detail", pk=pk)
        form = TransferForm(current_bed=admission.current_bed)
        return render(request, self.template_name, {"admission": admission, "form": form})

    def post(self, request, pk):
        admission = selectors.get_admission(pk)
        form = TransferForm(request.POST, current_bed=admission.current_bed)
        if form.is_valid():
            try:
                services.transfer_bed(
                    admission,
                    new_bed=form.cleaned_data["new_bed"],
                    transferred_at=form.cleaned_data["transferred_at"],
                    acting_user=request.user,
                )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"Moved to {form.cleaned_data['new_bed']}.")
                return redirect("admissions:admission_detail", pk=pk)
        return render(request, self.template_name, {"admission": admission, "form": form})


@method_decorator(require_POST, name="dispatch")
class NoteAddView(RoleRequiredMixin, View):
    allowed_roles = CARE

    def post(self, request, pk):
        admission = selectors.get_admission(pk)
        form = ProgressNoteForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
            return redirect("admissions:admission_detail", pk=pk)
        try:
            services.add_progress_note(
                admission,
                note_type=form.cleaned_data["note_type"],
                text=form.cleaned_data["text"],
                acting_user=request.user,
            )
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(request, "Note added.")
        return redirect("admissions:admission_detail", pk=pk)


class DischargeView(RoleRequiredMixin, View):
    allowed_roles = DISCHARGE
    template_name = "admissions/discharge.html"

    def get(self, request, pk):
        admission = selectors.get_admission(pk)
        if admission.status != AdmissionStatus.ADMITTED:
            messages.error(request, "This patient has already been discharged.")
            return redirect("admissions:admission_detail", pk=pk)
        return render(
            request, self.template_name, {"admission": admission, "form": DischargeForm()}
        )

    def post(self, request, pk):
        admission = selectors.get_admission(pk)
        form = DischargeForm(request.POST)
        if form.is_valid():
            try:
                services.discharge_patient(admission, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"{admission.patient.full_name} was discharged.")
                return redirect("admissions:admission_detail", pk=pk)
        return render(request, self.template_name, {"admission": admission, "form": form})


class DischargeSummaryView(RoleRequiredMixin, View):
    allowed_roles = CLINICAL

    def get(self, request, pk):
        admission = selectors.get_admission(pk)
        if admission.status != AdmissionStatus.DISCHARGED:
            raise Http404("The discharge summary is available after discharge.")
        return render(
            request, "admissions/discharge_summary.html", detail_context(request, admission)
        )


# --- Wards and beds ------------------------------------------------------------------


class WardsMixin(RoleRequiredMixin):
    allowed_roles = WARDS


class WardListView(WardsMixin, View):
    def get(self, request):
        return render(request, "admissions/ward_list.html", {"wards": selectors.ward_list()})


class WardFormView(WardsMixin, View):
    """Create (no pk) or edit a ward."""

    template_name = "admissions/ward_form.html"

    def get(self, request, pk=None):
        ward = get_object_or_404(Ward, pk=pk) if pk else None
        return render(request, self.template_name, {"ward": ward, "form": WardForm(instance=ward)})

    def post(self, request, pk=None):
        ward = get_object_or_404(Ward, pk=pk) if pk else None
        form = WardForm(request.POST, instance=ward)
        if form.is_valid():
            try:
                if ward:
                    services.update_ward(ward, acting_user=request.user, **form.cleaned_data)
                else:
                    ward = services.create_ward(acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"Ward {ward.name} saved.")
                return redirect("admissions:bed_list", pk=ward.pk)
        return render(request, self.template_name, {"ward": ward, "form": form})


@method_decorator(require_POST, name="dispatch")
class WardToggleView(WardsMixin, View):
    def post(self, request, pk):
        ward = get_object_or_404(Ward, pk=pk)
        try:
            services.set_ward_active(ward, not ward.is_active, acting_user=request.user)
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(
                request, f"Ward {ward.name} {'activated' if ward.is_active else 'deactivated'}."
            )
        return redirect("admissions:ward_list")


def render_bed_list(request, ward, form):
    occupied = set(
        BedAssignment.objects.filter(bed__ward=ward, ended_at__isnull=True).values_list(
            "bed_id", flat=True
        )
    )
    return render(
        request,
        "admissions/bed_list.html",
        {
            "ward": ward,
            "beds": ward.beds.order_by("bed_number"),
            "occupied": occupied,
            "form": form,
        },
    )


class BedListView(WardsMixin, View):
    def get(self, request, pk):
        return render_bed_list(request, get_object_or_404(Ward, pk=pk), BedForm())


@method_decorator(require_POST, name="dispatch")
class BedAddView(WardsMixin, View):
    def post(self, request, pk):
        ward = get_object_or_404(Ward, pk=pk)
        form = BedForm(request.POST, instance=Bed(ward=ward))
        if form.is_valid():
            try:
                bed = services.add_bed(ward, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"Bed {bed.bed_number} added.")
                return redirect("admissions:bed_list", pk=ward.pk)
        return render_bed_list(request, ward, form)


class BedUpdateView(WardsMixin, View):
    template_name = "admissions/bed_form.html"

    def get(self, request, pk):
        bed = get_object_or_404(Bed.objects.select_related("ward"), pk=pk)
        return render(request, self.template_name, {"bed": bed, "form": BedForm(instance=bed)})

    def post(self, request, pk):
        bed = get_object_or_404(Bed.objects.select_related("ward"), pk=pk)
        form = BedForm(request.POST, instance=bed)
        if form.is_valid():
            try:
                services.update_bed(bed, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"Bed {bed.bed_number} saved.")
                return redirect("admissions:bed_list", pk=bed.ward_id)
        return render(request, self.template_name, {"bed": bed, "form": form})


@method_decorator(require_POST, name="dispatch")
class BedToggleView(WardsMixin, View):
    def post(self, request, pk):
        bed = get_object_or_404(Bed, pk=pk)
        try:
            bed = services.set_bed_active(bed, not bed.is_active, acting_user=request.user)
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(
                request, f"Bed {bed.bed_number} {'activated' if bed.is_active else 'deactivated'}."
            )
        return redirect("admissions:bed_list", pk=bed.ward_id)
