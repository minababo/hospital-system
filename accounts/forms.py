from django import forms
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm

from accounts.models import Role, User

REQUIRED_PROFILE_FIELDS = ("first_name", "last_name", "email")


def _without_doctor(choices):
    return [(value, label) for value, label in choices if value != Role.DOCTOR]


class UniqueEmailMixin:
    """Emails must be unique ignoring case (Alice@x.com == alice@x.com)."""

    def clean_email(self):
        email = self.cleaned_data["email"]
        clash = User.objects.filter(email__iexact=email).exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError("A user with this email already exists.")
        return email


class UserCreateForm(UniqueEmailMixin, UserCreationForm):
    # UserCreationForm gives password1/password2 with Django's password validators
    # and a case-insensitive username uniqueness check.
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "email", "role")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in REQUIRED_PROFILE_FIELDS:
            self.fields[name].required = True
        # Subclasses (e.g. the doctor account form) may leave out the role field.
        if "role" in self.fields:
            self.fields["role"].choices = _without_doctor(self.fields["role"].choices)
            self.fields["role"].help_text = "Doctors are added under Doctors."


class UserUpdateForm(UniqueEmailMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "role", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in REQUIRED_PROFILE_FIELDS:
            self.fields[name].required = True
        role = self.fields["role"]
        if self.instance.role == Role.DOCTOR:
            # Disabled fields ignore submitted data and keep the current value.
            role.disabled = True
            role.help_text = "Doctor role is managed under Doctors."
        else:
            role.choices = _without_doctor(role.choices)


class AdminSetPasswordForm(SetPasswordForm):
    """Admin sets a new password for another user (no old password needed)."""


class UserFilterForm(forms.Form):
    ACTIVE_CHOICES = [("", "Any status"), ("true", "Active"), ("false", "Inactive")]

    search = forms.CharField(required=False)
    role = forms.ChoiceField(required=False, choices=[("", "All roles"), *Role.choices])
    is_active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)

    def clean_is_active(self):
        return {"true": True, "false": False}.get(self.cleaned_data["is_active"])
