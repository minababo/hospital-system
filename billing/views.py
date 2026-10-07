from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from accounts.permissions import RoleRequiredMixin, user_has_role
from billing import selectors, services
from billing.forms import (
    ChargeEditForm,
    ChargeVoidForm,
    DiscountForm,
    InvoiceCreateForm,
    InvoiceFilterForm,
    ManualChargeForm,
    PaymentForm,
    VoidForm,
)
from billing.models import Charge, InvoiceStatus, Payment
from billing.permissions import ACCOUNTS, CASHIER, VIEW_BILLING, VOID_CHARGE, VOID_MONEY
from common.forms import add_service_errors
from patients.models import Patient
from patients.selectors import search_patients


def flash_errors(request, error):
    for message in error.messages:
        messages.error(request, message)


def flash_form_errors(request, form):
    for field, errors in form.errors.items():
        label = form.fields[field].label if field in form.fields else ""
        for error in errors:
            messages.error(request, f"{label}: {error}" if label else error)


def is_possible_duplicate(error):
    return getattr(error, "code", None) == "possible_duplicate"


def duplicate_matches(patient, form):
    """The charges the duplicate warning is about, listed above the "Add anyway" box."""
    data = form.cleaned_data
    return services.find_possible_duplicates(
        patient=patient,
        charge_type=data["charge_type"],
        description=data["description"],
        unit_price=data["unit_price"],
    )


def charge_return_url(charge):
    """Where to go after editing or voiding a charge: its invoice, or the patient's
    billing page for an unbilled charge."""
    if charge.invoice_id:
        return redirect("billing:invoice_detail", pk=charge.invoice_id)
    return redirect("billing:patient_billing", pk=charge.patient_id)


# --- Pages -------------------------------------------------------------------


class InvoiceListView(RoleRequiredMixin, ListView):
    allowed_roles = VIEW_BILLING
    template_name = "billing/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = InvoiceFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.invoice_list(**self.filter_form.cleaned_data)
        return selectors.invoice_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class PatientSearchView(RoleRequiredMixin, View):
    allowed_roles = VIEW_BILLING

    def get(self, request):
        query = request.GET.get("q", "").strip()
        patients = search_patients(query)[:20] if query else []
        return render(request, "billing/patient_search.html", {"q": query, "patients": patients})


def render_patient_billing(request, patient, charge_form=None, duplicates=None):
    user = request.user
    context = {
        "patient": patient,
        **selectors.patient_billing_summary(patient),
        "charge_form": charge_form or ManualChargeForm(),
        "duplicates": duplicates,
        "invoice_form": InvoiceCreateForm(patient=patient),
        "can_cashier": user_has_role(user, *CASHIER),
        "can_void_charge": user_has_role(user, *VOID_CHARGE),
        "can_edit_charge": user_has_role(user, *ACCOUNTS),
    }
    return render(request, "billing/patient_billing.html", context)


class PatientBillingView(RoleRequiredMixin, View):
    allowed_roles = VIEW_BILLING

    def get(self, request, pk):
        return render_patient_billing(request, get_object_or_404(Patient, pk=pk))


def render_invoice_detail(request, invoice, charge_form=None, duplicates=None):
    user = request.user
    is_draft = invoice.status == InvoiceStatus.DRAFT
    can_edit_draft = is_draft and user_has_role(user, *ACCOUNTS)
    context = {
        "invoice": invoice,
        "charges": [c for c in invoice.charges.all() if not c.is_voided],
        "payments": invoice.payments.all(),
        "charge_form": charge_form or ManualChargeForm(),
        "duplicates": duplicates,
        "discount_form": DiscountForm(
            initial={"amount": invoice.discount, "reason": invoice.discount_reason}
        ),
        "payment_form": PaymentForm(initial={"amount": invoice.balance}),
        "void_form": VoidForm(),
        "can_edit_draft": can_edit_draft,
        # Edit/void links per line: only while the invoice is a draft.
        "can_void_charge": is_draft and user_has_role(user, *VOID_CHARGE),
        "can_issue": is_draft and user_has_role(user, *CASHIER),
        "can_take_payment": invoice.status in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID)
        and user_has_role(user, *CASHIER),
        "can_void": user_has_role(user, *VOID_MONEY),
    }
    return render(request, "billing/invoice_detail.html", context)


