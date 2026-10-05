from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import FormView, ListView

from accounts.models import Role
from accounts.permissions import RoleRequiredMixin, user_has_role
from common.forms import add_service_errors
from doctors.models import Doctor
from laboratory import selectors, services
from laboratory.forms import (
    CancelForm,
    LabOrderForm,
    LabTestFilterForm,
    LabTestForm,
    LabTestParameterForm,
    ResultEntryForm,
    WalkInOrderForm,
    WorklistFilterForm,
)
from laboratory.models import LabOrder, LabTest, LabTestParameter, OrderStatus
from laboratory.permissions import (
    CANCEL,
    CATALOG,
    LAB,
    ORDER_FROM_RECORD,
    REPORT,
    VIEW_LAB,
    WALK_IN,
)
from patients.forms import DocumentUploadForm
from patients.models import Patient
from patients.selectors import search_patients
from records.selectors import visible_records


def flash_errors(request, error):
    for message in error.messages:
        messages.error(request, message)


def flash_form_errors(request, form):
    for errors in form.errors.values():
        for error in errors:
            messages.error(request, error)


# --- Worklist ------------------------------------------------------------------------


class WorklistView(RoleRequiredMixin, ListView):
    allowed_roles = REPORT
    template_name = "laboratory/worklist.html"
    context_object_name = "orders"
    paginate_by = 25

    def get_queryset(self):
        user = self.request.user
        self.filter_form = WorklistFilterForm(self.request.GET)
        filters = dict(self.filter_form.cleaned_data) if self.filter_form.is_valid() else {}
        if filters.pop("mine", False) and user.role == Role.DOCTOR:
            own_profile = Doctor.objects.filter(user=user).first()
            if own_profile is None:
                return LabOrder.objects.none()
            filters["ordering_doctor"] = own_profile
        # Reception only sees finished orders, to print their reports.
        if user.role == Role.RECEPTIONIST:
            filters["status"] = OrderStatus.COMPLETED
        return selectors.worklist(**filters)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["filter_form"] = self.filter_form
        context["is_doctor"] = user.role == Role.DOCTOR
        context["is_receptionist"] = user.role == Role.RECEPTIONIST
        context["can_view_orders"] = user_has_role(user, *VIEW_LAB)
        context["can_walk_in"] = user_has_role(user, *WALK_IN)
        return context


# --- Catalog -----------------------------------------------------------------------


class CatalogMixin(RoleRequiredMixin):
    allowed_roles = CATALOG


class LabTestListView(CatalogMixin, ListView):
    template_name = "laboratory/test_list.html"
    context_object_name = "tests"
    paginate_by = 25

    def get_queryset(self):
        self.filter_form = LabTestFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.lab_test_list(**self.filter_form.cleaned_data)
        return selectors.lab_test_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class LabTestCreateView(CatalogMixin, FormView):
    form_class = LabTestForm
    template_name = "laboratory/test_form.html"

    def form_valid(self, form):
        try:
            test = services.create_lab_test(acting_user=self.request.user, **form.cleaned_data)
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, f"{test} was added. Now add its parameters.")
        return redirect("laboratory:test_update", pk=test.pk)


class LabTestUpdateView(CatalogMixin, View):
    """Edit the test and manage its parameters on one page."""

    def get(self, request, pk):
        test = get_object_or_404(LabTest, pk=pk)
        return render_test_page(request, test, LabTestForm(instance=test))

    def post(self, request, pk):
        test = get_object_or_404(LabTest, pk=pk)
        form = LabTestForm(request.POST, instance=test)
        if form.is_valid():
            try:
                services.update_lab_test(test, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"{test} was updated.")
                return redirect("laboratory:test_update", pk=test.pk)
        return render_test_page(request, test, form)


def render_test_page(request, test, form, parameter_form=None):
    """The test edit page: test form, parameter list and "add parameter" form."""
    return render(
        request,
        "laboratory/test_form.html",
        {
            "test": test,
            "form": form,
            "parameters": test.parameters.all(),
            "parameter_form": parameter_form or LabTestParameterForm(),
        },
    )


@method_decorator(require_POST, name="dispatch")
class LabTestToggleActiveView(CatalogMixin, View):
    def post(self, request, pk):
        test = get_object_or_404(LabTest, pk=pk)
        services.set_lab_test_active(test, not test.is_active, acting_user=request.user)
        state = "activated" if test.is_active else "deactivated"
        messages.success(request, f"{test} was {state}.")
        return redirect("laboratory:test_list")


