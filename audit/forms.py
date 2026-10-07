import re

from django import forms
from django.contrib.auth import get_user_model

from accounts.models import Role
from audit.models import Action

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")

# The event prefixes ("<app>.<model>.<verb>"). A fixed list because audit must not
# import the other apps; a module missing here can still be found with the event filter.
MODULES = [
    ("accounts", "Accounts"),
    ("doctors", "Doctors"),
    ("patients", "Patients"),
    ("appointments", "Appointments"),
    ("records", "Medical records"),
    ("billing", "Billing"),
    ("laboratory", "Laboratory"),
    ("pharmacy", "Pharmacy"),
    ("admissions", "Admissions"),
    ("staff", "Staff"),
]

# "P000123", "p123" or "123". The MRN is the patient's pk with a P prefix.
MRN_PATTERN = re.compile(r"^[Pp]?0*(\d+)$")


class AuditFilterForm(forms.Form):
    date_from = forms.DateField(required=False, widget=DATE_INPUT, label="From")
    date_to = forms.DateField(required=False, widget=DATE_INPUT, label="To")
    actor = forms.ModelChoiceField(
        queryset=get_user_model().objects.order_by("username"),
        required=False,
        empty_label="Any user",
    )
    role = forms.ChoiceField(required=False, choices=[("", "Any role"), *Role.choices])
    action = forms.ChoiceField(required=False, choices=[("", "Any action"), *Action.choices])
    module = forms.ChoiceField(required=False, choices=[("", "Any module"), *MODULES])
    event = forms.CharField(required=False, max_length=80)
    patient = forms.CharField(required=False, max_length=20, label="Patient MRN")
    # Set by the "Audit trail" link on a patient's page.
    patient_id = forms.IntegerField(required=False, min_value=1, widget=forms.HiddenInput)
    q = forms.CharField(required=False, max_length=100, label="Search")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["actor"].label_from_instance = lambda u: (
            f"{u.get_full_name() or u.username} ({u.username})"
        )

    def clean_patient(self):
        value = self.cleaned_data["patient"].strip()
        if not value:
            return None
        match = MRN_PATTERN.match(value)
        if not match or int(match.group(1)) == 0:
            raise forms.ValidationError("Enter an MRN like P000123.")
        return int(match.group(1))

    def clean(self):
        cleaned = super().clean()
        date_from, date_to = cleaned.get("date_from"), cleaned.get("date_to")
        if date_from and date_to and date_from > date_to:
            self.add_error("date_to", "The end date must be on or after the start date.")
        return cleaned