class InvoiceDetailView(RoleRequiredMixin, View):
    allowed_roles = VIEW_BILLING

    def get(self, request, pk):
        return render_invoice_detail(request, selectors.get_invoice(pk))


class InvoicePrintView(RoleRequiredMixin, View):
    allowed_roles = VIEW_BILLING

    def get(self, request, pk):
        invoice = selectors.get_invoice(pk)
        charges = [c for c in invoice.charges.all() if not c.is_voided]
        payments = [p for p in invoice.payments.all() if not p.is_voided]
        return render(
            request,
            "billing/invoice_print.html",
            {"invoice": invoice, "charges": charges, "payments": payments},
        )


class ReceiptView(RoleRequiredMixin, View):
    allowed_roles = VIEW_BILLING

    def get(self, request, pk):
        payment = get_object_or_404(
            Payment.objects.select_related("invoice__patient", "received_by"), pk=pk
        )
        return render(
            request,
            "billing/receipt.html",
            {
                "payment": payment,
                "invoice": payment.invoice,
                "balance_after": selectors.balance_after_payment(payment),
            },
        )


# --- Actions (POST only) -----------------------------------------------------


@method_decorator(require_POST, name="dispatch")
class ActionView(RoleRequiredMixin, View):
    """Base for billing buttons: run a service, show errors as messages, redirect."""

    def run(self, action, success_message):
        try:
            result = action()
        except ValidationError as error:
            flash_errors(self.request, error)
            return None
        messages.success(
            self.request, success_message(result) if callable(success_message) else success_message
        )
        return result


