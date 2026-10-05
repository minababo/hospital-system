from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.urls import reverse

from accounts.models import Role
from pharmacy import selectors, services
from pharmacy.models import Medicine

MANAGE = {Role.ADMIN, Role.PHARMACIST}

# --- Model / services -------------------------------------------------------


def test_name_strength_form_unique_ignoring_case(make_medicine):
    make_medicine(name="Amoxicillin", strength="500 mg", form=Medicine.Form.CAPSULE)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_medicine(name="AMOXICILLIN", strength="500 MG", form=Medicine.Form.CAPSULE)


def test_same_name_other_strength_or_form_allowed(make_medicine):
    make_medicine(name="Amoxicillin", strength="500 mg", form=Medicine.Form.CAPSULE)
    make_medicine(name="Amoxicillin", strength="250 mg", form=Medicine.Form.CAPSULE)
    make_medicine(name="Amoxicillin", strength="500 mg", form=Medicine.Form.SYRUP)

    assert Medicine.objects.count() == 3


def test_create_medicine_service_validates_duplicates(make_medicine, admin_user_obj):
    make_medicine(name="Paracetamol", strength="500 mg", form=Medicine.Form.TABLET)

    with pytest.raises(ValidationError, match="already exists"):
        services.create_medicine(
            name=" paracetamol ", strength="500 mg", form="TABLET", acting_user=admin_user_obj
        )


def test_str(make_medicine):
    medicine = make_medicine(name="Amoxicillin", strength="500 mg", form=Medicine.Form.CAPSULE)

    assert str(medicine) == "Amoxicillin 500 mg (Capsule)"


def test_active_medicines_excludes_inactive(make_medicine, admin_user_obj):
    active = make_medicine()
    inactive = make_medicine()
    services.set_medicine_active(inactive, False, acting_user=admin_user_obj)

    assert list(selectors.active_medicines()) == [active]
    assert list(selectors.medicine_list(is_active=False)) == [inactive]


def test_medicine_list_search_and_form(make_medicine):
    amox = make_medicine(name="Amoxil", generic_name="Amoxicillin", form=Medicine.Form.CAPSULE)
    make_medicine(name="Panadol", generic_name="Paracetamol")

    assert list(selectors.medicine_list(search="amoxicillin")) == [amox]
    assert list(selectors.medicine_list(form=Medicine.Form.CAPSULE)) == [amox]


# --- Views ------------------------------------------------------------------

URLS = [
    ("pharmacy:medicine_list", False, "get", 200),
    ("pharmacy:medicine_create", False, "get", 200),
    ("pharmacy:medicine_update", True, "get", 200),
    ("pharmacy:medicine_toggle_active", True, "post", 302),
]


@pytest.mark.parametrize(("name", "needs_pk", "method", "_status"), URLS)
def test_anonymous_redirected(client, make_medicine, name, needs_pk, method, _status):
    url = reverse(name, args=[make_medicine().pk]) if needs_pk else reverse(name)

    response = getattr(client, method)(url)

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "needs_pk", "method", "status"), URLS)
def test_rbac(client_for_role, make_medicine, role, name, needs_pk, method, status):
    url = reverse(name, args=[make_medicine().pk]) if needs_pk else reverse(name)

    response = getattr(client_for_role(role), method)(url)

    assert response.status_code == (status if role in MANAGE else 403)


def test_toggle_rejects_get(client_for_role, make_medicine):
    url = reverse("pharmacy:medicine_toggle_active", args=[make_medicine().pk])

    assert client_for_role(Role.PHARMACIST).get(url).status_code == 405


def test_create_via_view(client_for_role):
    response = client_for_role(Role.PHARMACIST).post(
        reverse("pharmacy:medicine_create"),
        {
            "name": "Cetirizine",
            "strength": "10 mg",
            "form": "TABLET",
            "generic_name": "",
            "unit_price": "12.50",
            "reorder_level": 20,
        },
    )

    assert response.status_code == 302
    medicine = Medicine.objects.get(name="Cetirizine")
    assert (medicine.unit_price, medicine.reorder_level) == (Decimal("12.50"), 20)


@pytest.mark.parametrize("role", list(Role))
def test_medicines_nav_link(client_for_role, role):
    response = client_for_role(role).get(reverse("dashboard"))

    labels = [item["label"] for item in response.context["nav_items"]]
    assert ("Medicines" in labels) == (role in MANAGE)
