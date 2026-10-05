from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import Role
from billing import services as billing_services
from billing.models import Charge
from laboratory import services
from laboratory.models import LabOrder, OrderStatus
from patients.models import PatientDocument

pytestmark = pytest.mark.django_db


@pytest.fixture
def lab_staff(make_user):
    return make_user(role=Role.LAB_STAFF)


@pytest.fixture
def fbc(make_lab_test):
    return make_lab_test(
        code="FBC",
        name="Full Blood Count",
        price=Decimal("1500.00"),
        parameters=[
            {"name": "Haemoglobin", "unit": "g/dL", "ref_low": "12", "ref_high": "16"},
            {"name": "Comment", "result_type": "TEXT", "ref_text": "Normal"},
        ],
    )


def order_for(record, tests, **kwargs):
    return services.order_tests_for_record(
        record=record,
        test_ids=[t.pk for t in tests],
        priority=kwargs.get("priority", "ROUTINE"),
        clinical_notes="",
        acting_user=kwargs.get("user", record.doctor.user),
    )


def lab_charges():
    return Charge.objects.filter(source_type="lab_order_item")


# --- Ordering --------------------------------------------------------------------


def test_order_from_record_bills_each_item(make_record, fbc, make_lab_test):
    record = make_record()
    crp = make_lab_test(name="CRP", price=Decimal("800"))

    order = order_for(record, [fbc, crp])

    assert order.ordering_doctor == record.doctor
    assert order.patient == record.patient
    assert order.items.count() == 2
    charges = lab_charges()
    assert charges.count() == 2
    assert {c.charge_type for c in charges} == {"LABORATORY"}
    assert sorted(c.amount for c in charges) == [Decimal("800.00"), Decimal("1500.00")]
    assert any(order.number in c.description for c in charges)


def test_price_is_snapshotted(make_record, fbc):
    order = order_for(make_record(), [fbc])
    fbc.price = Decimal("9999")
    fbc.save()

    assert order.items.get().price == Decimal("1500.00")


def test_posting_the_same_item_again_is_idempotent(make_record, fbc):
    order = order_for(make_record(), [fbc])
    item = order.items.get()

    again = billing_services.post_charge(
        patient=order.patient,
        charge_type="LABORATORY",
        description="dup",
        quantity=1,
        unit_price="1",
        source_type="lab_order_item",
        source_id=item.pk,
        acting_user=None,
    )

    assert lab_charges().count() == 1
    assert again.amount == Decimal("1500.00")


def test_only_own_doctor_orders(make_record, fbc, make_doctor):
    with pytest.raises(PermissionDenied):
        order_for(make_record(), [fbc], user=make_doctor().user)


def test_inactive_or_parameterless_tests_rejected(make_record, make_lab_test):
    record = make_record()
    inactive = make_lab_test(is_active=False)
    empty = make_lab_test(parameters=[])

    for test in (inactive, empty):
        with pytest.raises(ValidationError, match="inactive or not set up"):
            order_for(record, [test])
    with pytest.raises(ValidationError, match="at least one"):
        order_for(record, [])


def test_duplicate_test_on_same_record_rejected(make_record, fbc):
    record = make_record()
    order_for(record, [fbc])

    with pytest.raises(ValidationError, match="Already ordered for this consultation"):
        order_for(record, [fbc])


def test_walk_in_requires_referrer(make_patient, fbc, lab_staff):
    kwargs = {
        "patient": make_patient(),
        "test_ids": [fbc.pk],
        "priority": "URGENT",
        "clinical_notes": "",
        "acting_user": lab_staff,
    }
    with pytest.raises(ValidationError):
        services.create_walk_in_order(referred_by="  ", **kwargs)

    order = services.create_walk_in_order(referred_by="Dr. Fernando (City Clinic)", **kwargs)
    assert order.record is None and order.referred_by == "Dr. Fernando (City Clinic)"
    assert lab_charges().count() == 1


# --- Workflow --------------------------------------------------------------------


@pytest.fixture
def collected(make_lab_order, fbc, lab_staff):
    order = make_lab_order(tests=[fbc])
    services.collect_sample(order, notes="Left arm", acting_user=lab_staff)
    order.refresh_from_db()
    return order


