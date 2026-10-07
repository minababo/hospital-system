from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Role
from admissions.models import (
    Admission,
    AdmissionStatus,
    Bed,
    BedAssignment,
    DischargeType,
    ProgressNote,
    Source,
    Ward,
    nights_between,
)
from audit.services import Action, created_changes, log_action, saved_snapshot, updated_changes
from billing import services as billing_services
from billing.models import ChargeType

# Each public write records one audit entry as its last step (same transaction).
# Bed charges are logged by billing.services.post_charge itself.
# Locking: state changes lock the Admission row first, then Bed rows, always in that
# order, so concurrent requests queue instead of double-booking a bed.

CHARGE_SOURCE = "bed_assignment"


def _lock_admission(admission):
    # No select_related: PostgreSQL can't lock the nullable side of a join.
    return Admission.objects.select_for_update().get(pk=admission.pk)


def _lock_bed(bed):
    return Bed.objects.select_for_update().get(pk=bed.pk)


def _bed_is_occupied(bed):
    return BedAssignment.objects.filter(bed=bed, ended_at__isnull=True).exists()


def _check_bed_available(bed):
    if not bed.is_active or not bed.ward.is_active:
        raise ValidationError({"bed": ["This bed is not in use."]})
    if _bed_is_occupied(bed):
        raise ValidationError({"bed": ["This bed is already occupied."]})


def _check_event_time(when, *, now, not_before=None, field):
    """A clinical event time: not in the future, at most ADMISSION_BACKDATE_DAYS old,
    and not before the previous event of the same stay."""
    if when > now:
        raise ValidationError({field: ["This can't be in the future."]})
    limit = settings.ADMISSION_BACKDATE_DAYS
    if when < now - timedelta(days=limit):
        raise ValidationError({field: [f"This can't be more than {limit} days ago."]})
    if not_before is not None and when < not_before:
        raise ValidationError(
            {field: [f"This can't be before {timezone.localtime(not_before):%d %b %Y %H:%M}."]}
        )


def _save_or_raise(instance, message):
    """Save in a savepoint and turn a constraint race into a friendly error."""
    try:
        with transaction.atomic():
            instance.save()
    except IntegrityError as error:
        raise ValidationError(message) from error


def _open_assignment(admission, bed, when):
    assignment = BedAssignment(
        admission=admission, bed=bed, daily_rate=bed.ward.daily_rate, started_at=when
    )
    assignment.full_clean(validate_constraints=False)
    _save_or_raise(assignment, {"bed": ["This bed was just taken. Please choose another."]})
    return assignment


def _post_assignment_charge(assignment, *, acting_user, minimum_one_day=False):
    """Bill a closed bed period: one unit per night at the rate copied when it opened.
    Charges post only when the period closes (transfer or discharge), so a stay is
    never billed for nights it didn't have. post_charge is idempotent per assignment."""
    nights = nights_between(assignment.started_at, assignment.ended_at)
    quantity = nights or (1 if minimum_one_day else 0)
    if quantity == 0:
        return None
    bed, admission = assignment.bed, assignment.admission
    return billing_services.post_charge(
        patient=admission.patient,
        charge_type=ChargeType.ADMISSION,
        description=(
            f"{bed.ward.name} bed {bed.bed_number} — {quantity} day(s) ({admission.number})"
        ),
        quantity=quantity,
        unit_price=assignment.daily_rate,
        source_type=CHARGE_SOURCE,
        source_id=assignment.pk,
        acting_user=acting_user,
    )


# --- Wards and beds -------------------------------------------------------------------


@transaction.atomic
def create_ward(*, acting_user, **fields):
    ward = Ward(**fields)
    ward.full_clean()
    ward.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="admissions.ward.created",
        obj=ward,
        changes=created_changes(ward),
    )
    return ward


