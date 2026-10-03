from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import FormView, ListView, UpdateView

from accounts.permissions import RoleRequiredMixin
from common.forms import add_service_errors
from pharmacy import selectors, services
from pharmacy.forms import MedicineFilterForm, MedicineForm
from pharmacy.models import Medicine
from pharmacy.permissions import MANAGE_MEDICINES


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
