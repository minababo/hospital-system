import pytest
from django.urls import reverse

from accounts.models import Role

PATIENT_ROLES = {Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR, Role.NURSE}


@pytest.mark.parametrize("role", list(Role))
def test_patients_link_only_for_patient_roles(client_for_role, role):
    response = client_for_role(role).get(reverse("dashboard"))

    labels = [item["label"] for item in response.context["nav_items"]]
    assert ("Patients" in labels) == (role in PATIENT_ROLES)
    if role in PATIENT_ROLES:
        assert labels.index("Patients") == 1  # right after Dashboard


def test_receptionist_dashboard_has_register_button(client_for_role):
    response = client_for_role(Role.RECEPTIONIST).get(reverse("dashboard"))

    assert reverse("patients:patient_create") in response.content.decode()
