from django import forms

from pharmacy.models import Medicine, StockStatus
from records.models import PrescriptionStatus

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class MedicineForm(forms.ModelForm):
    class Meta:
        model = Medicine
        fields = ("name", "generic_name", "strength", "form", "unit_price", "reorder_level")


class MedicineFilterForm(forms.Form):
    ACTIVE_CHOICES = [("", "Any status"), ("true", "Active"), ("false", "Inactive")]

    search = forms.CharField(required=False)
    form = forms.ChoiceField(required=False, choices=[("", "All forms"), *Medicine.Form.choices])
    is_active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)

    def clean_is_active(self):
        return {"true": True, "false": False}.get(self.cleaned_data["is_active"])


class InventoryFilterForm(forms.Form):
    STATUS_CHOICES = [
        ("", "All"),
        (StockStatus.LOW, "Low"),
        (StockStatus.OUT_OF_STOCK, "Out of stock"),
    ]

    search = forms.CharField(required=False)
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)


class ReceiveStockForm(forms.Form):
    batch_number = forms.CharField(max_length=50)
    expiry_date = forms.DateField(widget=DATE_INPUT)
    quantity = forms.IntegerField(min_value=1, help_text="Units received (tablets, bottles, ...)")
    supplier = forms.CharField(max_length=150, required=False)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 2}))


class AdjustStockForm(forms.Form):
    quantity_change = forms.IntegerField(help_text="Use a minus sign to remove units, e.g. -3")
    reason = forms.CharField(max_length=255)


class DispensingQueueFilterForm(forms.Form):
    STATUS_CHOICES = [
        ("", "All waiting"),
        (PrescriptionStatus.ISSUED, "Issued"),
        (PrescriptionStatus.PARTIALLY_DISPENSED, "Partially dispensed"),
    ]

    q = forms.CharField(required=False)
    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)


class DispenseForm(forms.Form):
    """One quantity box per prescription item ("qty_<item id>"), built from the rows
    given by pharmacy.dispensing_selectors.item_progress()."""

    def __init__(self, *args, rows, **kwargs):
        super().__init__(*args, **kwargs)
        self.item_ids = [row.item.pk for row in rows]
        for row in rows:
            self.fields[f"qty_{row.item.pk}"] = forms.IntegerField(
                min_value=0,
                required=False,
                initial=row.default_quantity,
                label=str(row.item.medicine),
            )
        self.fields["notes"] = forms.CharField(
            required=False, widget=forms.Textarea(attrs={"rows": 2})
        )

    def quantities(self):
        return {item_id: self.cleaned_data.get(f"qty_{item_id}") or 0 for item_id in self.item_ids}
