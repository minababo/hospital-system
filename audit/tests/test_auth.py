import pytest
from django.urls import reverse

from accounts.models import Role
from audit.models import Action, AuditLog

pytestmark = pytest.mark.django_db


def only(event):
    entries = list(AuditLog.objects.filter(event=event))
    assert len(entries) == 1, f"expected one {event}, got {len(entries)}"
    return entries[0]


def test_login_is_logged_with_ip_and_browser(client, make_user, user_password):
    user = make_user(role=Role.NURSE, username="nurse_a")
    response = client.post(
        reverse("accounts:login"),
        {"username": "nurse_a", "password": user_password},
        REMOTE_ADDR="198.51.100.4",
        HTTP_USER_AGENT="TestBrowser/1.0",
    )
    assert response.status_code == 302
    entry = only("accounts.user.logged_in")
    assert entry.action == Action.LOGIN
    assert entry.actor == user and entry.actor_role == Role.NURSE
    assert entry.ip_address == "198.51.100.4"
    assert entry.user_agent == "TestBrowser/1.0"


def test_forwarded_ip_used_only_when_trusted(client, make_user, user_password, settings):
    make_user(username="rec_a")
    data = {"username": "rec_a", "password": user_password}
    headers = {"REMOTE_ADDR": "10.0.0.2", "HTTP_X_FORWARDED_FOR": "203.0.113.50, 10.0.0.1"}

    settings.AUDIT_TRUST_X_FORWARDED_FOR = False
    client.post(reverse("accounts:login"), data, **headers)
    settings.AUDIT_TRUST_X_FORWARDED_FOR = True
    client.post(reverse("accounts:logout"))
    client.post(reverse("accounts:login"), data, **headers)

    ips = list(
        AuditLog.objects.filter(event="accounts.user.logged_in")
        .order_by("id")
        .values_list("ip_address", flat=True)
    )
    assert ips == ["10.0.0.2", "203.0.113.50"]


def test_logout_is_logged(client, make_user):
    user = make_user(role=Role.ACCOUNTANT)
    client.force_login(user)
    client.post(reverse("accounts:logout"))
    entry = only("accounts.user.logged_out")
    assert entry.action == Action.LOGOUT and entry.actor == user


def test_failed_login_keeps_the_username_but_never_the_password(client, make_user):
    make_user(username="lab_a")
    client.post(reverse("accounts:login"), {"username": "lab_a", "password": "Wr0ng-Secret!"})
    entry = only("accounts.user.login_failed")
    assert entry.action == Action.LOGIN_FAILED
    assert entry.actor is None and entry.actor_name == ""
    assert entry.message == "Failed login for 'lab_a'"
    assert "Wr0ng-Secret!" not in str(entry.changes) + entry.message
    assert not AuditLog.objects.filter(event="accounts.user.logged_in").exists()


def test_password_change_is_logged_without_values(client, make_user, user_password):
    user = make_user(role=Role.PHARMACIST)
    client.force_login(user)
    new_password = "N3w-Very-Long-Passphrase!"
    response = client.post(
        reverse("accounts:password_change"),
        {
            "old_password": user_password,
            "new_password1": new_password,
            "new_password2": new_password,
        },
    )
    assert response.status_code == 302
    entry = only("accounts.password.changed")
    assert entry.actor == user and entry.action == Action.UPDATE
    assert entry.changes == {}
    assert new_password not in entry.message and user_password not in entry.message


def test_failed_password_change_is_not_logged(client, make_user):
    client.force_login(make_user())
    client.post(
        reverse("accounts:password_change"),
        {"old_password": "wrong", "new_password1": "x", "new_password2": "y"},
    )
    assert not AuditLog.objects.filter(event="accounts.password.changed").exists()


def test_admin_password_reset_is_logged_without_values(client, make_user, admin_user_obj):
    target = make_user(role=Role.NURSE)
    client.force_login(admin_user_obj)
    new_password = "An0ther-Long-Passphrase!"
    response = client.post(
        reverse("accounts:user_password", args=[target.pk]),
        {"new_password1": new_password, "new_password2": new_password},
    )
    assert response.status_code == 302
    entry = only("accounts.user.password_set")
    assert entry.actor == admin_user_obj
    assert entry.object_id == str(target.pk)
    assert entry.changes == {}
    assert new_password not in entry.message