def values_for(item, haemoglobin, comment_text="Normal"):
    hb, text = list(item.test.parameters.all())
    return {hb.pk: haemoglobin, text.pk: comment_text}


def test_collect_only_from_requested(make_lab_order, lab_staff):
    order = make_lab_order()
    services.collect_sample(order, notes="", acting_user=lab_staff)
    order.refresh_from_db()
    assert order.status == OrderStatus.SAMPLE_COLLECTED
    assert order.sample_collected_by == lab_staff

    with pytest.raises(ValidationError, match="new request"):
        services.collect_sample(order, notes="", acting_user=lab_staff)


def test_save_results_requires_collected_sample(make_lab_order, lab_staff):
    order = make_lab_order()
    item = order.items.get()

    with pytest.raises(ValidationError, match="after sample collection"):
        services.save_results(order, item=item, values={}, comment="", acting_user=lab_staff)


def test_save_results_snapshots_and_flags(collected, lab_staff):
    item = collected.items.get()

    services.save_results(
        collected,
        item=item,
        values=values_for(item, "17.2"),
        comment="Repeat in a week",
        acting_user=lab_staff,
    )

    numeric = item.results.get(parameter__result_type="NUMERIC")
    assert numeric.value_numeric == Decimal("17.2")
    assert (numeric.unit, numeric.ref_low, numeric.ref_high) == (
        "g/dL",
        Decimal("12"),
        Decimal("16"),
    )
    assert numeric.flag == "H"
    item.refresh_from_db()
    assert item.comment == "Repeat in a week"

    # Changing the catalog range later doesn't change the saved snapshot...
    parameter = numeric.parameter
    parameter.ref_high = Decimal("20")
    parameter.save()
    numeric.refresh_from_db()
    assert numeric.ref_high == Decimal("16")

    # ...but editing the result re-snapshots and recomputes the flag.
    services.save_results(
        collected, item=item, values=values_for(item, "17.2"), comment="", acting_user=lab_staff
    )
    numeric.refresh_from_db()
    assert numeric.ref_high == Decimal("20") and numeric.flag == "N"


def test_numeric_parsing_errors_are_field_specific(collected, lab_staff):
    item = collected.items.get()
    hb = item.test.parameters.get(result_type="NUMERIC")

    for bad in ("abc", "NaN", "1.23456"):
        with pytest.raises(ValidationError) as excinfo:
            services.save_results(
                collected,
                item=item,
                values=values_for(item, bad),
                comment="",
                acting_user=lab_staff,
            )
        assert f"param_{hb.pk}" in excinfo.value.message_dict


def test_blank_value_clears_result(collected, lab_staff):
    item = collected.items.get()
    services.save_results(
        collected, item=item, values=values_for(item, "14"), comment="", acting_user=lab_staff
    )

    services.save_results(
        collected, item=item, values=values_for(item, ""), comment="", acting_user=lab_staff
    )

    assert item.results.filter(parameter__result_type="NUMERIC").count() == 0


def test_release_requires_all_results_then_locks(collected, lab_staff):
    item = collected.items.get()
    services.save_results(
        collected, item=item, values=values_for(item, "14", ""), comment="", acting_user=lab_staff
    )

    with pytest.raises(ValidationError, match="Missing: FBC: Comment"):
        services.release_results(collected, acting_user=lab_staff)

    services.save_results(
        collected, item=item, values=values_for(item, "14"), comment="", acting_user=lab_staff
    )
    services.release_results(collected, acting_user=lab_staff)
    collected.refresh_from_db()
    assert collected.status == OrderStatus.COMPLETED
    assert collected.released_by == lab_staff

    with pytest.raises(ValidationError):
        services.save_results(
            collected, item=item, values=values_for(item, "15"), comment="", acting_user=lab_staff
        )


# --- Cancelling ------------------------------------------------------------------


