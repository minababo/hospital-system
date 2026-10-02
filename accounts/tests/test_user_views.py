import pytest
from django.urls import reverse

from accounts.models import Role, User

NON_ADMIN_ROLES = [role for role in Role if role != Role.ADMIN]

# (url name, needs a user pk, HTTP method, status the admin should get)
USER_MANAGEMENT_URLS = [
    ("accounts:user_list", False, "get", 200),
    ("accounts:user_create", False, "get", 200),
    ("accounts:user_update", True, "get", 200),
    ("accounts:user_password", True, "get", 200),
    ("accounts:user_toggle_active", True, "post", 302),
]


@pytest.fixture
def target_user(make_user):
    return make_user(role=Role.RECEPTIONIST)


def build_url(name, needs_pk, user):
    return reverse(name, args=[user.pk]) if needs_pk else reverse(name)


def valid_create_data(**overrides):
    data = {
        "username": "newdoc",
        "first_name": "Kamal",
        "last_name": "Fernando",
        "email": "kamal@example.com",
        "role": Role.DOCTOR,
        "password1": "Very-Str0ng-Passw0rd",
        "password2": "Very-Str0ng-Passw0rd",
    }
    data.update(overrides)
    return data


# --- RBAC -------------------------------------------------------------------


@pytest.mark.parametrize(("name", "needs_pk", "method", "_"), USER_MANAGEMENT_URLS)
def test_anonymous_is_redirected_to_login(client, target_user, name, needs_pk, method, _):
    response = getattr(client, method)(build_url(name, needs_pk, target_user))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", NON_ADMIN_ROLES)
@pytest.mark.parametrize(("name", "needs_pk", "method", "_"), USER_MANAGEMENT_URLS)
def test_non_admin_roles_get_403(client_for_role, target_user, role, name, needs_pk, method, _):
    client = client_for_role(role)

    response = getattr(client, method)(build_url(name, needs_pk, target_user))

    assert response.status_code == 403


@pytest.mark.parametrize(("name", "needs_pk", "method", "expected"), USER_MANAGEMENT_URLS)
def test_admin_has_access(client, admin_user_obj, target_user, name, needs_pk, method, expected):
    client.force_login(admin_user_obj)

    response = getattr(client, method)(build_url(name, needs_pk, target_user))

    assert response.status_code == expected


# --- List -------------------------------------------------------------------


def test_list_filters_by_search(client, admin_user_obj, make_user):
    make_user(username="findme")
    make_user(username="other")
    client.force_login(admin_user_obj)

    response = client.get(reverse("accounts:user_list"), {"search": "findme"})

    assert [u.username for u in response.context["users"]] == ["findme"]


def test_list_is_paginated(client, admin_user_obj, make_user):
    for _ in range(25):
        make_user()
    client.force_login(admin_user_obj)

    response = client.get(reverse("accounts:user_list"))

    assert len(response.context["users"]) == 20
    assert response.context["page_obj"].paginator.num_pages == 2


# --- Create -----------------------------------------------------------------


@pytest.fixture
def logged_in_admin(client, admin_user_obj):
    client.force_login(admin_user_obj)
    return client


def test_create_valid_user(logged_in_admin):
    response = logged_in_admin.post(reverse("accounts:user_create"), valid_create_data())

    assert response.status_code == 302
    user = User.objects.get(username="newdoc")
    assert user.role == Role.DOCTOR
    assert user.check_password("Very-Str0ng-Passw0rd")


@pytest.mark.parametrize(
    ("overrides", "error_field"),
    [
        ({"username": "ADMIN1"}, "username"),  # duplicate, different case
        ({"email": "ADMIN1@example.com"}, "email"),  # duplicate, different case
        ({"password2": "Something-Else-123"}, "password2"),
        ({"password1": "password", "password2": "password"}, "password2"),
        ({"role": "JANITOR"}, "role"),
        ({"first_name": ""}, "first_name"),
        ({"email": ""}, "email"),
    ],
    ids=[
        "duplicate-username",
        "duplicate-email",
        "password-mismatch",
        "weak-password",
        "invalid-role",
        "missing-first-name",
        "missing-email",
    ],
)
def test_create_rejects_invalid_input(logged_in_admin, overrides, error_field):
    response = logged_in_admin.post(reverse("accounts:user_create"), valid_create_data(**overrides))

    assert response.status_code == 200
    assert error_field in response.context["form"].errors
    assert not User.objects.filter(username="newdoc").exists()


# --- Edit -------------------------------------------------------------------


def test_edit_changes_role(logged_in_admin, target_user):
    response = logged_in_admin.post(
        reverse("accounts:user_update", args=[target_user.pk]),
        {
            "first_name": target_user.first_name,
            "last_name": target_user.last_name,
            "email": target_user.email,
            "role": Role.PHARMACIST,
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    target_user.refresh_from_db()
    assert target_user.role == Role.PHARMACIST


def test_edit_rejects_email_used_by_someone_else(logged_in_admin, target_user, make_user):
    other = make_user(email="taken@example.com")

    response = logged_in_admin.post(
        reverse("accounts:user_update", args=[target_user.pk]),
        {
            "first_name": "A",
            "last_name": "B",
            "email": other.email.upper(),
            "role": target_user.role,
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    assert "email" in response.context["form"].errors


def test_admin_cannot_remove_own_admin_role_via_view(logged_in_admin, admin_user_obj):
    response = logged_in_admin.post(
        reverse("accounts:user_update", args=[admin_user_obj.pk]),
        {
            "first_name": admin_user_obj.first_name,
            "last_name": admin_user_obj.last_name,
            "email": admin_user_obj.email,
            "role": Role.DOCTOR,
            "is_active": "on",
        },
    )

    assert response.status_code == 200
    assert "Admin role" in str(response.context["form"].non_field_errors())
    admin_user_obj.refresh_from_db()
    assert admin_user_obj.role == Role.ADMIN


# --- Password reset ---------------------------------------------------------


def test_admin_resets_password_then_user_can_log_in(logged_in_admin, target_user):
    client = logged_in_admin
    new_password = "Reset-Passw0rd-2026"

    response = client.post(
        reverse("accounts:user_password", args=[target_user.pk]),
        {"new_password1": new_password, "new_password2": new_password},
    )
    assert response.status_code == 302

    client.logout()
    response = client.post(
        reverse("accounts:login"), {"username": target_user.username, "password": new_password}
    )
    assert response.status_code == 302
    assert response.url == "/dashboard/"


# --- Activate / deactivate --------------------------------------------------


def test_deactivated_user_cannot_log_in(logged_in_admin, target_user, user_password):
    client = logged_in_admin
    client.post(reverse("accounts:user_toggle_active", args=[target_user.pk]))
    target_user.refresh_from_db()
    assert target_user.is_active is False

    client.logout()
    response = client.post(
        reverse("accounts:login"), {"username": target_user.username, "password": user_password}
    )
    assert response.status_code == 200
    assert "_auth_user_id" not in client.session


def test_admin_cannot_deactivate_self_via_view(logged_in_admin, admin_user_obj):
    response = logged_in_admin.post(
        reverse("accounts:user_toggle_active", args=[admin_user_obj.pk]), follow=True
    )

    assert "You cannot deactivate your own account." in response.content.decode()
    admin_user_obj.refresh_from_db()
    assert admin_user_obj.is_active is True


def test_toggle_active_rejects_get(logged_in_admin, target_user):
    response = logged_in_admin.get(reverse("accounts:user_toggle_active", args=[target_user.pk]))

    assert response.status_code == 405
    target_user.refresh_from_db()
    assert target_user.is_active is True
