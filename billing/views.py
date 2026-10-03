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
    DiscountForm,
    InvoiceCreateForm,
    InvoiceFilterForm,
    ManualChargeForm,
    PaymentForm,
    VoidForm,
)
from billing.models import Charge, InvoiceStatus, Payment
from billing.permissions import ACCOUNTS, CASHIER, VIEW_BILLING, VOID_CHARGE, VOID_MONEY
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


class PatientBillingView(RoleRequiredMixin, View):
    allowed_roles = VIEW_BILLING

    def get(self, request, pk):
        patient = get_object_or_404(Patient, pk=pk)
        user = request.user
        context = {
            "patient": patient,
            **selectors.patient_billing_summary(patient),
            "charge_form": ManualChargeForm(),
            "invoice_form": InvoiceCreateForm(patient=patient),
            "void_form": VoidForm(),
            "can_cashier": user_has_role(user, *CASHIER),
            "can_void_charge": user_has_role(user, *VOID_CHARGE),
        }
        return render(request, "billing/patient_billing.html", context)


class InvoiceDetailView(RoleRequiredMixin, View):
    allowed_roles = VIEW_BILLING

    def get(self, request, pk):
        invoice = selectors.get_invoice(pk)
        user = request.user
        is_draft = invoice.status == InvoiceStatus.DRAFT
        context = {
            "invoice": invoice,
            "charges": [c for c in invoice.charges.all() if not c.is_voided],
            "payments": invoice.payments.all(),
            "charge_form": ManualChargeForm(),
            "discount_form": DiscountForm(
                initial={"amount": invoice.discount, "reason": invoice.discount_reason}
            ),
            "payment_form": PaymentForm(initial={"amount": invoice.balance}),
            "void_form": VoidForm(),
            "can_edit_draft": is_draft and user_has_role(user, *ACCOUNTS),
            "can_issue": is_draft and user_has_role(user, *CASHIER),
            "can_take_payment": invoice.status
            in (InvoiceStatus.ISSUED, InvoiceStatus.PARTIALLY_PAID)
            and user_has_role(user, *CASHIER),
            "can_void": user_has_role(user, *VOID_MONEY),
        }
        return render(request, "billing/invoice_detail.html", context)


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
        if form.is_valid():
            self.run(
                lambda: services.add_manual_charge(
                    patient=patient, acting_user=request.user, **form.cleaned_data
                ),
                "Charge added.",
            )
        else:
            flash_form_errors(request, form)
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


class ChargeVoidView(ActionView):
    allowed_roles = VOID_CHARGE

    def post(self, request, pk):
        charge = get_object_or_404(Charge, pk=pk)
        invoice_id = charge.invoice_id
        self.run(
            lambda: services.void_charge(
                charge, reason=request.POST.get("reason", ""), acting_user=request.user
            ),
            "Charge voided.",
        )
        if invoice_id:
            return redirect("billing:invoice_detail", pk=invoice_id)
        return redirect("billing:patient_billing", pk=charge.patient_id)


class InvoiceActionView(ActionView):
    def post(self, request, pk, **kwargs):
        invoice = selectors.get_invoice(pk)
        self.perform(invoice, **kwargs)
        return redirect("billing:invoice_detail", pk=invoice.pk)


class InvoiceChargeAddView(InvoiceActionView):
    allowed_roles = ACCOUNTS

    def perform(self, invoice):
        form = ManualChargeForm(self.request.POST)
        if not form.is_valid():
            flash_form_errors(self.request, form)
            return
        self.run(
            lambda: services.add_manual_charge(
                patient=invoice.patient,
                invoice=invoice,
                acting_user=self.request.user,
                **form.cleaned_data,
            ),
            "Charge added to the invoice.",
        )


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
