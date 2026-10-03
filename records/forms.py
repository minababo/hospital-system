from django import forms

from pharmacy.selectors import active_medicines
from records.models import Diagnosis, MedicalRecord, PrescriptionItem, Vitals
from records.services import VITAL_FIELDS


class MedicalRecordForm(forms.ModelForm):
    class Meta:
        model = MedicalRecord
        fields = (
            "presenting_complaint",
            "clinical_notes",
            "examination_findings",
            "treatment_plan",
            "follow_up_date",
        )
        widgets = {
            "presenting_complaint": forms.Textarea(attrs={"rows": 2}),
            "clinical_notes": forms.Textarea(attrs={"rows": 4}),
            "examination_findings": forms.Textarea(attrs={"rows": 3}),
            "treatment_plan": forms.Textarea(attrs={"rows": 3}),
            "follow_up_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        }


class DiagnosisForm(forms.ModelForm):
    class Meta:
        model = Diagnosis
        fields = ("description", "icd10_code", "diagnosis_type", "notes")


class PrescriptionItemForm(forms.ModelForm):
    # Only shown after the service warns about a matching allergy.
    allergy_override = forms.BooleanField(
        required=False, label="Prescribe anyway (I have checked the allergy)"
    )

    class Meta:
        model = PrescriptionItem
        fields = (
            "medicine",
            "dose",
            "frequency",
            "route",
            "duration_days",
            "quantity",
            "instructions",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["medicine"].queryset = active_medicines()


class VitalsForm(forms.ModelForm):
    class Meta:
        model = Vitals
        fields = VITAL_FIELDS


class AddendumForm(forms.Form):
    text = forms.CharField(label="Addendum", widget=forms.Textarea(attrs={"rows": 3}))


class CancelPrescriptionForm(forms.Form):
    reason = forms.CharField(label="Reason for cancelling", max_length=255)
