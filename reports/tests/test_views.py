import pytest
from django.urls import reverse
from pytest_django.asserts import assertTemplateUsed

from accounts.models import Role

DASHBOARD_URL = reverse("dashboard")


def test_anonymous_is_redirected_to_login(client):
    response = client.get(DASHBOARD_URL)

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
def test_each_role_gets_its_own_dashboard(client_for_role, role):
    response = client_for_role(role).get(DASHBOARD_URL)

    assert response.status_code == 200
    assertTemplateUsed(response, f"reports/dashboards/{role.lower()}.html")
    assert f"{role.label} Dashboard" in response.content.decode()


@pytest.mark.parametrize("role", list(Role))
def test_users_link_only_in_admin_sidebar(client_for_role, role):
    response = client_for_role(role).get(DASHBOARD_URL)

    labels = [item["label"] for item in response.context["nav_items"]]
    assert ("Users" in labels) == (role == Role.ADMIN)
    assert (reverse("accounts:user_list") in response.content.decode()) == (role == Role.ADMIN)
