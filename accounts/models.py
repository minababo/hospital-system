from django.contrib.auth.models import AbstractUser
from django.contrib.auth.models import UserManager as DjangoUserManager
from django.db import models


class Role(models.TextChoices):
    ADMIN = "ADMIN", "Admin"
    DOCTOR = "DOCTOR", "Doctor"
    NURSE = "NURSE", "Nurse"
    RECEPTIONIST = "RECEPTIONIST", "Receptionist"
    LAB_STAFF = "LAB_STAFF", "Lab Staff"
    PHARMACIST = "PHARMACIST", "Pharmacist"
    ACCOUNTANT = "ACCOUNTANT", "Accountant"


class UserManager(DjangoUserManager):
    def create_user(self, username, email=None, password=None, **extra_fields):
        if not extra_fields.get("role"):
            raise ValueError("Users must have a role.")
        return super().create_user(username, email, password, **extra_fields)

    def create_superuser(self, username, email=None, password=None, **extra_fields):
        # Lets `manage.py createsuperuser` work without asking for a role.
        extra_fields.setdefault("role", Role.ADMIN)
        return super().create_superuser(username, email, password, **extra_fields)


class User(AbstractUser):
    """Custom user model, set up before the first migration so fields (e.g. role) can be
    added later without swapping AUTH_USER_MODEL mid-project."""

    role = models.CharField(max_length=20, choices=Role.choices)

    objects = UserManager()

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=Role.values),
                name="accounts_user_role_valid",
            ),
        ]

    def __str__(self):
        return self.get_full_name() or self.username
