from django import forms
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm

from accounts.models import Role, User

REQUIRED_PROFILE_FIELDS = ("first_name", "last_name", "email")


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


class UserUpdateForm(UniqueEmailMixin, forms.ModelForm):
    class Meta:
        model = User
        fields = ("first_name", "last_name", "email", "role", "is_active")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in REQUIRED_PROFILE_FIELDS:
            self.fields[name].required = True


class AdminSetPasswordForm(SetPasswordForm):
    """Admin sets a new password for another user (no old password needed)."""


class UserFilterForm(forms.Form):
    ACTIVE_CHOICES = [("", "Any status"), ("true", "Active"), ("false", "Inactive")]

    search = forms.CharField(required=False)
    role = forms.ChoiceField(required=False, choices=[("", "All roles"), *Role.choices])
    is_active = forms.ChoiceField(required=False, choices=ACTIVE_CHOICES)

    def clean_is_active(self):
        return {"true": True, "false": False}.get(self.cleaned_data["is_active"])
