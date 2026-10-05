from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from pharmacy.models import Dispense, StockBatch

MANAGE = {Role.ADMIN, Role.PHARMACIST}
DISPENSE = {Role.PHARMACIST}
DISPENSE_VIEW = {Role.PHARMACIST, Role.ADMIN}


@pytest.fixture
def objects(make_medicine, make_batch, make_issued_prescription):
    medicine = make_medicine(name="Amoxicillin", unit_price=Decimal("25"))
    batch = make_batch(medicine=medicine, qty=50)
    expired = make_batch(medicine=medicine, qty=5, expiry=timezone.localdate() - timedelta(days=1))
    prescription = make_issued_prescription(items=[(medicine, 10)])
    return {
        "medicine": medicine,
        "batch": batch,
        "expired": expired,
        "prescription": prescription,
        "item": prescription.items.get(),
    }


def build(name, o):
    return {
        "pharmacy:inventory_detail": lambda: [o["medicine"].pk],
        "pharmacy:receive_stock": lambda: [o["medicine"].pk],
        "pharmacy:adjust_stock": lambda: [o["batch"].pk],
        "pharmacy:write_off": lambda: [o["expired"].pk],
        "pharmacy:dispense_page": lambda: [o["prescription"].pk],
        "pharmacy:dispense": lambda: [o["prescription"].pk],
    }.get(name, lambda: [])()


def post_data(name, o):
    return {
        "pharmacy:receive_stock": {
            "batch_number": "NEW1",
            "expiry_date": (timezone.localdate() + timedelta(days=100)).isoformat(),
            "quantity": 10,
        },
        "pharmacy:adjust_stock": {"quantity_change": -1, "reason": "Broken"},
        "pharmacy:dispense": {f"qty_{o['item'].pk}": 2},
    }.get(name, {})


# (url name, method, allowed roles, status for allowed role)
URLS = [
    ("pharmacy:inventory_list", "get", MANAGE, 200),
    ("pharmacy:inventory_detail", "get", MANAGE, 200),
    ("pharmacy:alerts", "get", MANAGE, 200),
    ("pharmacy:dispensing_queue", "get", DISPENSE_VIEW, 200),
    ("pharmacy:dispense_page", "get", DISPENSE_VIEW, 200),
    ("pharmacy:receive_stock", "post", MANAGE, 302),
    ("pharmacy:adjust_stock", "post", MANAGE, 302),
    ("pharmacy:write_off", "post", MANAGE, 302),
    ("pharmacy:dispense", "post", DISPENSE, 302),
]
POST_ONLY = [url for url in URLS if url[1] == "post"]


@pytest.mark.parametrize(("name", "method", "_roles", "_status"), URLS)
def test_anonymous_redirected(client, objects, name, method, _roles, _status):
    response = getattr(client, method)(reverse(name, args=build(name, objects)))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "method", "roles", "status"), URLS)
def test_rbac_matrix(client_for_role, objects, role, name, method, roles, status):
    client = client_for_role(role)
    url = reverse(name, args=build(name, objects))

    response = client.post(url, post_data(name, objects)) if method == "post" else client.get(url)

    assert response.status_code == (status if role in roles else 403)


@pytest.mark.parametrize(("name", "_method", "_roles", "_status"), POST_ONLY)
def test_post_only_reject_get(client_for_role, objects, name, _method, _roles, _status):
    response = client_for_role(Role.PHARMACIST).get(reverse(name, args=build(name, objects)))

    assert response.status_code == 405


def test_admin_views_dispensing_page_read_only(client_for_role, objects):
    client = client_for_role(Role.ADMIN)
    url = reverse("pharmacy:dispense_page", args=[objects["prescription"].pk])

    page = client.get(url)
    post = client.post(reverse("pharmacy:dispense", args=[objects["prescription"].pk]))

    assert page.status_code == 200 and page.context["can_dispense"] is False
    assert 'name="qty_' not in page.content.decode()
    assert post.status_code == 403


