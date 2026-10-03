from django import forms

from pharmacy.models import Medicine


class MedicineForm(forms.ModelForm):
    class Meta:
        model = Medicine
        fields = ("name", "generic_name", "strength", "form")


class MedicineFilterForm(forms.Form):
    ACTIVE_CHOICES = [("", "Any status"), ("true", "Active"), ("false", "Inactive")]

    search = forms.CharField(required=False)
    form = forms.ChoiceField(required=False, choices=[("", "All forms"), *Medicine.Form.choices])
    is_active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)

    def clean_is_active(self):
        return {"true": True, "false": False}.get(self.cleaned_data["is_active"])
