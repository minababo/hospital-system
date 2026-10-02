from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import Role, User

# Audit logging of these actions will be added in these services (audit app).

UPDATABLE_FIELDS = {"first_name", "last_name", "email", "role", "is_active"}


DOCTOR_ROLE_MESSAGE = "Doctor accounts are managed under Doctors."


@transaction.atomic
def create_user(
    *, username, password, role, first_name, last_name, email, acting_user, allow_doctor=False
):
    # A DOCTOR user must always have a Doctor profile, so only doctors.services
    # (which creates both in one transaction) passes allow_doctor=True.
    if role == Role.DOCTOR and not allow_doctor:
        raise ValidationError(DOCTOR_ROLE_MESSAGE)
    return User.objects.create_user(
        username=username,
        password=password,
        role=role,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )


def update_user(user, *, acting_user, **fields):
    unknown = set(fields) - UPDATABLE_FIELDS
    if unknown:
        raise ValueError(f"Cannot update fields: {', '.join(sorted(unknown))}")

    if user.pk == acting_user.pk:
        if fields.get("is_active") is False:
            raise ValidationError("You cannot deactivate your own account.")
        if "role" in fields and fields["role"] != Role.ADMIN:
            raise ValidationError("You cannot remove the Admin role from your own account.")

    if "role" in fields:
        # Compare with the saved role: a ModelForm may already have changed user.role in memory.
        saved_role = User.objects.filter(pk=user.pk).values_list("role", flat=True).first()
        if (saved_role == Role.DOCTOR) != (fields["role"] == Role.DOCTOR):
            raise ValidationError(
                f"{DOCTOR_ROLE_MESSAGE} A user cannot be changed to or from the Doctor role."
            )

    with transaction.atomic():
        for name, value in fields.items():
            setattr(user, name, value)
        user.full_clean()
        user.save()
    return user


def set_user_password(user, password, *, acting_user):
    with transaction.atomic():
        user.set_password(password)
        user.save(update_fields=["password"])
    return user


def set_user_active(user, active, *, acting_user):
    if not active and user.pk == acting_user.pk:
        raise ValidationError("You cannot deactivate your own account.")

    with transaction.atomic():
        user.is_active = active
        user.save(update_fields=["is_active"])
    return user
