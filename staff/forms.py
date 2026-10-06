from django import forms
from django.contrib.auth import get_user_model
from django.forms import formset_factory

from common.validators import normalize_nic
from doctors.models import Department
from staff.models import (
    AttendanceStatus,
    Employee,
    EmployeeStatus,
    LeaveRequest,
    LeaveStatus,
    StaffCategory,
)
from staff.selectors import users_without_employee

DATE_INPUT = forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")
TIME_INPUT = forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class EmployeeForm(forms.ModelForm):
    class Meta:
        model = Employee
        fields = (
            "first_name",
            "last_name",
            "nic",
            "phone",
            "email",
            "address",
            "designation",
            "category",
            "employment_type",
            "department",
            "date_joined",
            "user",
        )
        widgets = {
            "date_joined": DATE_INPUT,
            "address": forms.Textarea(attrs={"rows": 2}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only logins not linked elsewhere, plus the one this employee already has.
        users = users_without_employee()
        if self.instance.user_id:
            users = users | get_user_model().objects.filter(pk=self.instance.user_id)
        self.fields["user"].queryset = users.order_by("first_name", "last_name", "username")
        self.fields["user"].required = False
        self.fields["department"].queryset = Department.objects.filter(is_active=True)

    # Allow spaces while typing (e.g. "2000 1234 5678"); stored without them.
    nic = forms.CharField(label="NIC", max_length=20, required=False)

    def clean_nic(self):
        return normalize_nic(self.cleaned_data["nic"]) or None


class EndEmploymentForm(forms.Form):
    status = forms.ChoiceField(
        label="Reason",
        choices=[
            (EmployeeStatus.RESIGNED, "Resigned"),
            (EmployeeStatus.TERMINATED, "Terminated"),
        ],
    )
    end_date = forms.DateField(label="Last working day", widget=DATE_INPUT)


class EmployeeFilterForm(forms.Form):
    q = forms.CharField(required=False)
    department = forms.ModelChoiceField(
        queryset=Department.objects.all(), required=False, empty_label="All departments"
    )
    category = forms.ChoiceField(
        required=False, choices=[("", "All categories"), *StaffCategory.choices]
    )
    status = forms.ChoiceField(
        required=False, choices=[("", "Any status"), *EmployeeStatus.choices]
    )


class AttendanceRowForm(forms.Form):
    """One employee's row on the daily sheet. The formset repeats it per employee."""

    employee_id = forms.IntegerField(widget=forms.HiddenInput)
    status = forms.ChoiceField(
        required=False, choices=[("", "—"), *AttendanceStatus.choices], label="Status"
    )
    check_in = forms.TimeField(required=False, widget=TIME_INPUT, label="In")
    check_out = forms.TimeField(required=False, widget=TIME_INPUT, label="Out")
    notes = forms.CharField(required=False, max_length=255)


# extra=0: exactly one form per row given in `initial` (one per employee), no blanks.
AttendanceFormSet = formset_factory(AttendanceRowForm, extra=0)


class LeaveRequestForm(forms.ModelForm):
    """Self-service: the employee is the logged-in user, so there's no employee field."""

    class Meta:
        model = LeaveRequest
        fields = ("leave_type", "start_date", "end_date", "reason")
        widgets = {
            "start_date": DATE_INPUT,
            "end_date": DATE_INPUT,
            "reason": forms.Textarea(attrs={"rows": 2}),
        }


class LeaveRecordForm(LeaveRequestForm):
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.filter(status=EmployeeStatus.ACTIVE).order_by(
            "first_name", "last_name"
        )
    )
    confirm_doctor_conflicts = forms.BooleanField(
        required=False, label="Record anyway (the doctor's appointments are not cancelled)"
    )

    class Meta(LeaveRequestForm.Meta):
        fields = ("employee", *LeaveRequestForm.Meta.fields)


class DecisionForm(forms.Form):
    note = forms.CharField(required=False, max_length=255, label="Note (required when rejecting)")
    confirm_doctor_conflicts = forms.BooleanField(
        required=False, label="Approve anyway (the doctor's appointments are not cancelled)"
    )


class LeaveFilterForm(forms.Form):
    STATUS_CHOICES = [("", "Pending"), ("ALL", "All statuses"), *LeaveStatus.choices]

    status = forms.ChoiceField(required=False, choices=STATUS_CHOICES)
    employee = forms.ModelChoiceField(
        queryset=Employee.objects.order_by("first_name", "last_name"),
        required=False,
        empty_label="All employees",
    )
    date_from = forms.DateField(required=False, widget=DATE_INPUT)
    date_to = forms.DateField(required=False, widget=DATE_INPUT)


class DayForm(forms.Form):
    date = forms.DateField(required=False)


class MonthForm(forms.Form):
    month = forms.DateField(required=False, input_formats=["%Y-%m"])
