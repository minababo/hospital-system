import pytest
from django.core.exceptions import ValidationError

from accounts import services
from accounts.models import Role


def test_create_user(admin_user_obj):
    user = services.create_user(
        username="newnurse",
        password="Str0ng-Passw0rd!",
        role=Role.NURSE,
        first_name="Nimali",
        last_name="Perera",
        email="nimali@example.com",
        acting_user=admin_user_obj,
    )

    assert user.role == Role.NURSE
    assert user.check_password("Str0ng-Passw0rd!")


def test_update_user_changes_role(admin_user_obj, make_user):
    user = make_user(role=Role.NURSE)

    services.update_user(user, acting_user=admin_user_obj, role=Role.DOCTOR)

    user.refresh_from_db()
    assert user.role == Role.DOCTOR


def test_update_user_rejects_unknown_fields(admin_user_obj, make_user):
    with pytest.raises(ValueError):
        services.update_user(make_user(), acting_user=admin_user_obj, is_superuser=True)


def test_admin_cannot_remove_own_admin_role(admin_user_obj):
    with pytest.raises(ValidationError, match="Admin role"):
        services.update_user(admin_user_obj, acting_user=admin_user_obj, role=Role.DOCTOR)

    admin_user_obj.refresh_from_db()
    assert admin_user_obj.role == Role.ADMIN


def test_admin_cannot_deactivate_self_via_update(admin_user_obj):
    with pytest.raises(ValidationError, match="deactivate"):
        services.update_user(admin_user_obj, acting_user=admin_user_obj, is_active=False)


def test_set_user_password(admin_user_obj, make_user):
    user = make_user()

    services.set_user_password(user, "Brand-N3w-Passw0rd", acting_user=admin_user_obj)

    user.refresh_from_db()
    assert user.check_password("Brand-N3w-Passw0rd")


def test_set_user_active(admin_user_obj, make_user):
    user = make_user()

    services.set_user_active(user, False, acting_user=admin_user_obj)

    user.refresh_from_db()
    assert user.is_active is False


def test_admin_cannot_deactivate_self(admin_user_obj):
    with pytest.raises(ValidationError, match="deactivate"):
        services.set_user_active(admin_user_obj, False, acting_user=admin_user_obj)

    admin_user_obj.refresh_from_db()
    assert admin_user_obj.is_active is True
