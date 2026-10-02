from django import forms
from django.db.models import Q

from accounts.forms import REQUIRED_PROFILE_FIELDS, UniqueEmailMixin, UserCreateForm
from accounts.models import User
from doctors.models import Department, Doctor, DoctorSchedule

ACTIVE_CHOICES = [("", "Any status"), ("true", "Active"), ("false", "Inactive")]


def _parse_active(value):
    return {"true": True, "false": False}.get(value)


class DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields = ("name", "description")
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class DepartmentFilterForm(forms.Form):
    search = forms.CharField(required=False)
    is_active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)

    def clean_is_active(self):
        return _parse_active(self.cleaned_data["is_active"])


class DoctorAccountForm(UserCreateForm):
    """The user account part of "Add doctor": same validation as the Users page,
    without the role field (the service sets role=DOCTOR)."""

    class Meta(UserCreateForm.Meta):
        fields = ("username", "first_name", "last_name", "email")


class DoctorUserUpdateForm(UniqueEmailMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = REQUIRED_PROFILE_FIELDS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in REQUIRED_PROFILE_FIELDS:
            self.fields[name].required = True


class DoctorProfileForm(forms.ModelForm):
    class Meta:
        model = Doctor
        fields = (
            "department",
            "specialization",
            "registration_number",
            "qualification",
            "phone",
            "consultation_fee",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Only active departments can be picked, but when editing keep the doctor's
        # current department selectable even if it has since been deactivated.
        active_or_current = Q(is_active=True)
        if self.instance.pk:
            active_or_current |= Q(pk=self.instance.department_id)
        self.fields["department"].queryset = Department.objects.filter(active_or_current)


class DoctorFilterForm(forms.Form):
    search = forms.CharField(required=False)
    department = forms.ModelChoiceField(
        required=False, queryset=Department.objects.all(), empty_label="All departments"
    )
    is_active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)

    def clean_is_active(self):
        return _parse_active(self.cleaned_data["is_active"])


class DoctorScheduleForm(forms.ModelForm):
    class Meta:
        model = DoctorSchedule
        fields = ("weekday", "start_time", "end_time", "slot_minutes", "is_active")
        widgets = {
            "start_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
            "end_time": forms.TimeInput(attrs={"type": "time"}, format="%H:%M"),
        }
