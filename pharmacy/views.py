from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import FormView, ListView, UpdateView

from accounts.models import Role
from accounts.permissions import RoleRequiredMixin
from common.forms import add_service_errors
from pharmacy import dispensing, dispensing_selectors, selectors, services
from pharmacy.forms import (
    AdjustStockForm,
    DispenseForm,
    DispensingQueueFilterForm,
    InventoryFilterForm,
    MedicineFilterForm,
    MedicineForm,
    ReceiveStockForm,
)
from pharmacy.models import Medicine, StockBatch
from pharmacy.permissions import DISPENSE, DISPENSE_VIEW, MANAGE_MEDICINES
from records.models import Prescription, PrescriptionStatus


def flash_errors(request, error):
    for message in error.messages:
        messages.error(request, message)


def flash_form_errors(request, form):
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else ""
        for error in errors:
            messages.error(request, f"{label}: {error}" if label else error)


class ManageMixin(RoleRequiredMixin):
    allowed_roles = MANAGE_MEDICINES


class MedicineListView(ManageMixin, ListView):
    template_name = "pharmacy/medicine_list.html"
    context_object_name = "medicines"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = MedicineFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.medicine_list(**self.filter_form.cleaned_data)
        return selectors.medicine_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class MedicineCreateView(ManageMixin, FormView):
    form_class = MedicineForm
    template_name = "pharmacy/medicine_form.html"
    success_url = reverse_lazy("pharmacy:medicine_list")

    def form_valid(self, form):
        try:
            medicine = services.create_medicine(acting_user=self.request.user, **form.cleaned_data)
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, f"{medicine} was added.")
        return redirect(self.success_url)


class MedicineUpdateView(ManageMixin, UpdateView):
    model = Medicine
    form_class = MedicineForm
    template_name = "pharmacy/medicine_form.html"
    success_url = reverse_lazy("pharmacy:medicine_list")

    def form_valid(self, form):
        try:
            services.update_medicine(
                self.object, acting_user=self.request.user, **form.cleaned_data
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, f"{self.object} was updated.")
        return redirect(self.success_url)


@method_decorator(require_POST, name="dispatch")
class MedicineToggleActiveView(ManageMixin, View):
    def post(self, request, pk):
        medicine = get_object_or_404(Medicine, pk=pk)
        services.set_medicine_active(medicine, not medicine.is_active, acting_user=request.user)
        state = "activated" if medicine.is_active else "deactivated"
        messages.success(request, f"{medicine} was {state}.")
        return redirect("pharmacy:medicine_list")


# --- Inventory ---------------------------------------------------------------------


def expiry_warning_until(today):
    """Last date counted as "expiring soon"."""
    return today + timedelta(days=settings.PHARMACY_EXPIRY_WARNING_DAYS)


class InventoryListView(ManageMixin, ListView):
    template_name = "pharmacy/inventory_list.html"
    context_object_name = "medicines"
    paginate_by = 25

    def get_queryset(self):
        self.filter_form = InventoryFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.inventory_list(**self.filter_form.cleaned_data)
        return selectors.inventory_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class InventoryDetailView(ManageMixin, View):
    def get(self, request, medicine_pk):
        medicine = get_object_or_404(Medicine, pk=medicine_pk)
        today = timezone.localdate()
        return render(
            request,
            "pharmacy/inventory_detail.html",
            {
                "medicine": medicine,
                "batches": selectors.batches_for_medicine(medicine),
                "movements": selectors.recent_movements(medicine),
                "usable_stock": selectors.usable_stock(medicine, today),
                "today": today,
                "expiry_warning_until": expiry_warning_until(today),
                "receive_form": ReceiveStockForm(),
                "adjust_form": AdjustStockForm(),
            },
        )


