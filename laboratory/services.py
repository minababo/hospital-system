from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import Role
from appointments.models import Status as AppointmentStatus
from audit.services import Action, created_changes, log_action, saved_snapshot, updated_changes
from billing import services as billing_services
from billing.models import Charge, ChargeType, InvoiceStatus
from laboratory.models import (
    LabOrder,
    LabOrderItem,
    LabOrderReport,
    LabResult,
    LabTest,
    LabTestParameter,
    OrderStatus,
    compute_flag,
    format_decimal,
)
from laboratory.selectors import missing_results, orderable_tests
from patients import services as patient_services
from patients.models import PatientDocument
from records.models import MedicalRecord

# Each public function records one audit entry as its last step (same transaction).
# Status changes lock the order row with select_for_update().

CHARGE_SOURCE = "lab_order_item"


def _lock_order(order):
    # No select_related: PostgreSQL can't lock the nullable side of a join.
    return LabOrder.objects.select_for_update().get(pk=order.pk)


def _require_status(order, *statuses, message):
    if order.status not in statuses:
        raise ValidationError(message)


# --- Catalog -----------------------------------------------------------------------


@transaction.atomic
def create_lab_test(*, acting_user, **fields):
    test = LabTest(**fields)
    test.full_clean()
    test.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="laboratory.test.created",
        obj=test,
        changes=created_changes(test),
    )
    return test


@transaction.atomic
def update_lab_test(test, *, acting_user, **fields):
    before = saved_snapshot(test)
    for name, value in fields.items():
        setattr(test, name, value)
    test.full_clean()
    test.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="laboratory.test.updated",
        obj=test,
        changes=updated_changes(test, before),
    )
    return test


@transaction.atomic
def set_lab_test_active(test, active, *, acting_user):
    was_active = LabTest.objects.values_list("is_active", flat=True).get(pk=test.pk)
    test.is_active = active
    test.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="laboratory.test.activated" if active else "laboratory.test.deactivated",
        obj=test,
        changes={"is_active": [was_active, active]},
    )
    return test


@transaction.atomic
def add_parameter(test, *, acting_user, **fields):
    parameter = LabTestParameter(test=test, **fields)
    parameter.full_clean()
    parameter.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="laboratory.parameter.created",
        obj=parameter,
        changes=created_changes(parameter),
    )
    return parameter


@transaction.atomic
def update_parameter(parameter, *, acting_user, **fields):
    before = saved_snapshot(parameter)
    for name, value in fields.items():
        setattr(parameter, name, value)
    parameter.full_clean()
    parameter.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="laboratory.parameter.updated",
        obj=parameter,
        changes=updated_changes(parameter, before),
    )
    return parameter


@transaction.atomic
def remove_parameter(parameter, *, acting_user):
    # Results keep a PROTECT link to their parameter: past reports must stay complete.
    if parameter.results.exists():
        raise ValidationError(
            "This parameter has recorded results, so it can't be removed. "
            "Create a new version of the test instead and deactivate this one."
        )
    pk, description = parameter.pk, str(parameter)
    parameter.delete()
    parameter.pk = pk  # delete() cleared it; the audit entry still names the row
    log_action(
        actor=acting_user,
        action=Action.DELETE,
        event="laboratory.parameter.removed",
        obj=parameter,
        message=f"Removed {description}",
    )


# --- Ordering ----------------------------------------------------------------------


def _selected_tests(test_ids):
    wanted = {int(pk) for pk in test_ids}
    if not wanted:
        raise ValidationError("Select at least one test.")
    tests = list(orderable_tests().filter(pk__in=wanted))
    if len(tests) != len(wanted):
        raise ValidationError("Some selected tests are inactive or not set up yet.")
    return tests


def _create_order(*, patient, tests, priority, clinical_notes, acting_user, **source):
    """Create the order and its items, and bill each test (one charge per item)."""
    order = LabOrder(
        patient=patient,
        priority=priority,
        clinical_notes=(clinical_notes or "").strip(),
        created_by=acting_user,
        **source,
    )
    order.full_clean()
    order.save()
    for test in tests:
        item = LabOrderItem(order=order, test=test, price=test.price)
        item.full_clean()
        item.save()
        # Billing at request time; cancelling the order voids these charges again.
        billing_services.post_charge(
            patient=patient,
            charge_type=ChargeType.LABORATORY,
            description=f"{test.name} ({order.number})",
            quantity=1,
            unit_price=item.price,
            source_type=CHARGE_SOURCE,
            source_id=item.pk,
            acting_user=acting_user,
        )
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="laboratory.order.created",
        obj=order,
        patient=patient,
        message=(
            f"Requested {', '.join(test.code for test in tests)} ({order.get_priority_display()})"
        ),
    )
    return order