def test_cancel_voids_unbilled_and_draft_invoice_charges(make_record, fbc, make_lab_test):
    record = make_record()
    order = order_for(record, [fbc, make_lab_test(name="CRP")])
    on_draft = lab_charges().first()
    billing_services.create_invoice(
        patient=record.patient, charge_ids=[on_draft.pk], acting_user=record.doctor.user
    )

    services.cancel_order(order, reason="Patient declined", acting_user=record.doctor.user)

    order.refresh_from_db()
    assert order.status == OrderStatus.CANCELLED
    assert lab_charges().filter(is_voided=False).count() == 0
    on_draft.refresh_from_db()
    assert on_draft.invoice is None


def test_cancel_blocked_when_charge_on_issued_invoice(make_record, fbc, make_lab_test):
    record = make_record()
    order = order_for(record, [fbc, make_lab_test(name="CRP")])
    first = lab_charges().first()
    invoice = billing_services.create_invoice(
        patient=record.patient, charge_ids=[first.pk], acting_user=record.doctor.user
    )
    billing_services.issue_invoice(invoice, acting_user=record.doctor.user)

    with pytest.raises(ValidationError, match=f"on invoice {invoice.number}"):
        services.cancel_order(order, reason="x", acting_user=record.doctor.user)

    order.refresh_from_db()
    assert order.status == OrderStatus.REQUESTED
    assert lab_charges().filter(is_voided=False).count() == 2  # nothing was voided


def test_cancel_rules(make_record, fbc, make_doctor, lab_staff):
    order = order_for(make_record(), [fbc])

    with pytest.raises(ValidationError):
        services.cancel_order(order, reason=" ", acting_user=lab_staff)
    with pytest.raises(PermissionDenied):
        services.cancel_order(order, reason="x", acting_user=make_doctor().user)

    services.cancel_order(order, reason="Sample haemolysed, re-request", acting_user=lab_staff)
    with pytest.raises(ValidationError):
        services.cancel_order(order, reason="again", acting_user=lab_staff)


# --- Reports ----------------------------------------------------------------------


def test_attach_report_uses_lab_report_category(make_lab_order, make_upload, lab_staff):
    order = make_lab_order()

    report = services.attach_lab_report(
        order, file=make_upload("hb-electrophoresis.pdf"), description="", acting_user=lab_staff
    )

    assert report.document.category == PatientDocument.Category.LAB_REPORT
    assert report.document.patient == order.patient


def test_attach_report_rules(make_lab_order, make_upload, make_user, lab_staff):
    order = make_lab_order()
    with pytest.raises(PermissionDenied):
        services.attach_lab_report(
            order, file=make_upload(), description="", acting_user=make_user(role=Role.NURSE)
        )
    LabOrder.objects.filter(pk=order.pk).update(status=OrderStatus.CANCELLED)
    order.refresh_from_db()
    with pytest.raises(ValidationError):
        services.attach_lab_report(order, file=make_upload(), description="", acting_user=lab_staff)


# --- Catalog -----------------------------------------------------------------------


def test_catalog_services(lab_staff):
    test = services.create_lab_test(
        code=" lft ",
        name="Liver Function",
        section="BIOCHEMISTRY",
        specimen_type="SERUM",
        price=Decimal("2500"),
        turnaround_hours=24,
        description="",
        acting_user=lab_staff,
    )
    assert test.code == "LFT"
    parameter = services.add_parameter(
        test,
        name="ALT",
        unit="U/L",
        result_type="NUMERIC",
        ref_low=None,
        ref_high=Decimal("40"),
        ref_text="",
        display_order=1,
        acting_user=lab_staff,
    )
    services.update_parameter(parameter, unit="IU/L", acting_user=lab_staff)
    services.set_lab_test_active(test, False, acting_user=lab_staff)
    test.refresh_from_db()
    assert test.is_active is False


def test_remove_parameter_blocked_when_results_exist(collected, lab_staff):
    item = collected.items.get()
    services.save_results(
        collected, item=item, values=values_for(item, "14"), comment="", acting_user=lab_staff
    )
    used = item.test.parameters.get(result_type="NUMERIC")

    with pytest.raises(ValidationError, match="recorded results"):
        services.remove_parameter(used, acting_user=lab_staff)