@method_decorator(require_POST, name="dispatch")
class ReceiveStockView(ManageMixin, View):
    def post(self, request, medicine_pk):
        medicine = get_object_or_404(Medicine, pk=medicine_pk)
        form = ReceiveStockForm(request.POST)
        if not form.is_valid():
            flash_form_errors(request, form)
            return redirect("pharmacy:inventory_detail", medicine_pk=medicine.pk)
        try:
            batch = services.receive_stock(
                medicine=medicine, acting_user=request.user, **form.cleaned_data
            )
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(
                request, f"Received {batch.quantity_received} units (batch {batch.batch_number})."
            )
        return redirect("pharmacy:inventory_detail", medicine_pk=medicine.pk)


@method_decorator(require_POST, name="dispatch")
class AdjustStockView(ManageMixin, View):
    def post(self, request, pk):
        batch = get_object_or_404(StockBatch, pk=pk)
        form = AdjustStockForm(request.POST)
        if not form.is_valid():
            flash_form_errors(request, form)
        else:
            try:
                services.adjust_stock(batch=batch, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                flash_errors(request, error)
            else:
                messages.success(request, f"Batch {batch.batch_number} adjusted.")
        return redirect("pharmacy:inventory_detail", medicine_pk=batch.medicine_id)


@method_decorator(require_POST, name="dispatch")
class WriteOffView(ManageMixin, View):
    def post(self, request, pk):
        batch = get_object_or_404(StockBatch, pk=pk)
        try:
            services.write_off_expired(batch=batch, acting_user=request.user)
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(request, f"Expired batch {batch.batch_number} written off.")
        return redirect("pharmacy:inventory_detail", medicine_pk=batch.medicine_id)


class AlertsView(ManageMixin, View):
    def get(self, request):
        today = timezone.localdate()
        context = {
            **selectors.alerts(today),
            "today": today,
            "warning_days": settings.PHARMACY_EXPIRY_WARNING_DAYS,
            "expiry_warning_until": expiry_warning_until(today),
        }
        return render(request, "pharmacy/alerts.html", context)


# --- Dispensing -------------------------------------------------------------------


class DispensingQueueView(RoleRequiredMixin, ListView):
    allowed_roles = DISPENSE_VIEW
    template_name = "pharmacy/dispensing_queue.html"
    context_object_name = "prescriptions"
    paginate_by = 25

    def get_queryset(self):
        self.filter_form = DispensingQueueFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return dispensing_selectors.dispensing_queue(**self.filter_form.cleaned_data)
        return dispensing_selectors.dispensing_queue()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


def get_prescription(pk):
    # Draft prescriptions belong to an unfinished consultation: not the pharmacy's business.
    return get_object_or_404(
        Prescription.objects.select_related("patient", "doctor__user", "record").exclude(
            status=PrescriptionStatus.DRAFT
        ),
        pk=pk,
    )


def render_dispense_page(request, prescription, form=None):
    rows = dispensing_selectors.item_progress(prescription)
    can_dispense = (
        request.user.role == Role.PHARMACIST
        and prescription.status in dispensing_selectors.DISPENSABLE_STATUSES
    )
    form = form or DispenseForm(rows=rows)
    return render(
        request,
        "pharmacy/dispense.html",
        {
            "prescription": prescription,
            "patient": prescription.patient,
            "form": form,
            "lines": [(row, form[dispensing.field_name(row.item.pk)]) for row in rows],
            "can_dispense": can_dispense,
            "dispenses": dispensing_selectors.prescription_dispenses(prescription),
        },
    )


class DispensePageView(RoleRequiredMixin, View):
    allowed_roles = DISPENSE_VIEW

    def get(self, request, pk):
        return render_dispense_page(request, get_prescription(pk))


@method_decorator(require_POST, name="dispatch")
class DispenseView(RoleRequiredMixin, View):
    allowed_roles = DISPENSE

    def post(self, request, pk):
        prescription = get_prescription(pk)
        form = DispenseForm(request.POST, rows=dispensing_selectors.item_progress(prescription))
        if form.is_valid():
            try:
                dispense = dispensing.dispense_prescription(
                    prescription=prescription,
                    quantities=form.quantities(),
                    notes=form.cleaned_data["notes"],
                    acting_user=request.user,
                )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"Dispensed {dispense.number}.")
                return redirect("pharmacy:dispense_page", pk=prescription.pk)
        return render_dispense_page(request, prescription, form)
