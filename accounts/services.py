from django.core.exceptions import ValidationError
from django.db import transaction

from accounts.models import Role, User
from audit.services import Action, created_changes, log_action, saved_snapshot, updated_changes

# Fields recorded in the audit log when a user changes (never the password).
USER_AUDIT_FIELDS = ("username", "first_name", "last_name", "email", "role", "is_active")

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
    user = User.objects.create_user(
        username=username,
        password=password,
        role=role,
        first_name=first_name,
        last_name=last_name,
        email=email,
    )
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="accounts.user.created",
        obj=user,
        changes=created_changes(user, USER_AUDIT_FIELDS),
        message=f"Account created with role {user.get_role_display()}",
    )
    return user


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
        before = saved_snapshot(user, USER_AUDIT_FIELDS)
        for name, value in fields.items():
            setattr(user, name, value)
        user.full_clean()
        user.save()
        changes = updated_changes(user, before, USER_AUDIT_FIELDS)
        log_action(
            actor=acting_user,
            action=Action.UPDATE,
            event="accounts.user.role_changed" if "role" in changes else "accounts.user.updated",
            obj=user,
            changes=changes,
        )
    return user


def set_user_password(user, password, *, acting_user):
    with transaction.atomic():
        user.set_password(password)
        user.save(update_fields=["password"])
        # Never the password itself, not even hashed.
        log_action(
            actor=acting_user,
            action=Action.UPDATE,
            event="accounts.user.password_set",
            obj=user,
            message="Password set by an administrator",
        )
    return user


def set_user_active(user, active, *, acting_user):
    if not active and user.pk == acting_user.pk:
        raise ValidationError("You cannot deactivate your own account.")

    with transaction.atomic():
        was_active = User.objects.values_list("is_active", flat=True).get(pk=user.pk)
        user.is_active = active
        user.save(update_fields=["is_active"])
        log_action(
            actor=acting_user,
            action=Action.STATUS_CHANGE,
            event="accounts.user.activated" if active else "accounts.user.deactivated",
            obj=user,
            changes={"is_active": [was_active, active]},
        )
    return user
