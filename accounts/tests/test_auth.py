import pytest
from django.urls import reverse

from accounts.models import Role

LOGIN_URL = reverse("accounts:login")
LOGOUT_URL = reverse("accounts:logout")
PASSWORD_CHANGE_URL = reverse("accounts:password_change")


def is_logged_in(client):
    return "_auth_user_id" in client.session


# --- Login / logout ---------------------------------------------------------


@pytest.mark.django_db
def test_login_page_renders(client):
    response = client.get(LOGIN_URL)

    assert response.status_code == 200
    assert "accounts/login.html" in [t.name for t in response.templates]


def test_login_success_redirects_to_dashboard(client, make_user, user_password):
    user = make_user(role=Role.NURSE)

    response = client.post(LOGIN_URL, {"username": user.username, "password": user_password})

    assert response.status_code == 302
    assert response.url == "/dashboard/"
    assert is_logged_in(client)


def test_login_wrong_password_shows_error(client, make_user):
    user = make_user()

    response = client.post(LOGIN_URL, {"username": user.username, "password": "wrong"})

    assert response.status_code == 200
    assert response.context["form"].non_field_errors()
    assert not is_logged_in(client)


def test_inactive_user_cannot_log_in(client, make_user, user_password):
    user = make_user(is_active=False)

    response = client.post(LOGIN_URL, {"username": user.username, "password": user_password})

    assert response.status_code == 200
    assert not is_logged_in(client)


def test_login_page_shows_expired_message(client, db):
    response = client.get(LOGIN_URL, {"expired": "1"})

    assert b"Your session expired due to inactivity" in response.content


def test_logout_rejects_get(client_for_role):
    client = client_for_role(Role.DOCTOR)

    assert client.get(LOGOUT_URL).status_code == 405
    assert is_logged_in(client)


def test_logout_via_post(client_for_role):
    client = client_for_role(Role.DOCTOR)

    response = client.post(LOGOUT_URL)

    assert response.status_code == 302
    assert response.url == LOGIN_URL
    assert not is_logged_in(client)


# --- Password change --------------------------------------------------------


def test_password_change_requires_correct_old_password(client, make_user):
    user = make_user()
    client.force_login(user)

    response = client.post(
        PASSWORD_CHANGE_URL,
        {
            "old_password": "not-my-password",
            "new_password1": "N3w-Secure-Passw0rd",
            "new_password2": "N3w-Secure-Passw0rd",
        },
    )

    assert response.status_code == 200
    assert "old_password" in response.context["form"].errors


def test_password_change_success(client, make_user, user_password):
    user = make_user()
    client.force_login(user)
    new_password = "N3w-Secure-Passw0rd"

    response = client.post(
        PASSWORD_CHANGE_URL,
        {
            "old_password": user_password,
            "new_password1": new_password,
            "new_password2": new_password,
        },
    )

    assert response.status_code == 302
    assert response.url == reverse("dashboard")
    # Still logged in after the change...
    assert client.get(reverse("dashboard")).status_code == 200
    # ...and the new password works for a fresh login.
    client.logout()
    client.post(LOGIN_URL, {"username": user.username, "password": new_password})
    assert is_logged_in(client)


# --- Session timeout --------------------------------------------------------


def test_session_settings_create_idle_timeout(monkeypatch, load_settings):
    monkeypatch.setenv("SESSION_IDLE_TIMEOUT_MINUTES", "15")

    loaded = load_settings()

    assert loaded["SESSION_COOKIE_AGE"] == 15 * 60
    assert loaded["SESSION_SAVE_EVERY_REQUEST"] is True
    assert loaded["SESSION_EXPIRE_AT_BROWSER_CLOSE"] is True


def test_timeout_seconds_reach_the_page(client_for_role, settings):
    response = client_for_role(Role.DOCTOR).get(reverse("dashboard"))

    assert response.context["session_timeout_seconds"] == settings.SESSION_COOKIE_AGE