@transaction.atomic
def order_tests_for_record(*, record, test_ids, priority, clinical_notes, acting_user):
    record = MedicalRecord.objects.select_related("appointment", "doctor").get(pk=record.pk)
    if record.doctor.user_id != acting_user.pk:
        raise PermissionDenied("Only the consultation's own doctor can order tests for it.")
    if record.appointment.status == AppointmentStatus.CANCELLED:
        raise ValidationError("The appointment was cancelled.")
    tests = _selected_tests(test_ids)

    already = LabOrderItem.objects.filter(order__record=record, test__in=tests).exclude(
        order__status=OrderStatus.CANCELLED
    )
    if already.exists():
        names = ", ".join(sorted({item.test.name for item in already.select_related("test")}))
        raise ValidationError(f"Already ordered for this consultation: {names}.")

    return _create_order(
        patient=record.patient,
        tests=tests,
        priority=priority,
        clinical_notes=clinical_notes,
        acting_user=acting_user,
        record=record,
        ordering_doctor=record.doctor,
    )


@transaction.atomic
def create_walk_in_order(*, patient, test_ids, referred_by, priority, clinical_notes, acting_user):
    referred_by = (referred_by or "").strip()
    if not referred_by:
        raise ValidationError({"referred_by": ["Enter who referred the patient."]})
    return _create_order(
        patient=patient,
        tests=_selected_tests(test_ids),
        priority=priority,
        clinical_notes=clinical_notes,
        acting_user=acting_user,
        referred_by=referred_by,
    )


# --- Workflow ----------------------------------------------------------------------


@transaction.atomic
def collect_sample(order, *, notes, acting_user, now=None):
    order = _lock_order(order)
    _require_status(
        order, OrderStatus.REQUESTED, message="The sample can only be collected for a new request."
    )
    order.status = OrderStatus.SAMPLE_COLLECTED
    order.sample_collected_at = now or timezone.now()
    order.sample_collected_by = acting_user
    order.sample_notes = (notes or "").strip()[:255]
    order.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="laboratory.order.sample_collected",
        obj=order,
        patient=order.patient,
        changes={"status": [OrderStatus.REQUESTED, OrderStatus.SAMPLE_COLLECTED]},
        message=order.sample_notes,
    )
    return order


def _parse_numeric(raw):
    """Return a Decimal (or None for blank). Raises ValidationError for anything else."""
    if raw is None or str(raw).strip() == "":
        return None
    try:
        value = Decimal(str(raw).strip())
    except InvalidOperation as error:
        raise ValidationError("Enter a number.") from error
    if not value.is_finite():  # Decimal("NaN") and Decimal("Infinity") parse fine
        raise ValidationError("Enter a number.")
    if value.as_tuple().exponent < -3:
        raise ValidationError("Use at most 3 decimal places.")
    return value


@transaction.atomic
def save_results(order, *, item, values, comment, acting_user, now=None):
    """Save one test's results. values = {parameter_id: raw value}; blank clears a value.
    Errors use the form field names ("param_<id>") so they show next to the right box."""
    order = _lock_order(order)
    _require_status(
        order,
        OrderStatus.SAMPLE_COLLECTED,
        message="Results can only be entered after sample collection and before release.",
    )
    if item.order_id != order.pk:
        raise ValidationError("That test isn't on this order.")

    parameters = list(item.test.parameters.all())
    parsed, errors = {}, {}
    for parameter in parameters:
        raw = values.get(parameter.pk)
        if parameter.result_type == LabTestParameter.ResultType.NUMERIC:
            try:
                parsed[parameter.pk] = _parse_numeric(raw)
            except ValidationError as error:
                errors[f"param_{parameter.pk}"] = error.messages
        else:
            parsed[parameter.pk] = (raw or "").strip()
    if errors:
        raise ValidationError(errors)

    existing = {result.parameter_id: result for result in item.results.all()}
    # {parameter name: [old, new]} for the audit entry.
    changes = {}
    for parameter in parameters:
        value = parsed[parameter.pk]
        old = existing[parameter.pk].display_value if parameter.pk in existing else None
        if isinstance(value, Decimal):
            new = format_decimal(value)  # same formatting as display_value: 14.0 -> "14"
        else:
            new = value or None
        if old != new:
            changes[parameter.name] = [old, new]
        if value in (None, ""):
            if parameter.pk in existing:
                existing[parameter.pk].delete()
            continue
        result = existing.get(parameter.pk) or LabResult(item=item, parameter=parameter)
        numeric = parameter.result_type == LabTestParameter.ResultType.NUMERIC
        result.value_numeric = value if numeric else None
        result.value_text = "" if numeric else value[:255]
        # Snapshot the catalog's unit and range as they are now.
        result.unit = parameter.unit
        result.ref_low = parameter.ref_low
        result.ref_high = parameter.ref_high
        result.ref_text = parameter.ref_text
        result.flag = compute_flag(result.value_numeric, result.ref_low, result.ref_high)
        result.entered_by = acting_user
        result.entered_at = now or timezone.now()
        result.full_clean()
        result.save()

    old_comment = item.comment
    item.comment = (comment or "").strip()
    item.save(update_fields=["comment"])
    if old_comment != item.comment:
        changes["comment"] = [old_comment, item.comment]
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="laboratory.results.saved",
        obj=item,
        patient=order.patient,
        changes=changes,
    )
    return item


