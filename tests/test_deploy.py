import runpy

import pytest
from django.conf import settings

DEV_ONLY_MAIL_BACKENDS = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
}


@pytest.mark.django_db
def test_healthz_makes_no_database_queries(client, django_assert_num_queries):
    with django_assert_num_queries(0):
        response = client.get("/healthz/")

    assert response.status_code == 200


def _load_settings_module():
    # Execute settings.py as a plain module so env changes take effect
    # without touching the already-configured django.conf.settings.
    return runpy.run_path(str(settings.BASE_DIR / "config" / "settings.py"))


def test_production_mailer_is_not_a_dev_only_backend(monkeypatch):
    monkeypatch.setenv("DEBUG", "False")
    monkeypatch.delenv("MAILER_BACKEND", raising=False)

    loaded = _load_settings_module()

    assert loaded["MAILERS"]["default"]["BACKEND"] not in DEV_ONLY_MAIL_BACKENDS


def test_render_hostname_is_allowed_and_csrf_trusted(monkeypatch):
    monkeypatch.setenv("RENDER_EXTERNAL_HOSTNAME", "hms-test.onrender.com")

    loaded = _load_settings_module()

    assert "hms-test.onrender.com" in loaded["ALLOWED_HOSTS"]
    assert "localhost" in loaded["ALLOWED_HOSTS"]
    assert loaded["CSRF_TRUSTED_ORIGINS"].count("https://hms-test.onrender.com") == 1
