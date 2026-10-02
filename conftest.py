import itertools
import runpy

import pytest
from django.conf import settings as django_settings

from accounts.models import Role, User


@pytest.fixture(autouse=True)
def _fast_password_hasher(settings):
    # The production hasher is slow on purpose; tests create many users.
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def _plain_static_storage(settings):
    # Tests run with DEBUG=False, and the manifest storage fails without collectstatic.
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture
def load_settings():
    """Run config/settings.py as a plain module, so tests can check how env vars
    change settings without touching the live django.conf.settings."""

    def _load():
        return runpy.run_path(str(django_settings.BASE_DIR / "config" / "settings.py"))

    return _load


@pytest.fixture
def user_password():
    return "Str0ng-Test-Passw0rd!"


@pytest.fixture
def make_user(db, user_password):
    counter = itertools.count(1)

    def _make_user(role=Role.DOCTOR, password=None, **kwargs):
        n = next(counter)
        username = kwargs.pop("username", f"{role.lower()}{n}")
        kwargs.setdefault("first_name", "Test")
        kwargs.setdefault("last_name", f"User{n}")
        kwargs.setdefault("email", f"{username}@example.com")
        return User.objects.create_user(
            username=username, password=password or user_password, role=role, **kwargs
        )

    return _make_user


@pytest.fixture
def admin_user_obj(make_user):
    return make_user(role=Role.ADMIN, username="admin1")


@pytest.fixture
def client_for_role(client, make_user):
    """Returns a function: client_for_role(Role.NURSE) -> client logged in as a new nurse."""

    def _client_for_role(role):
        client.force_login(make_user(role=role))
        return client

    return _client_for_role
