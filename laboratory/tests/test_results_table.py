"""The shared results table (issue #48 item 5) on the order page, consultation and history."""

import re
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from pytest_django.asserts import assertTemplateUsed

from accounts.models import Role
from laboratory.models import LabResult, OrderStatus
from records.models import RecordStatus

pytestmark = pytest.mark.django_db

PARAMETERS = [
    {"name": "Haemoglobin", "unit": "g/dL", "ref_low": "12", "ref_high": "16"},
    {"name": "WBC", "unit": "10^9/L", "ref_low": "4", "ref_high": "11"},
    {"name": "Platelets", "unit": "10^9/L", "ref_low": "150", "ref_high": "400"},
]
TEMPLATE = "laboratory/partials/results_table.html"


@pytest.fixture
def released_order(make_lab_order, make_lab_test, make_record):
    """A completed consultation order: Hb low, WBC high, platelets normal, with a comment."""
    record = make_record(status=RecordStatus.FINALIZED)
    test = make_lab_test(name="Full blood count", parameters=PARAMETERS)
    order = make_lab_order(record=record, tests=[test], status=OrderStatus.COMPLETED)
    item = order.items.get()
    item.comment = "Repeat in two weeks"
    item.save()
    values = {"Haemoglobin": ("10.2", "L"), "WBC": ("12.5", "H"), "Platelets": ("250", "N")}
    for parameter in test.parameters.all():
        value, flag = values[parameter.name]
        LabResult.objects.create(
            item=item,
            parameter=parameter,
            value_numeric=Decimal(value),
            unit=parameter.unit,
            ref_low=parameter.ref_low,
            ref_high=parameter.ref_high,
            flag=flag,
            entered_at=timezone.now(),
        )
    return order


def row(html, name):
    """The <tr> for one parameter."""
    match = re.search(rf"<tr>\s*<td>{re.escape(name)}</td>.*?</tr>", html, re.S)
    assert match, name
    return match.group()


def check_table(html):
    assert '<th class="num">Result</th>' in html and "Reference range" in html
    low, high, normal = row(html, "Haemoglobin"), row(html, "WBC"), row(html, "Platelets")
    assert '<span class="badge badge-warning" title="Low">L</span>' in low
    assert '<span class="badge badge-danger" title="High">H</span>' in high
    assert "badge" not in normal
    assert re.search(r'<td class="num [^"]*">10.2</td>', low)  # numbers right-aligned
    assert "12–16 g/dL" in low
    assert "Comment:</span> Repeat in two weeks" in html


def test_order_page_uses_results_table(client_for_role, released_order):
    response = client_for_role(Role.LAB_STAFF).get(
        reverse("laboratory:order_detail", args=[released_order.pk])
    )
    assertTemplateUsed(response, TEMPLATE)
    check_table(response.content.decode())


def test_consultation_uses_results_table(client, released_order):
    record = released_order.record
    client.force_login(record.doctor.user)
    response = client.get(reverse("records:record_detail", args=[record.pk]))
    assertTemplateUsed(response, TEMPLATE)
    check_table(response.content.decode())


def test_treatment_history_uses_results_table(client_for_role, released_order):
    response = client_for_role(Role.NURSE).get(
        reverse("records:treatment_history", args=[released_order.patient.pk])
    )
    assertTemplateUsed(response, TEMPLATE)
    check_table(response.content.decode())


def test_unreleased_results_are_not_shown_to_clinicians(client, make_lab_order, make_record):
    record = make_record()
    order = make_lab_order(record=record, status=OrderStatus.SAMPLE_COLLECTED)
    client.force_login(record.doctor.user)
    response = client.get(reverse("records:record_detail", args=[record.pk]))
    assert order.number in response.content.decode()
    assert TEMPLATE not in [t.name for t in response.templates]


def test_no_comment_row_without_a_comment(client_for_role, make_lab_order):
    order = make_lab_order()
    html = (
        client_for_role(Role.LAB_STAFF)
        .get(reverse("laboratory:order_detail", args=[order.pk]))
        .content.decode()
    )
    assert "Comment:" not in html
