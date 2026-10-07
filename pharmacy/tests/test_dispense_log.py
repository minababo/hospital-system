"""Dispensing visible to clinicians (issue #48 item 6): the Dispensing card on the
consultation and treatment history, the shared dispense log, and bounded queries."""

from datetime import date
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from pytest_django.asserts import assertTemplateUsed

from accounts.models import Role
from pharmacy.dispensing import dispense_prescription
from pharmacy.dispensing_selectors import dispense_history_events, dispense_log

pytestmark = pytest.mark.django_db

LOG_TEMPLATE = "pharmacy/partials/dispense_log.html"
CARD_TEMPLATE = "pharmacy/partials/prescription_dispensing.html"


@pytest.fixture
def pharmacist(make_user):
    return make_user(role=Role.PHARMACIST, first_name="Nimali", last_name="Perera")


@pytest.fixture
def setup(make_medicine, make_batch, make_issued_prescription):
    """An issued prescription for 30 x Amoxicillin, with batch P-001 in stock."""
    amox = make_medicine(name="Amoxicillin", unit_price=Decimal("25.00"))
    make_batch(medicine=amox, qty=100, batch_number="P-001", expiry=date(2027, 11, 30))
    prescription = make_issued_prescription(items=[(amox, 30)])
    return prescription, prescription.items.get()


def dispense(prescription, item, qty, user, notes=""):
    return dispense_prescription(
        prescription=prescription, quantities={item.pk: qty}, notes=notes, acting_user=user
    )


def consultation(client, prescription):
    record = prescription.record
    client.force_login(record.doctor.user)
    return client.get(reverse("records:record_detail", args=[record.pk]))


def test_card_says_not_dispensed_yet_before_any_dispense(client, setup):
    prescription, _ = setup
    response = consultation(client, prescription)
    html = response.content.decode()

    assertTemplateUsed(response, CARD_TEMPLATE)
    assert 'id="dispensing"' in html
    assert "Not dispensed yet." in html


def test_card_shows_progress_and_log_after_a_partial_dispense(client, setup, pharmacist):
    prescription, item = setup
    result = dispense(prescription, item, 10, pharmacist, notes="Rest tomorrow")

    html = consultation(client, prescription).content.decode()

    # Prescribed 30, dispensed 10, remaining 20.
    assert '<td class="num">30</td>' in html
    assert '<td class="num">10</td>' in html
    assert ">20</td>" in html
    assert "Partially dispensed" in html
    # The log: DSP number, pharmacist, batch with expiry, notes.
    assert result.number in html
    assert "Nimali Perera" in html
    assert "Batch P-001 (exp 2027-11-30) × 10" in html
    assert "Rest tomorrow" in html
    assert "Not dispensed yet." not in html


def test_dispensing_page_shows_the_same_log(client, setup, pharmacist):
    prescription, item = setup
    result = dispense(prescription, item, 10, pharmacist)
    client.force_login(pharmacist)
    response = client.get(reverse("pharmacy:dispense_page", args=[prescription.pk]))
    html = response.content.decode()

    assertTemplateUsed(response, LOG_TEMPLATE)
    assert result.number in html
    assert "Batch P-001 (exp 2027-11-30) × 10" in html


def test_treatment_history_shows_the_card_without_the_anchor(client_for_role, setup, pharmacist):
    prescription, item = setup
    dispense(prescription, item, 30, pharmacist)
    response = client_for_role(Role.NURSE).get(
        reverse("records:treatment_history", args=[prescription.patient.pk])
    )
    html = response.content.decode()

    assertTemplateUsed(response, CARD_TEMPLATE)
    assert "Batch P-001" in html
    assert 'id="dispensing"' not in html  # one card per consultation: no duplicate ids


def test_history_event_links_to_the_dispensing_card(setup, pharmacist):
    prescription, item = setup
    dispense(prescription, item, 10, pharmacist)
    events = dispense_history_events(prescription.patient)

    assert len(events) == 1
    assert events[0].url == (
        reverse("records:record_detail", args=[prescription.record_id]) + "#dispensing"
    )


def test_dispense_log_uses_three_queries(django_assert_num_queries, setup, pharmacist):
    prescription, item = setup
    for _ in range(3):
        dispense(prescription, item, 5, pharmacist)
    with django_assert_num_queries(3):
        log = dispense_log(prescription)
    assert [sum(i.quantity for i in entry.items) for entry in log] == [5, 5, 5]


def count_queries(client, url):
    with CaptureQueriesContext(connection) as context:
        assert client.get(url).status_code == 200
    return len(context.captured_queries)


def test_consultation_query_count_does_not_grow_with_dispenses(client, setup, pharmacist):
    prescription, item = setup
    record = prescription.record
    client.force_login(record.doctor.user)
    url = reverse("records:record_detail", args=[record.pk])

    dispense(prescription, item, 5, pharmacist)
    one = count_queries(client, url)
    dispense(prescription, item, 5, pharmacist)
    dispense(prescription, item, 5, pharmacist)
    three = count_queries(client, url)

    assert three == one
    assert one <= 40


def test_dispensing_page_query_count_does_not_grow_with_dispenses(client, setup, pharmacist):
    prescription, item = setup
    client.force_login(pharmacist)
    url = reverse("pharmacy:dispense_page", args=[prescription.pk])

    dispense(prescription, item, 5, pharmacist)
    one = count_queries(client, url)
    dispense(prescription, item, 5, pharmacist)
    dispense(prescription, item, 5, pharmacist)
    three = count_queries(client, url)

    assert three == one
    assert one <= 25