@method_decorator(require_POST, name="dispatch")
class ParameterAddView(CatalogMixin, View):
    def post(self, request, pk):
        test = get_object_or_404(LabTest, pk=pk)
        form = LabTestParameterForm(request.POST)
        if form.is_valid():
            try:
                services.add_parameter(test, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, "Parameter added.")
                return redirect("laboratory:test_update", pk=test.pk)
        # Show the page again with the parameter form's errors.
        return render_test_page(request, test, LabTestForm(instance=test), parameter_form=form)


class ParameterUpdateView(CatalogMixin, View):
    template_name = "laboratory/parameter_form.html"

    def get_parameter(self, pk):
        return get_object_or_404(LabTestParameter.objects.select_related("test"), pk=pk)

    def get(self, request, pk):
        parameter = self.get_parameter(pk)
        form = LabTestParameterForm(instance=parameter)
        return render(request, self.template_name, {"parameter": parameter, "form": form})

    def post(self, request, pk):
        parameter = self.get_parameter(pk)
        form = LabTestParameterForm(request.POST, instance=parameter)
        if form.is_valid():
            try:
                services.update_parameter(parameter, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, "Parameter updated.")
                return redirect("laboratory:test_update", pk=parameter.test_id)
        return render(request, self.template_name, {"parameter": parameter, "form": form})


@method_decorator(require_POST, name="dispatch")
class ParameterRemoveView(CatalogMixin, View):
    def post(self, request, pk):
        parameter = get_object_or_404(LabTestParameter, pk=pk)
        test_id = parameter.test_id
        try:
            services.remove_parameter(parameter, acting_user=request.user)
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(request, "Parameter removed.")
        return redirect("laboratory:test_update", pk=test_id)


# --- Ordering ----------------------------------------------------------------------


class WalkInView(RoleRequiredMixin, View):
    """GET ?q= searches for the patient, GET ?patient=<pk> shows the order form."""

    allowed_roles = WALK_IN
    template_name = "laboratory/walk_in.html"

    def get(self, request):
        patient = self.get_patient(request.GET.get("patient", ""))
        if patient is None:
            query = request.GET.get("q", "").strip()
            return render(
                request,
                self.template_name,
                {"q": query, "patients": search_patients(query)[:20] if query else []},
            )
        return self.render_form(patient, WalkInOrderForm(initial={"patient": patient}))

    def post(self, request):
        form = WalkInOrderForm(request.POST)
        if not form.is_valid():
            patient = self.get_patient(request.POST.get("patient", ""))
            if patient is None:
                return redirect("laboratory:walk_in")
            return self.render_form(patient, form)
        data = form.cleaned_data
        try:
            order = services.create_walk_in_order(
                patient=data["patient"],
                test_ids=[test.pk for test in data["tests"]],
                referred_by=data["referred_by"],
                priority=data["priority"],
                clinical_notes=data["clinical_notes"],
                acting_user=request.user,
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.render_form(data["patient"], form)
        messages.success(request, f"Lab request {order.number} created. It has been billed.")
        if user_has_role(request.user, *VIEW_LAB):
            return redirect("laboratory:order_detail", pk=order.pk)
        return redirect("laboratory:worklist")

    def get_patient(self, patient_id):
        patient_id = str(patient_id)
        return Patient.objects.filter(pk=patient_id).first() if patient_id.isdigit() else None

    def render_form(self, patient, form):
        selected = {str(value) for value in form["tests"].value() or []}
        return render(
            self.request,
            self.template_name,
            {
                "patient": patient,
                "form": form,
                "tests_by_section": selectors.orderable_tests_by_section(),
                "selected": selected,
            },
        )


@method_decorator(require_POST, name="dispatch")
class OrderForRecordView(RoleRequiredMixin, View):
    allowed_roles = ORDER_FROM_RECORD

    def post(self, request, record_pk):
        # visible_records: another doctor's draft is a 404.
        record = get_object_or_404(visible_records(request.user), pk=record_pk)
        form = LabOrderForm(request.POST)
        if not form.is_valid():
            flash_form_errors(request, form)
            return redirect("records:record_detail", pk=record.pk)
        try:
            order = services.order_tests_for_record(
                record=record,
                test_ids=[test.pk for test in form.cleaned_data["tests"]],
                priority=form.cleaned_data["priority"],
                clinical_notes=form.cleaned_data["clinical_notes"],
                acting_user=request.user,
            )
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(request, f"Lab request {order.number} sent to the laboratory.")
        return redirect("records:record_detail", pk=record.pk)


# --- Order detail and lab workflow --------------------------------------------------


class OrderDetailView(RoleRequiredMixin, View):
    allowed_roles = VIEW_LAB

    def get(self, request, pk):
        order = selectors.get_order(pk)
        user = request.user
        is_lab = user_has_role(user, *LAB)
        is_open = order.status in (OrderStatus.REQUESTED, OrderStatus.SAMPLE_COLLECTED)
        own_doctor = order.ordering_doctor_id and order.ordering_doctor.user_id == user.pk
        context = {
            "order": order,
            "items": [(item, selectors.item_rows(item)) for item in order.items.all()],
            "report_form": DocumentUploadForm(),
            "cancel_form": CancelForm(),
            "can_collect": is_lab and order.status == OrderStatus.REQUESTED,
            "can_enter_results": is_lab and order.status == OrderStatus.SAMPLE_COLLECTED,
            "can_release": is_lab and order.status == OrderStatus.SAMPLE_COLLECTED,
            "can_upload": is_lab and order.status != OrderStatus.CANCELLED,
            "can_cancel": is_open and (is_lab or own_doctor),
        }
        return render(request, "laboratory/order_detail.html", context)


RESULTS_CLOSED_MESSAGE = "Results can only be entered after sample collection and before release."


class ResultEntryView(RoleRequiredMixin, View):
    allowed_roles = LAB
    template_name = "laboratory/result_entry.html"

    def get_objects(self, pk, item_pk):
        order = selectors.get_order(pk)
        item = next((item for item in order.items.all() if item.pk == item_pk), None)
        if item is None:
            raise Http404
        return order, item

    def results_closed(self, order):
        """Send the user back to the order instead of showing a form that can't be saved.
        (The service enforces the same rule; this just avoids a dead-end page.)"""
        messages.error(self.request, RESULTS_CLOSED_MESSAGE)
        return redirect("laboratory:order_detail", pk=order.pk)

    def get(self, request, pk, item_pk):
        order, item = self.get_objects(pk, item_pk)
        if order.status != OrderStatus.SAMPLE_COLLECTED:
            return self.results_closed(order)
        form = ResultEntryForm(item=item)
        return render(request, self.template_name, {"order": order, "item": item, "form": form})

    def post(self, request, pk, item_pk):
        order, item = self.get_objects(pk, item_pk)
        if order.status != OrderStatus.SAMPLE_COLLECTED:
            return self.results_closed(order)
        form = ResultEntryForm(request.POST, item=item)
        if form.is_valid():
            try:
                services.save_results(
                    order,
                    item=item,
                    values=form.values(),
                    comment=form.cleaned_data["comment"],
                    acting_user=request.user,
                )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"Results saved for {item.test.name}.")
                return redirect("laboratory:order_detail", pk=order.pk)
        return render(request, self.template_name, {"order": order, "item": item, "form": form})