@transaction.atomic
def release_results(order, *, acting_user, now=None):
    order = _lock_order(order)
    _require_status(
        order,
        OrderStatus.SAMPLE_COLLECTED,
        message="Only orders with a collected sample can be released.",
    )
    missing = missing_results(order)
    if missing:
        names = ", ".join(f"{item.test.code}: {parameter.name}" for item, parameter in missing)
        raise ValidationError(f"Enter all results before releasing. Missing: {names}.")
    order.status = OrderStatus.COMPLETED
    order.released_at = now or timezone.now()
    order.released_by = acting_user
    order.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="laboratory.order.released",
        obj=order,
        patient=order.patient,
        changes={"status": [OrderStatus.SAMPLE_COLLECTED, OrderStatus.COMPLETED]},
    )
    return order


@transaction.atomic
def cancel_order(order, *, reason, acting_user, now=None):
    """Cancel an open order and void its unpaid charges. All or nothing: if any charge
    is already on an issued invoice, nothing changes."""
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": ["Please give a reason for cancelling."]})
    order = _lock_order(order)
    _require_status(
        order,
        OrderStatus.REQUESTED,
        OrderStatus.SAMPLE_COLLECTED,
        message="Completed or cancelled orders can't be cancelled.",
    )
    is_lab_staff = acting_user.role == Role.LAB_STAFF
    is_ordering_doctor = (
        order.ordering_doctor_id is not None and order.ordering_doctor.user_id == acting_user.pk
    )
    if not (is_lab_staff or is_ordering_doctor):
        raise PermissionDenied("Only lab staff or the ordering doctor can cancel this order.")

    items = list(order.items.select_related("test"))
    charges = {
        charge.source_id: charge
        for charge in Charge.objects.filter(
            source_type=CHARGE_SOURCE,
            source_id__in=[item.pk for item in items],
            is_voided=False,
        ).select_related("invoice")
    }
    # Check every charge first, so a blocked one doesn't leave others half-voided.
    for item in items:
        charge = charges.get(item.pk)
        if charge and charge.invoice_id and charge.invoice.status != InvoiceStatus.DRAFT:
            raise ValidationError(
                f"Charge for {item.test.name} is on invoice {charge.invoice.number} — "
                "void or settle that invoice first."
            )
    for charge in charges.values():
        billing_services.void_charge(
            charge,
            reason=f"Lab order {order.number} cancelled: {reason}",
            acting_user=acting_user,
            now=now,
        )

    old_status = order.status
    order.status = OrderStatus.CANCELLED
    order.cancelled_at = now or timezone.now()
    order.cancelled_by = acting_user
    order.cancel_reason = reason[:255]
    order.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="laboratory.order.cancelled",
        obj=order,
        patient=order.patient,
        changes={"status": [old_status, OrderStatus.CANCELLED]},
        message=f"Cancelled: {order.cancel_reason}",
    )
    return order


@transaction.atomic
def attach_lab_report(order, *, file, description, acting_user):
    if acting_user.role != Role.LAB_STAFF:
        raise PermissionDenied("Only lab staff can upload lab reports.")
    if order.status == OrderStatus.CANCELLED:
        raise ValidationError("Reports can't be added to a cancelled order.")
    document = patient_services.upload_document(
        patient=order.patient,
        file=file,
        category=PatientDocument.Category.LAB_REPORT,
        description=description or order.number,
        acting_user=acting_user,
    )  # upload_document records the file upload itself
    report = LabOrderReport.objects.create(order=order, document=document)
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="laboratory.report.attached",
        obj=report,
        patient=order.patient,
        message=f"Attached {document.original_name} to {order.number}",
    )
    return report