class PatientChargeAddView(ActionView):
    allowed_roles = CASHIER

    def post(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        form = ManualChargeForm(request.POST)
        if not form.is_valid():
            flash_form_errors(request, form)
            return redirect("billing:patient_billing", pk=patient.pk)
        try:
            services.add_manual_charge(
                patient=patient, acting_user=request.user, **form.cleaned_data
            )
        except ValidationError as error:
            if is_possible_duplicate(error):
                # Show the page again with the matches and the "Add anyway" checkbox.
                return render_patient_billing(
                    request, patient, form, duplicate_matches(patient, form)
                )
            flash_errors(request, error)
        else:
            messages.success(request, "Charge added.")
        return redirect("billing:patient_billing", pk=patient.pk)


class InvoiceCreateView(ActionView):
    allowed_roles = CASHIER

    def post(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        form = InvoiceCreateForm(request.POST, patient=patient)
        if not form.is_valid():
            flash_form_errors(request, form)
            return redirect("billing:patient_billing", pk=patient.pk)
        invoice = self.run(
            lambda: services.create_invoice(
                patient=patient,
                charge_ids=[charge.pk for charge in form.cleaned_data["charges"]],
                acting_user=request.user,
            ),
            lambda invoice: f"Draft invoice {invoice.number} created.",
        )
        if invoice is None:
            return redirect("billing:patient_billing", pk=patient.pk)
        return redirect("billing:invoice_detail", pk=invoice.pk)


class InvoiceActionView(ActionView):
    def post(self, request, pk, **kwargs):
        invoice = selectors.get_invoice(pk)
        self.perform(invoice, **kwargs)
        return redirect("billing:invoice_detail", pk=invoice.pk)


class InvoiceChargeAddView(ActionView):
    allowed_roles = ACCOUNTS

    def post(self, request, pk):
        invoice = selectors.get_invoice(pk)
        form = ManualChargeForm(request.POST)
        if not form.is_valid():
            flash_form_errors(request, form)
            return redirect("billing:invoice_detail", pk=invoice.pk)
        try:
            services.add_manual_charge(
                patient=invoice.patient,
                invoice=invoice,
                acting_user=request.user,
                **form.cleaned_data,
            )
        except ValidationError as error:
            if is_possible_duplicate(error):
                return render_invoice_detail(
                    request, invoice, form, duplicate_matches(invoice.patient, form)
                )
            flash_errors(request, error)
        else:
            messages.success(request, "Charge added to the invoice.")
        return redirect("billing:invoice_detail", pk=invoice.pk)


class InvoiceChargeRemoveView(InvoiceActionView):
    allowed_roles = ACCOUNTS

    def perform(self, invoice, charge_pk):
        charge = get_object_or_404(Charge, pk=charge_pk, invoice=invoice)
        self.run(
            lambda: services.remove_charge_from_invoice(
                invoice, charge, acting_user=self.request.user
            ),
            "Charge removed from the invoice. It is unbilled again.",
        )


class DiscountView(InvoiceActionView):
    allowed_roles = ACCOUNTS

    def perform(self, invoice):
        form = DiscountForm(self.request.POST)
        if not form.is_valid():
            flash_form_errors(self.request, form)
            return
        self.run(
            lambda: services.set_discount(
                invoice,
                amount=form.cleaned_data["amount"],
                reason=form.cleaned_data["reason"],
                acting_user=self.request.user,
            ),
            "Discount saved.",
        )


class IssueView(InvoiceActionView):
    allowed_roles = CASHIER

    def perform(self, invoice):
        self.run(
            lambda: services.issue_invoice(invoice, acting_user=self.request.user),
            f"Invoice {invoice.number} issued.",
        )


class PaymentAddView(InvoiceActionView):
    allowed_roles = CASHIER

    def perform(self, invoice):
        form = PaymentForm(self.request.POST)
        if not form.is_valid():
            flash_form_errors(self.request, form)
            return
        self.run(
            lambda: services.record_payment(
                invoice=invoice, acting_user=self.request.user, **form.cleaned_data
            ),
            lambda payment: f"Payment received. Receipt {payment.receipt_number}.",
        )


class InvoiceVoidView(InvoiceActionView):
    allowed_roles = VOID_MONEY

    def perform(self, invoice):
        self.run(
            lambda: services.void_invoice(
                invoice, reason=self.request.POST.get("reason", ""), acting_user=self.request.user
            ),
            f"Invoice {invoice.number} voided. Its charges are unbilled again.",
        )


class PaymentVoidView(ActionView):
    allowed_roles = VOID_MONEY

    def post(self, request, pk):
        payment = get_object_or_404(Payment, pk=pk)
        self.run(
            lambda: services.void_payment(
                payment, reason=request.POST.get("reason", ""), acting_user=request.user
            ),
            f"Payment {payment.receipt_number} voided.",
        )
        return redirect("billing:invoice_detail", pk=payment.invoice_id)


# --- Charge correction pages (GET shows the form, POST saves) -------------------


class ChargeActionPage(RoleRequiredMixin, View):
    """Edit or void one charge on its own page, so it works without JavaScript and
    the form is never nested inside the "Create invoice" form."""

    template_name = "billing/charge_action.html"

    def get_charge(self, pk):
        return get_object_or_404(Charge.objects.select_related("patient", "invoice"), pk=pk)

    def get(self, request, pk):
        charge = self.get_charge(pk)
        blocked = self.blocked_reason(charge)
        if blocked:
            messages.error(request, blocked)
            return charge_return_url(charge)
        return self.render_page(charge, self.make_form(charge))

    def post(self, request, pk):
        charge = self.get_charge(pk)
        form = self.make_form(charge, request.POST)
        if form.is_valid():
            try:
                self.save(charge, form)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, self.success_message)
                return charge_return_url(charge)
        return self.render_page(charge, form)

    def render_page(self, charge, form):
        return render(
            self.request,
            self.template_name,
            {"charge": charge, "form": form, **self.page_text},
        )


class ChargeEditView(ChargeActionPage):
    allowed_roles = ACCOUNTS
    success_message = "Charge corrected."
    page_text = {"title": "Correct a charge", "button": "Save correction", "danger": False}

    def blocked_reason(self, charge):
        return services.edit_blocked_reason(charge)

    def make_form(self, charge, data=None):
        return ChargeEditForm.for_charge(charge, data)

    def save(self, charge, form):
        services.edit_charge(charge, acting_user=self.request.user, **form.cleaned_data)


class ChargeVoidView(ChargeActionPage):
    allowed_roles = VOID_CHARGE
    success_message = "Charge voided."
    page_text = {"title": "Void a charge", "button": "Void charge", "danger": True}

    def blocked_reason(self, charge):
        return services.void_blocked_reason(charge)

    def make_form(self, charge, data=None):
        return ChargeVoidForm(data)

    def save(self, charge, form):
        services.void_charge(
            charge, reason=form.cleaned_data["reason"], acting_user=self.request.user
        )
