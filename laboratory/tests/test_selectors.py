import pytest

from laboratory import selectors
from laboratory.models import LabOrder, OrderStatus
from patients.selectors import patient_history

pytestmark = pytest.mark.django_db


def test_worklist_defaults_to_open_orders_urgent_first(make_lab_order):
    routine_old = make_lab_order()
    urgent_new = make_lab_order(priority="URGENT")
    make_lab_order(status=OrderStatus.COMPLETED)
    make_lab_order(status=OrderStatus.CANCELLED)
    collected = make_lab_order(status=OrderStatus.SAMPLE_COLLECTED)

    assert list(selectors.worklist()) == [urgent_new, routine_old, collected]
    assert len(selectors.worklist(status="ALL")) == 5


def test_worklist_search_by_number_and_patient(make_lab_order, make_patient):
    kamal = make_lab_order(patient=make_patient(first_name="Kamal"))
    other = make_lab_order()

    assert list(selectors.worklist(q=kamal.number)) == [kamal]
    assert other in selectors.worklist(q=str(other.pk))
    assert list(selectors.worklist(q="kamal")) == [kamal]


def test_pending_requests_count(make_lab_order):
    make_lab_order()
    make_lab_order(status=OrderStatus.SAMPLE_COLLECTED)
    make_lab_order(status=OrderStatus.COMPLETED)

    assert selectors.pending_requests_count() == 2


def test_missing_results(make_lab_order, make_lab_test):
    test = make_lab_test(parameters=[{"name": "A"}, {"name": "B"}])
    order = make_lab_order(tests=[test])

    assert [p.name for _, p in selectors.missing_results(order)] == ["A", "B"]


def test_orderable_tests(make_lab_test):
    ok = make_lab_test()
    make_lab_test(is_active=False)
    make_lab_test(parameters=[])

    assert list(selectors.orderable_tests()) == [ok]
    assert selectors.orderable_tests_by_section() == [("Haematology", [ok])]


def test_history_events(make_lab_order):
    order = make_lab_order()
    LabOrder.objects.filter(pk=order.pk).update(
        status=OrderStatus.COMPLETED, released_at=order.created_at
    )

    titles = [event.title for event in patient_history(order.patient)]

    assert f"Lab tests requested ({order.number})" in titles
    assert f"Lab results released ({order.number})" in titles
