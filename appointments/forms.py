from django import forms

from appointments.models import Status
from doctors.models import Doctor
from doctors.selectors import active_departments, active_doctors
from patients.models import Patient

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class DoctorChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, doctor):
        return f"{doctor} ({doctor.specialization}, {doctor.department.name})"


def slot_choices(slots):
    """Radio options from available_slots(): value "09:15", label "09:15–09:30"."""
    return [(f"{start:%H:%M}", f"{start:%H:%M}–{end:%H:%M}") for start, end in slots]


class BookingSelectionForm(forms.Form):
    """Step 2 of booking (sent with GET): pick department, doctor and date."""

    patient = forms.ModelChoiceField(queryset=Patient.objects.all(), widget=forms.HiddenInput)
    department = forms.ModelChoiceField(
        queryset=active_departments(), required=False, empty_label="All departments"
    )
    doctor = DoctorChoiceField(
        queryset=active_doctors(), required=False, empty_label="Choose a doctor"
    )
    date = forms.DateField(required=False, widget=DATE_INPUT)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        department = self.data.get("department")
        if department and str(department).isdigit():
            self.fields["doctor"].queryset = active_doctors(department=department)


class BookingConfirmForm(forms.Form):
    """Step 3 (POST): the chosen slot and reason. Slot availability is checked again by
    services.book_appointment, so a stale page can't double-book."""

    patient = forms.ModelChoiceField(queryset=Patient.objects.all(), widget=forms.HiddenInput)
    doctor = forms.ModelChoiceField(queryset=active_doctors(), widget=forms.HiddenInput)
    date = forms.DateField(widget=forms.HiddenInput)
    start_time = forms.TimeField(label="Time", widget=forms.RadioSelect)
    reason = forms.CharField(label="Reason for visit", max_length=255)

    def __init__(self, *args, slots=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_time"].widget.choices = slot_choices(slots)
        self.fields["start_time"].error_messages["required"] = "Please choose a time slot."


class DatePickForm(forms.Form):
    date = forms.DateField(required=False, widget=DATE_INPUT)


class RescheduleForm(forms.Form):
    date = forms.DateField(widget=forms.HiddenInput)
    start_time = forms.TimeField(label="New time", widget=forms.RadioSelect)

    def __init__(self, *args, slots=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_time"].widget.choices = slot_choices(slots)
        self.fields["start_time"].error_messages["required"] = "Please choose a time slot."


class CancelForm(forms.Form):
    reason = forms.CharField(label="Reason for cancelling", max_length=255)


class AppointmentFilterForm(forms.Form):
    date = forms.DateField(required=False, widget=DATE_INPUT)
    doctor = DoctorChoiceField(
        queryset=Doctor.objects.select_related("user", "department"),
        required=False,
        empty_label="All doctors",
    )
    status = forms.ChoiceField(required=False, choices=[("", "Any status"), *Status.choices])
    q = forms.CharField(required=False)
