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


# Consultations come from completed appointments, never from manual entry or edits.
MANUAL_CHARGE_TYPES = [
    choice for choice in ChargeType.choices if choice[0] != ChargeType.CONSULTATION
]


class ManualChargeForm(forms.ModelForm):
    # Shown only after the service reports a possible duplicate (see the templates).
    confirm_duplicate = forms.BooleanField(
        required=False, label="Add anyway (this is a separate charge, not a repeat)"
    )

    class Meta:
        model = Charge
        fields = ("charge_type", "description", "quantity", "unit_price")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["charge_type"].choices = MANUAL_CHARGE_TYPES


class ChargeEditForm(forms.Form):
    """A plain form (not a ModelForm), so the charge object isn't changed before
    billing.services.edit_charge locks the row and checks it."""

    description = forms.CharField(max_length=255)
    charge_type = forms.ChoiceField(choices=MANUAL_CHARGE_TYPES, label="Type")
    quantity = forms.IntegerField(min_value=1)
    unit_price = forms.DecimalField(
        label="Unit price (Rs.)", min_value=Decimal("0"), max_digits=10, decimal_places=2
    )
    reason = forms.CharField(max_length=200, label="Reason for the correction")

    @classmethod
    def for_charge(cls, charge, data=None):
        initial = {
            "description": charge.description,
            "charge_type": charge.charge_type,
            "quantity": charge.quantity,
            "unit_price": charge.unit_price,
        }
        return cls(data, initial=initial)


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


class ChargeVoidForm(forms.Form):
    reason = forms.CharField(label="Reason for voiding", max_length=255)


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