@transaction.atomic
def update_ward(ward, *, acting_user, **fields):
    # A new daily rate applies to new bed periods; open ones keep their copied rate.
    before = saved_snapshot(ward)
    for name, value in fields.items():
        setattr(ward, name, value)
    ward.full_clean()
    ward.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="admissions.ward.updated",
        obj=ward,
        changes=updated_changes(ward, before),
    )
    return ward


@transaction.atomic
def set_ward_active(ward, active, *, acting_user):
    if not active and BedAssignment.objects.filter(bed__ward=ward, ended_at__isnull=True).exists():
        raise ValidationError("This ward has patients in it. Move or discharge them first.")
    was_active = Ward.objects.values_list("is_active", flat=True).get(pk=ward.pk)
    ward.is_active = active
    ward.save(update_fields=["is_active", "updated_at"])
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="admissions.ward.activated" if active else "admissions.ward.deactivated",
        obj=ward,
        changes={"is_active": [was_active, active]},
    )
    return ward


@transaction.atomic
def add_bed(ward, *, acting_user, **fields):
    bed = Bed(ward=ward, **fields)
    bed.full_clean()
    bed.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="admissions.bed.created",
        obj=bed,
        changes=created_changes(bed),
    )
    return bed


@transaction.atomic
def update_bed(bed, *, acting_user, **fields):
    before = saved_snapshot(bed)
    for name, value in fields.items():
        setattr(bed, name, value)
    bed.full_clean()
    bed.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="admissions.bed.updated",
        obj=bed,
        changes=updated_changes(bed, before),
    )
    return bed


@transaction.atomic
def set_bed_active(bed, active, *, acting_user):
    bed = _lock_bed(bed)
    if not active and _bed_is_occupied(bed):
        raise ValidationError("This bed is occupied. Move or discharge the patient first.")
    was_active = bed.is_active  # bed was just re-read from the database by _lock_bed
    bed.is_active = active
    bed.save(update_fields=["is_active"])
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="admissions.bed.activated" if active else "admissions.bed.deactivated",
        obj=bed,
        changes={"is_active": [was_active, active]},
    )
    return bed


# --- Admission, transfer, discharge --------------------------------------------------


@transaction.atomic
def admit_patient(
    *,
    patient,
    bed,
    admitting_doctor,
    reason,
    source,
    appointment=None,
    admitted_at=None,
    acting_user,
    now=None,
):
    now = now or timezone.now()
    admitted_at = admitted_at or now
    _check_event_time(admitted_at, now=now, field="admitted_at")

    current = Admission.objects.filter(patient=patient, status=AdmissionStatus.ADMITTED).first()
    if current:
        raise ValidationError(f"This patient is already admitted ({current.number}).")
    if not admitting_doctor.user.is_active:
        raise ValidationError({"admitting_doctor": ["This doctor is not active."]})
    if appointment is not None:
        if appointment.patient_id != patient.pk:
            raise ValidationError("That appointment is for another patient.")
        if source != Source.OPD:
            raise ValidationError(
                {"source": ["Admissions from an appointment use the OPD source."]}
            )
    elif source == Source.OPD:
        raise ValidationError({"source": ["Choose the outpatient appointment, or another source."]})

    bed = _lock_bed(bed)
    _check_bed_available(bed)

    admission = Admission(
        patient=patient,
        admitting_doctor=admitting_doctor,
        appointment=appointment,
        source=source,
        reason=(reason or "").strip(),
        admitted_at=admitted_at,
        admitted_by=acting_user,
    )
    admission.full_clean(validate_constraints=False)
    _save_or_raise(admission, "This patient is already admitted.")
    _open_assignment(admission, bed, admitted_at)
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="admissions.admission.admitted",
        obj=admission,
        patient=patient,
        changes=created_changes(admission, ["admitting_doctor", "source", "reason", "admitted_at"])
        | {"bed": [None, bed]},
        message=f"Admitted to {bed.ward.name} bed {bed.bed_number}",
    )
    return admission


