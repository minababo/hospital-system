from django import forms

from laboratory.models import (
    LabTest,
    LabTestParameter,
    OrderStatus,
    Priority,
    Section,
    format_decimal,
)
from laboratory.selectors import orderable_tests
from patients.models import Patient


class LabTestForm(forms.ModelForm):
    class Meta:
        model = LabTest
        fields = (
            "code",
            "name",
            "section",
            "specimen_type",
            "price",
            "turnaround_hours",
            "description",
        )
        widgets = {"description": forms.Textarea(attrs={"rows": 2})}


class LabTestParameterForm(forms.ModelForm):
    class Meta:
        model = LabTestParameter
        fields = ("name", "unit", "result_type", "ref_low", "ref_high", "ref_text", "display_order")


class LabTestFilterForm(forms.Form):
    ACTIVE_CHOICES = [("", "Any status"), ("true", "Active"), ("false", "Inactive")]

    search = forms.CharField(required=False)
    section = forms.ChoiceField(required=False, choices=[("", "All sections"), *Section.choices])
    is_active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)

    def clean_is_active(self):
        return {"true": True, "false": False}.get(self.cleaned_data["is_active"])


class LabOrderForm(forms.Form):
    """The test checkboxes are drawn by the template, grouped by section; this form
    validates the posted ids against the orderable tests."""

    tests = forms.ModelMultipleChoiceField(
        queryset=orderable_tests(), widget=forms.CheckboxSelectMultiple
    )
    priority = forms.ChoiceField(choices=Priority.choices, initial=Priority.ROUTINE)
    clinical_notes = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 2}), label="Clinical notes"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tests"].error_messages["required"] = "Select at least one test."


class WalkInOrderForm(LabOrderForm):
    patient = forms.ModelChoiceField(queryset=Patient.objects.all(), widget=forms.HiddenInput)
    referred_by = forms.CharField(
        max_length=150, label="Referred by", help_text="Referring doctor or clinic"
    )


class ResultEntryForm(forms.Form):
    """One input per parameter of the test, built when the form is created.

    The fields can't be declared on the class because every test has different
    parameters, so __init__ adds them: a DecimalField for numbers or a CharField for
    text, named "param_<parameter id>".
    """

    def __init__(self, *args, item, **kwargs):
        super().__init__(*args, **kwargs)
        self.item = item
        existing = {result.parameter_id: result for result in item.results.all()}
        self.parameters = list(item.test.parameters.all())
        for parameter in self.parameters:
            result = existing.get(parameter.pk)
            reference = f"ref {parameter.reference_display}"
            help_text = f"{parameter.unit} · {reference}" if parameter.unit else reference
            if parameter.result_type == LabTestParameter.ResultType.NUMERIC:
                field = forms.DecimalField(
                    max_digits=12,
                    decimal_places=3,
                    required=False,
                    label=parameter.name,
                    help_text=help_text,
                    initial=format_decimal(result.value_numeric) if result else None,
                )
            else:
                field = forms.CharField(
                    max_length=255,
                    required=False,
                    label=parameter.name,
                    help_text=help_text,
                    initial=result.value_text if result else "",
                )
            self.fields[f"param_{parameter.pk}"] = field
        self.fields["comment"] = forms.CharField(
            required=False,
            widget=forms.Textarea(attrs={"rows": 2}),
            label="Interpretation / comment",
            initial=item.comment,
        )

    def values(self):
        """{parameter_id: cleaned value} for laboratory.services.save_results."""
        return {p.pk: self.cleaned_data.get(f"param_{p.pk}") for p in self.parameters}


class WorklistFilterForm(forms.Form):
    STATUS_CHOICES = [("", "Open (to do)"), ("ALL", "All statuses"), *OrderStatus.choices]

    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)
    priority = forms.ChoiceField(required=False, choices=[("", "Any priority"), *Priority.choices])
    date = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    q = forms.CharField(required=False)
    patient = forms.ModelChoiceField(
        queryset=Patient.objects.all(), required=False, widget=forms.HiddenInput
    )
    mine = forms.BooleanField(required=False, label="My orders")


class CancelForm(forms.Form):
    reason = forms.CharField(max_length=255)