def test_dispensing_page_shows_allergy_and_default_quantities(
    client_for_role, make_medicine, make_batch, make_issued_prescription
):
    medicine = make_medicine(name="Penicillin V", unit_price=Decimal("10"))
    make_batch(medicine=medicine, qty=4)  # less than prescribed
    prescription = make_issued_prescription(items=[(medicine, 10)])
    item = prescription.items.get()
    item.allergy_override = True
    item.save()
    prescription.patient.allergies = "Penicillin"
    prescription.patient.save()

    page = client_for_role(Role.PHARMACIST).get(
        reverse("pharmacy:dispense_page", args=[prescription.pk])
    )
    content = page.content.decode()

    assert "ALLERGIES:" in content and "Penicillin" in content
    assert "Prescribed despite allergy" in content
    assert page.context["form"][f"qty_{item.pk}"].value() == 4  # min(remaining 10, stock 4)


def test_dispense_via_view(client_for_role, objects):
    client = client_for_role(Role.PHARMACIST)
    prescription, item = objects["prescription"], objects["item"]

    response = client.post(
        reverse("pharmacy:dispense", args=[prescription.pk]), {f"qty_{item.pk}": 10}, follow=True
    )

    dispense = Dispense.objects.get()
    assert f"Dispensed {dispense.number}" in response.content.decode()
    prescription.refresh_from_db()
    assert prescription.status == "DISPENSED"


def test_dispense_errors_shown_on_page(client_for_role, objects):
    item = objects["item"]

    response = client_for_role(Role.PHARMACIST).post(
        reverse("pharmacy:dispense", args=[objects["prescription"].pk]), {f"qty_{item.pk}": 11}
    )

    assert response.status_code == 200
    assert "Only 10 left to dispense" in response.content.decode()
    assert not Dispense.objects.exists()


def test_draft_prescription_not_visible(client_for_role, make_issued_prescription):
    prescription = make_issued_prescription()
    prescription.status = "DRAFT"
    prescription.save()

    response = client_for_role(Role.PHARMACIST).get(
        reverse("pharmacy:dispense_page", args=[prescription.pk])
    )

    assert response.status_code == 404


def test_receive_stock_via_view(client_for_role, objects):
    client_for_role(Role.PHARMACIST).post(
        reverse("pharmacy:receive_stock", args=[objects["medicine"].pk]),
        {
            "batch_number": "xyz-9",
            "expiry_date": (timezone.localdate() + timedelta(days=60)).isoformat(),
            "quantity": 30,
            "supplier": "SPC",
        },
    )

    assert StockBatch.objects.get(batch_number="XYZ-9").quantity_on_hand == 30


def test_inventory_and_alert_pages_show_status(client_for_role, objects):
    client = client_for_role(Role.PHARMACIST)

    inventory = client.get(reverse("pharmacy:inventory_list"))
    alerts = client.get(reverse("pharmacy:alerts"))

    row = next(m for m in inventory.context["medicines"] if m.pk == objects["medicine"].pk)
    assert row.usable_stock == 50
    assert objects["expired"] in alerts.context["expired"]


EXPECTED_PHARMACY_NAV = {
    Role.ADMIN: ["Medicines", "Inventory", "Stock alerts"],
    Role.PHARMACIST: ["Prescriptions", "Inventory", "Stock alerts", "Medicines"],
}


@pytest.mark.parametrize("role", list(Role))
def test_pharmacy_nav(client_for_role, role):
    labels = [
        item["label"]
        for item in client_for_role(role).get(reverse("dashboard")).context["nav_items"]
    ]
    pharmacy_labels = [
        label
        for label in labels
        if label in {"Prescriptions", "Inventory", "Stock alerts", "Medicines"}
    ]

    assert pharmacy_labels == EXPECTED_PHARMACY_NAV.get(role, [])


def test_pharmacist_dashboard_quick_actions(client_for_role):
    content = client_for_role(Role.PHARMACIST).get(reverse("dashboard")).content.decode()

    for name in ("pharmacy:dispensing_queue", "pharmacy:inventory_list", "pharmacy:alerts"):
        assert reverse(name) in content
