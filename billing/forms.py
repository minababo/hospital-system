from decimal import Decimal

from django import forms

from billing.models import Charge, ChargeType, InvoiceStatus, PaymentMethod
from billing.selectors import unbilled_charges


class InvoiceCreateForm(forms.Form):
    charges = forms.ModelMultipleChoiceField(
        queryset=Charge.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,  # pending consultations are added even if nothing is ticked
    )

    def __init__(self, *args, patient, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["charges"].queryset = unbilled_charges(patient)
        self.fields["charges"].label_from_instance = lambda charge: (
            f"{charge.description} — Rs. {charge.amount:,.2f}"
        )


class ManualChargeForm(forms.ModelForm):
    class Meta:
        model = Charge
        fields = ("charge_type", "description", "quantity", "unit_price")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Consultations come from completed appointments, not manual entry.
        self.fields["charge_type"].choices = [
            choice for choice in ChargeType.choices if choice[0] != ChargeType.CONSULTATION
        ]


class DiscountForm(forms.Form):
    amount = forms.DecimalField(
        label="Discount (Rs.)", min_value=Decimal("0"), max_digits=12, decimal_places=2
    )
    reason = forms.CharField(max_length=255, required=False)


class PaymentForm(forms.Form):
    amount = forms.DecimalField(
        label="Amount (Rs.)", min_value=Decimal("0.01"), max_digits=12, decimal_places=2
    )
    method = forms.ChoiceField(choices=PaymentMethod.choices)
    reference = forms.CharField(
        max_length=100, required=False, help_text="Required for card, transfer and online"
    )


class VoidForm(forms.Form):
    reason = forms.CharField(label="Reason", max_length=255)


class InvoiceFilterForm(forms.Form):
    status = forms.ChoiceField(required=False, choices=[("", "Any status"), *InvoiceStatus.choices])
    q = forms.CharField(required=False)
    date_from = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    date_to = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    outstanding_only = forms.BooleanField(required=False, label="Outstanding only")
