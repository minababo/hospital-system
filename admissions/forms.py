from django import forms
from django.utils import timezone

from admissions.models import Bed, DischargeType, ProgressNote, Source, Ward
from admissions.selectors import free_beds_by_ward
from appointments.models import Appointment
from doctors.selectors import active_doctors
from patients.models import Patient

# <input type="datetime-local"> sends "2026-10-05T14:30" with no time zone. Django reads
# it in the current time zone (Asia/Colombo) and stores an aware datetime.
DATETIME_FORMAT = "%Y-%m-%dT%H:%M"


def datetime_field(label):
    return forms.DateTimeField(
        label=label,
        input_formats=[DATETIME_FORMAT],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=DATETIME_FORMAT),
    )


def now_to_the_minute():
    return timezone.localtime().replace(second=0, microsecond=0)


class FreeBedField(forms.ChoiceField):
    """A select of free beds grouped by ward (<optgroup> per ward); cleans to a Bed."""

    def __init__(self, *args, exclude_bed=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.choices = [
            (ward.name, [(bed.pk, bed.bed_number) for bed in beds])
            for ward, beds in free_beds_by_ward(exclude_bed=exclude_bed)
        ]

    def clean(self, value):
        value = super().clean(value)
        return Bed.objects.select_related("ward").get(pk=value)


class WardForm(forms.ModelForm):
    class Meta:
        model = Ward
        fields = ("name", "ward_type", "department", "daily_rate")


class BedForm(forms.ModelForm):
    class Meta:
        model = Bed
        fields = ("bed_number",)


class AdmitForm(forms.Form):
    patient = forms.ModelChoiceField(queryset=Patient.objects.all(), widget=forms.HiddenInput)
    appointment = forms.ModelChoiceField(
        queryset=Appointment.objects.all(), widget=forms.HiddenInput, required=False
    )
    admitting_doctor = forms.ModelChoiceField(queryset=active_doctors(), label="Admitting doctor")
    source = forms.ChoiceField(choices=Source.choices)
    reason = forms.CharField(label="Reason for admission", widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["bed"] = FreeBedField(label="Bed")
        self.fields["admitted_at"] = datetime_field("Admitted at")
        self.fields["admitted_at"].initial = now_to_the_minute()


class TransferForm(forms.Form):
    def __init__(self, *args, current_bed=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_bed"] = FreeBedField(label="Move to bed", exclude_bed=current_bed)
        self.fields["transferred_at"] = datetime_field("Moved at")
        self.fields["transferred_at"].initial = now_to_the_minute()


class ProgressNoteForm(forms.ModelForm):
    class Meta:
        model = ProgressNote
        fields = ("note_type", "text")
        widgets = {"text": forms.Textarea(attrs={"rows": 3})}


class DischargeForm(forms.Form):
    discharge_type = forms.ChoiceField(choices=DischargeType.choices, label="Discharge type")
    discharge_summary = forms.CharField(widget=forms.Textarea(attrs={"rows": 6}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["discharged_at"] = datetime_field("Discharged at")
        self.fields["discharged_at"].initial = now_to_the_minute()


class AdmissionFilterForm(forms.Form):
    ward = forms.ModelChoiceField(
        queryset=Ward.objects.filter(is_active=True), required=False, empty_label="All wards"
    )
    q = forms.CharField(required=False)
    date_from = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
    date_to = forms.DateField(
        required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
    )