@transaction.atomic
def transfer_bed(admission, *, new_bed, transferred_at=None, acting_user, now=None):
    now = now or timezone.now()
    admission = _lock_admission(admission)
    if admission.status != AdmissionStatus.ADMITTED:
        raise ValidationError("Only current inpatients can be moved.")
    current = BedAssignment.objects.select_for_update().get(
        admission=admission, ended_at__isnull=True
    )
    if new_bed.pk == current.bed_id:
        raise ValidationError({"new_bed": ["The patient is already in this bed."]})
    when = transferred_at or now
    _check_event_time(when, now=now, not_before=current.started_at, field="transferred_at")

    new_bed = _lock_bed(new_bed)
    _check_bed_available(new_bed)

    current.ended_at = when
    current.save(update_fields=["ended_at"])
    _post_assignment_charge(current, acting_user=acting_user)
    assignment = _open_assignment(admission, new_bed, when)
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="admissions.admission.transferred",
        obj=admission,
        patient=admission.patient,
        changes={"bed": [current.bed, new_bed]},
        message=f"Moved to {new_bed.ward.name} bed {new_bed.bed_number}",
    )
    return assignment


@transaction.atomic
def add_progress_note(admission, *, note_type, text, acting_user, now=None):
    if admission.status != AdmissionStatus.ADMITTED:
        raise ValidationError("Notes can only be added while the patient is admitted.")
    text = (text or "").strip()
    if not text:
        raise ValidationError({"text": ["Write the note."]})
    note = ProgressNote(
        admission=admission,
        note_type=note_type,
        text=text,
        author=acting_user,
        created_at=now or timezone.now(),
    )
    note.full_clean()
    note.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="admissions.note.added",
        obj=note,
        patient=admission.patient,
        message=f"{note.get_note_type_display()} note on {admission.number}",
    )
    return note


@transaction.atomic
def discharge_patient(
    admission, *, discharge_type, discharge_summary, discharged_at=None, acting_user, now=None
):
    """Close the stay and bill the last bed period.

    Minimum charge: an admission is billed at least one day. If the whole stay had
    no overnight (e.g. admitted and discharged the same day), one day is charged on
    the last bed period at its rate.
    """
    if acting_user.role != Role.DOCTOR:
        raise PermissionDenied("Only doctors can discharge patients.")
    now = now or timezone.now()
    admission = _lock_admission(admission)
    if admission.status != AdmissionStatus.ADMITTED:
        raise ValidationError("This patient has already been discharged.")
    summary = (discharge_summary or "").strip()
    if not summary:
        raise ValidationError({"discharge_summary": ["Write the discharge summary."]})
    if discharge_type not in DischargeType.values:
        raise ValidationError({"discharge_type": ["Choose how the patient left."]})

    current = BedAssignment.objects.select_for_update().get(
        admission=admission, ended_at__isnull=True
    )
    when = discharged_at or now
    _check_event_time(when, now=now, not_before=current.started_at, field="discharged_at")
    if when <= admission.admitted_at:
        raise ValidationError({"discharged_at": ["Discharge must be after the admission time."]})

    current.ended_at = when
    current.save(update_fields=["ended_at"])
    total_nights = sum(
        nights_between(a.started_at, a.ended_at) for a in admission.assignments.all()
    )
    _post_assignment_charge(current, acting_user=acting_user, minimum_one_day=total_nights == 0)

    admission.status = AdmissionStatus.DISCHARGED
    admission.discharged_at = when
    admission.discharged_by = acting_user
    admission.discharge_type = discharge_type
    admission.discharge_summary = summary
    admission.full_clean()
    admission.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="admissions.admission.discharged",
        obj=admission,
        patient=admission.patient,
        changes={
            "status": [AdmissionStatus.ADMITTED, admission.status],
            "discharge_type": [None, discharge_type],
        },
        message=f"Discharged ({admission.get_discharge_type_display()})",
    )
    return admission