@method_decorator(require_POST, name="dispatch")
class OrderActionView(RoleRequiredMixin, View):
    """Base for the order buttons: run the service, show the result as a message."""

    allowed_roles = LAB
    success_message = ""

    def post(self, request, pk):
        order = selectors.get_order(pk)
        try:
            self.perform(order)
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(request, self.success_message)
        return redirect("laboratory:order_detail", pk=order.pk)


class CollectView(OrderActionView):
    success_message = "Sample collected."

    def perform(self, order):
        services.collect_sample(
            order, notes=self.request.POST.get("notes", ""), acting_user=self.request.user
        )


class ReleaseView(OrderActionView):
    success_message = "Results released."

    def perform(self, order):
        services.release_results(order, acting_user=self.request.user)


class CancelView(OrderActionView):
    allowed_roles = CANCEL
    success_message = "Order cancelled. Its unpaid charges were voided."

    def perform(self, order):
        services.cancel_order(
            order, reason=self.request.POST.get("reason", ""), acting_user=self.request.user
        )


class ReportUploadView(OrderActionView):
    success_message = "Report uploaded."

    def perform(self, order):
        form = DocumentUploadForm(self.request.POST, self.request.FILES)
        if not form.is_valid():
            raise ValidationError([e for errors in form.errors.values() for e in errors])
        services.attach_lab_report(
            order,
            file=form.cleaned_data["file"],
            description=form.cleaned_data["description"],
            acting_user=self.request.user,
        )


class ReportPrintView(RoleRequiredMixin, View):
    allowed_roles = REPORT

    def get(self, request, pk):
        order = selectors.get_order(pk)
        if order.status != OrderStatus.COMPLETED:
            raise Http404("The report is available once results are released.")
        items = [(item, selectors.item_rows(item)) for item in order.items.all()]
        return render(request, "laboratory/report_print.html", {"order": order, "items": items})
