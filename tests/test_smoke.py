import pytest
from django.contrib.auth import get_user_model
from pytest_django.asserts import assertTemplateUsed

from accounts.models import User


def test_healthz_returns_ok(client):
    response = client.get("/healthz/")

    assert response.status_code == 200
    assert response.content == b"ok"


def test_home_renders_with_base_template(client):
    response = client.get("/")

    assert response.status_code == 200
    assertTemplateUsed(response, "home.html")
    assertTemplateUsed(response, "base.html")


def test_unknown_url_returns_custom_404(client):
    response = client.get("/this-page-does-not-exist/")

    assert response.status_code == 404
    assertTemplateUsed(response, "404.html")


def test_custom_user_model_is_active():
    assert get_user_model() is User


@pytest.mark.django_db
def test_create_user_hashes_password():
    raw_password = "S0me-Str0ng-Passw0rd"
    user = get_user_model().objects.create_user(username="alice", password=raw_password)

    assert user.password != raw_password
    assert user.check_password(raw_password)
