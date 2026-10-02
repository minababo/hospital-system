from django import forms

from common.validators import normalize_nic
from patients.models import Patient, PatientDocument


class PatientForm(forms.ModelForm):
    class Meta:
        model = Patient
        fields = (
            "first_name",
            "last_name",
            "date_of_birth",
            "gender",
            "nic",
            "phone",
            "email",
            "address",
            "blood_group",
            "allergies",
            "emergency_contact_name",
            "emergency_contact_phone",
        )
        widgets = {
            "date_of_birth": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
            "address": forms.Textarea(attrs={"rows": 2}),
            "allergies": forms.Textarea(attrs={"rows": 2}),
        }

    # Allow spaces while typing (e.g. "2000 1234 5678"); stored without them.
    nic = forms.CharField(label="NIC", max_length=20, required=False)

    def clean_nic(self):
        return normalize_nic(self.cleaned_data["nic"]) or None


class DocumentUploadForm(forms.Form):
    """Basic checks only; services.upload_document does the real file validation."""

    file = forms.FileField(widget=forms.FileInput(attrs={"accept": ".pdf,.jpg,.jpeg,.png"}))
    category = forms.ChoiceField(choices=PatientDocument.Category.choices)
    description = forms.CharField(max_length=255, required=False)
