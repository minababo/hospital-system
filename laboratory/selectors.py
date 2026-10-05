import re

from django.db.models import Case, Exists, IntegerField, OuterRef, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404
from django.urls import reverse

from laboratory.models import (
    LabOrder,
    LabOrderReport,
    LabResult,
    LabTest,
    LabTestParameter,
    OrderStatus,
    Priority,
    Section,
)
from patients.selectors import HistoryEvent, search_patients

OPEN_STATUSES = (OrderStatus.REQUESTED, OrderStatus.SAMPLE_COLLECTED)
ORDER_NUMBER = re.compile(r"(?:LAB-?)?(\d{1,18})", re.IGNORECASE)


# --- Catalog ---------------------------------------------------------------------


def lab_test_list(search=None, section=None, is_active=None):
    tests = LabTest.objects.prefetch_related("parameters")
    if search:
        tests = tests.filter(Q(code__icontains=search) | Q(name__icontains=search))
    if section:
        tests = tests.filter(section=section)
    if is_active is not None:
        tests = tests.filter(is_active=is_active)
    return tests


def orderable_tests():
    """Active tests that have at least one parameter (otherwise results can't be entered)."""
    has_parameters = Exists(LabTestParameter.objects.filter(test=OuterRef("pk")))
    return LabTest.objects.filter(has_parameters, is_active=True).order_by("section", "name")


def orderable_tests_by_section():
    """[("Haematology", [tests...]), ...] for the grouped checkboxes."""
    tests = list(orderable_tests())
    return [
        (label, [test for test in tests if test.section == value])
        for value, label in Section.choices
        if any(test.section == value for test in tests)
    ]


# --- Orders ----------------------------------------------------------------------


def _orders():
    return LabOrder.objects.select_related(
        "patient", "ordering_doctor__user", "record"
    ).prefetch_related("items__test")


def worklist(*, status=None, priority=None, q=None, date=None, ordering_doctor=None, patient=None):
    """Orders for the lab worklist: open ones by default, urgent first, then oldest first.
    status="ALL" shows every status."""
    orders = _orders()
    if status == "ALL":
        pass
    elif status:
        orders = orders.filter(status=status)
    else:
        orders = orders.filter(status__in=OPEN_STATUSES)
    if priority:
        orders = orders.filter(priority=priority)
    if q:
        q = q.strip()
        matches = Q(patient__in=search_patients(q))
        number = ORDER_NUMBER.fullmatch(q)
        if number:
            matches |= Q(pk=int(number.group(1)))
        orders = orders.filter(matches)
    if date:
        orders = orders.filter(created_at__date=date)
    if ordering_doctor:
        orders = orders.filter(ordering_doctor=ordering_doctor)
    if patient:
        orders = orders.filter(patient=patient)
    urgent_first = Case(
        When(priority=Priority.URGENT, then=Value(0)), default=Value(1), output_field=IntegerField()
    )
    return orders.order_by(urgent_first, "created_at")


def get_order(pk):
    return get_object_or_404(
        _orders()
        .select_related("sample_collected_by", "released_by", "cancelled_by", "created_by")
        .prefetch_related(
            "items__test__parameters",
            Prefetch("items__results", queryset=LabResult.objects.select_related("parameter")),
            Prefetch("reports", queryset=LabOrderReport.objects.select_related("document")),
        ),
        pk=pk,
    )


def orders_for_record(record):
    return (
        LabOrder.objects.filter(record=record)
        .prefetch_related(
            "items__test__parameters",
            Prefetch("items__results", queryset=LabResult.objects.select_related("parameter")),
        )
        .order_by("created_at")
    )


def patient_lab_orders(patient):
    return _orders().filter(patient=patient).order_by("-created_at")


def pending_requests_count():
    """For the dashboard's "Laboratory Requests" card."""
    return LabOrder.objects.filter(status__in=OPEN_STATUSES).count()


def item_rows(item):
    """[(parameter, result or None), ...] in the test's parameter order."""
    results = {result.parameter_id: result for result in item.results.all()}
    return [(parameter, results.get(parameter.pk)) for parameter in item.test.parameters.all()]


def missing_results(order):
    """(item, parameter) pairs that still have no value."""
    missing = []
    for item in order.items.all():
        for parameter, result in item_rows(item):
            if result is None or (result.value_numeric is None and not result.value_text):
                missing.append((item, parameter))
    return missing


# --- Patient history provider -----------------------------------------------------


def lab_history_events(patient):
    """Registered in patients.selectors.PROVIDERS by LaboratoryConfig.ready()."""
    events = []
    orders = (
        LabOrder.objects.filter(patient=patient)
        .exclude(status=OrderStatus.CANCELLED)
        .prefetch_related("items__test", "items__results")
    )
    for order in orders:
        url = reverse("laboratory:order_detail", args=[order.pk])
        tests = ", ".join(item.test.name for item in order.items.all())
        events.append(
            HistoryEvent(
                timestamp=order.created_at,
                kind="Laboratory",
                title=f"Lab tests requested ({order.number})",
                detail=tests,
                url=url,
            )
        )
        if order.released_at:
            abnormal = sum(
                1 for item in order.items.all() for r in item.results.all() if r.is_abnormal
            )
            events.append(
                HistoryEvent(
                    timestamp=order.released_at,
                    kind="Laboratory",
                    title=f"Lab results released ({order.number})",
                    detail=f"{abnormal} abnormal result(s)" if abnormal else "All results normal",
                    url=url,
                )
            )
    return events
