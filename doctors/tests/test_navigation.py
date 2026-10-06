import pytest
from django.urls import reverse

from accounts.models import Role

EXPECTED_SIDEBAR = {
    Role.ADMIN: [
        "Dashboard",
        "Patients",
        "Appointments",
        "Admissions",
        "Laboratory",
        "Billing",
        "Users",
        "Departments",
        "Wards & beds",
        "Doctors",
        "Medicines",
        "Inventory",
        "Stock alerts",
        "Lab tests",
        "Change password",
    ],
    Role.DOCTOR: [
        "Dashboard",
        "Patients",
        "Appointments",
        "Admissions",
        "Laboratory",
        "My profile",
        "Change password",
    ],
    Role.NURSE: [
        "Dashboard",
        "Patients",
        "Appointments",
        "Admissions",
        "Laboratory",
        "Doctors",
        "Change password",
    ],
    Role.RECEPTIONIST: [
        "Dashboard",
        "Patients",
        "Appointments",
        "Admissions",
        "Laboratory",
        "Billing",
        "Doctors",
        "Change password",
    ],
    Role.LAB_STAFF: ["Dashboard", "Lab worklist", "Lab tests", "Change password"],
    Role.PHARMACIST: [
        "Dashboard",
        "Prescriptions",
        "Inventory",
        "Stock alerts",
        "Medicines",
        "Change password",
    ],
    Role.ACCOUNTANT: ["Dashboard", "Billing", "Change password"],
}


@pytest.mark.parametrize("role", list(Role))
def test_sidebar_items_per_role(client_for_role, role):
    response = client_for_role(role).get(reverse("dashboard"))

    assert [item["label"] for item in response.context["nav_items"]] == EXPECTED_SIDEBAR[role]
