from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Role
from appointments import services as appointment_services
from appointments.models import Appointment
from appointments.models import Status as AppointmentStatus
from appointments.selectors import local_now
from audit.services import (
    Action,
    created_changes,
    log_action,
    saved_snapshot,
    snapshot,
    updated_changes,
)
from patients import services as patient_services
from records.models import (
    Diagnosis,
    MedicalRecord,
    Prescription,
    PrescriptionItem,
    PrescriptionStatus,
    RecordAddendum,
    RecordReport,
    RecordStatus,
    Vitals,
)
from records.selectors import allergy_matches, record_for_appointment

# Each public function records one audit entry as its last step (same transaction).
# Status changes lock the row with select_for_update() so two requests can't change
# the same record/prescription/appointment at the same time.

RECORD_FIELDS = {
    "presenting_complaint",
    "clinical_notes",
    "examination_findings",
    "treatment_plan",
    "follow_up_date",
}
FINALIZED_MESSAGE = "This consultation is finalized and can't be changed. Add an addendum instead."


def _ensure_own_doctor(record, user):
    if record.doctor.user_id != user.pk:
        raise PermissionDenied("Only the consultation's own doctor can change it.")


def _ensure_draft(record):
    if record.status != RecordStatus.DRAFT:
        raise ValidationError(FINALIZED_MESSAGE)


def _save_or_raise(instance, message):
    """Save inside a savepoint; turn a constraint race into a friendly error."""
    try:
        with transaction.atomic():
            instance.save()
    except IntegrityError as error:
        raise ValidationError(message) from error


# --- Consultation -----------------------------------------------------------


@transaction.atomic
def start_consultation(*, appointment, acting_user, now=None):
    """Create the DRAFT record for an appointment, or return the existing one."""
    appointment = (
        Appointment.objects.select_for_update().select_related("doctor").get(pk=appointment.pk)
    )
    if appointment.doctor.user_id != acting_user.pk:
        raise PermissionDenied("Only the appointment's own doctor can start the consultation.")

    existing = record_for_appointment(appointment)
    if existing:
        return existing

    if appointment.status not in (AppointmentStatus.CHECKED_IN, AppointmentStatus.COMPLETED):
        raise ValidationError("A consultation can only be started once the patient is checked in.")
    today, _ = local_now(now)
    if appointment.date > today:
        raise ValidationError("A consultation can't be started before the appointment date.")

    record = MedicalRecord(
        appointment=appointment,
        patient_id=appointment.patient_id,
        doctor_id=appointment.doctor_id,
        presenting_complaint=appointment.reason,
    )
    record.full_clean()
    try:
        with transaction.atomic():
            record.save()
    except IntegrityError:
        # Someone (e.g. a double click) created it a moment ago: use that one (no entry,
        # nothing new was written).
        return MedicalRecord.objects.get(appointment=appointment)
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="records.record.started",
        obj=record,
        patient=record.patient,
        message="Consultation started",
    )
    return record


@transaction.atomic
def update_record(record, *, data, acting_user):
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)
    unknown = set(data) - RECORD_FIELDS
    if unknown:
        raise ValueError(f"Cannot update fields: {', '.join(sorted(unknown))}")
    fields = sorted(RECORD_FIELDS)
    before = saved_snapshot(record, fields)
    for name, value in data.items():
        setattr(record, name, value)
    record.full_clean()
    record.save()
    log_action(
        actor=acting_user,
        action=Action.UPDATE,
        event="records.record.updated",
        obj=record,
        patient=record.patient,
        changes=updated_changes(record, before, fields),
    )
    return record


@transaction.atomic
def add_diagnosis(record, *, data, acting_user):
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)
    diagnosis = Diagnosis(record=record, **data)
    diagnosis.full_clean()  # also checks the "one primary diagnosis" constraint
    _save_or_raise(diagnosis, "This consultation already has a primary diagnosis.")
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="records.diagnosis.added",
        obj=diagnosis,
        patient=record.patient,
        changes=created_changes(diagnosis, ["description", "icd10_code", "diagnosis_type"]),
    )
    return diagnosis


@transaction.atomic
def remove_diagnosis(diagnosis, *, acting_user):
    _ensure_own_doctor(diagnosis.record, acting_user)
    _ensure_draft(diagnosis.record)
    pk, description = diagnosis.pk, str(diagnosis)
    diagnosis.delete()
    diagnosis.pk = pk  # delete() cleared it; the audit entry still names the row
    log_action(
        actor=acting_user,
        action=Action.DELETE,
        event="records.diagnosis.removed",
        obj=diagnosis,
        patient=diagnosis.record.patient,
        message=f"Removed diagnosis {description}",
    )


@transaction.atomic
def add_prescription_item(record, *, data, allergy_override, acting_user):
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)
    medicine = data["medicine"]
    if not medicine.is_active:
        raise ValidationError({"medicine": ["This medicine is no longer available."]})

    matches = allergy_matches(record.patient, medicine)
    if matches and not allergy_override:
        # code="allergy" lets the view show the override checkbox.
        raise ValidationError(
            f"Patient allergies mention: {', '.join(matches)}. Tick override to prescribe anyway.",
            code="allergy",
        )

    # The prescription is created with its first item, so empty drafts are rare.
    prescription, _ = Prescription.objects.get_or_create(
        record=record, defaults={"patient": record.patient, "doctor": record.doctor}
    )
    item = PrescriptionItem(prescription=prescription, allergy_override=bool(matches), **data)
    item.full_clean()
    _save_or_raise(item, "This medicine is already on the prescription.")
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="records.prescription_item.added",
        obj=item,
        patient=record.patient,
        changes=created_changes(
            item, ["medicine", "dose", "frequency", "duration_days", "quantity", "allergy_override"]
        ),
        message="Prescribed despite allergy note" if item.allergy_override else "",
    )
    return item


@transaction.atomic
def remove_prescription_item(item, *, acting_user):
    record = item.prescription.record
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)
    pk, description = item.pk, str(item.medicine)
    item.delete()
    item.pk = pk  # delete() cleared it; the audit entry still names the row
    log_action(
        actor=acting_user,
        action=Action.DELETE,
        event="records.prescription_item.removed",
        obj=item,
        patient=record.patient,
        message=f"Removed {description}",
    )


@transaction.atomic
def attach_report(record, *, file, category, description, acting_user):
    """Upload a report for this consultation. Allowed on drafts and finalized records,
    because results often arrive after the consultation."""
    _ensure_own_doctor(record, acting_user)
    document = patient_services.upload_document(
        patient=record.patient,
        file=file,
        category=category,
        description=description,
        acting_user=acting_user,
    )  # upload_document records the file upload itself
    report = RecordReport.objects.create(record=record, document=document)
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="records.report.attached",
        obj=report,
        patient=record.patient,
        message=f"Attached {document.original_name} to the consultation",
    )
    return report


@transaction.atomic
def finalize_record(record, *, acting_user, now=None):
    """Sign off the consultation: issue the prescription, lock the record and complete
    the appointment, all in one transaction."""
    now = now or timezone.now()
    record = MedicalRecord.objects.select_for_update().select_related("doctor").get(pk=record.pk)
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)

    diagnoses = list(record.diagnoses.all())
    if not diagnoses:
        raise ValidationError("Add at least one diagnosis before finalizing.")
    if sum(1 for d in diagnoses if d.diagnosis_type == Diagnosis.Type.PRIMARY) != 1:
        raise ValidationError("Mark exactly one diagnosis as primary before finalizing.")

    prescription = Prescription.objects.select_for_update().filter(record=record).first()
    if prescription:
        if prescription.items.exists():
            prescription.status = PrescriptionStatus.ISSUED
            prescription.issued_at = now
            prescription.save()
            # The prescription is a separate object, so it gets its own entry.
            log_action(
                actor=acting_user,
                action=Action.STATUS_CHANGE,
                event="records.prescription.issued",
                obj=prescription,
                patient=record.patient,
                changes={"status": [PrescriptionStatus.DRAFT, PrescriptionStatus.ISSUED]},
            )
        else:
            prescription.delete()  # no medicines were added; don't keep an empty draft

    record.status = RecordStatus.FINALIZED
    record.finalized_at = now
    record.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="records.record.finalized",
        obj=record,
        patient=record.patient,
        changes={"status": [RecordStatus.DRAFT, RecordStatus.FINALIZED]},
    )

    appointment = Appointment.objects.select_for_update().get(pk=record.appointment_id)
    if appointment.status == AppointmentStatus.CHECKED_IN:
        appointment_services.complete_appointment(appointment, acting_user=acting_user, now=now)
    return record


@transaction.atomic
def add_addendum(record, *, text, acting_user):
    if record.status != RecordStatus.FINALIZED:
        raise ValidationError("Addenda are for finalized consultations; edit the draft instead.")
    _ensure_own_doctor(record, acting_user)
    text = (text or "").strip()
    if not text:
        raise ValidationError({"text": ["Please write the addendum."]})
    addendum = RecordAddendum.objects.create(record=record, text=text, author=acting_user)
    log_action(
        actor=acting_user,
        action=Action.CREATE,
        event="records.addendum.added",
        obj=addendum,
        patient=record.patient,
        changes=created_changes(addendum, ["text"]),
    )
    return addendum


@transaction.atomic
def cancel_prescription(prescription, *, reason, acting_user, now=None):
    prescription = (
        Prescription.objects.select_for_update().select_related("doctor").get(pk=prescription.pk)
    )
    if prescription.doctor.user_id != acting_user.pk:
        raise PermissionDenied("Only the prescribing doctor can cancel this prescription.")
    if prescription.status != PrescriptionStatus.ISSUED:
        raise ValidationError(
            "Only issued prescriptions that haven't been dispensed can be cancelled."
        )
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": ["Please give a reason for cancelling."]})
    prescription.status = PrescriptionStatus.CANCELLED
    prescription.cancelled_at = now or timezone.now()
    prescription.cancelled_by = acting_user
    prescription.cancel_reason = reason[:255]
    prescription.save()
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="records.prescription.cancelled",
        obj=prescription,
        patient=prescription.patient,
        changes={"status": [PrescriptionStatus.ISSUED, PrescriptionStatus.CANCELLED]},
        message=f"Cancelled: {prescription.cancel_reason}",
    )
    return prescription


@transaction.atomic
def update_dispensing_status(prescription, *, fully_dispensed, acting_user, now=None):
    """Record that medicines were handed over: PARTIALLY_DISPENSED, or DISPENSED when
    nothing is left. Called by the pharmacy app; records owns the prescription status
    rules, so pharmacy never sets the status field itself. DISPENSED is final."""
    prescription = Prescription.objects.select_for_update().get(pk=prescription.pk)
    if prescription.status not in (
        PrescriptionStatus.ISSUED,
        PrescriptionStatus.PARTIALLY_DISPENSED,
    ):
        raise ValidationError(
            f"A {prescription.get_status_display().lower()} prescription can't be dispensed."
        )
    old_status = prescription.status
    prescription.status = (
        PrescriptionStatus.DISPENSED if fully_dispensed else PrescriptionStatus.PARTIALLY_DISPENSED
    )
    prescription.save(update_fields=["status", "updated_at"])
    log_action(
        actor=acting_user,
        action=Action.STATUS_CHANGE,
        event="records.prescription.dispensing_updated",
        obj=prescription,
        patient=prescription.patient,
        changes={"status": [old_status, prescription.status]},
    )
    return prescription


# --- Vitals -----------------------------------------------------------------

VITAL_FIELDS = (
    "bp_systolic",
    "bp_diastolic",
    "pulse_bpm",
    "respiratory_rate",
    "spo2_percent",
    "temperature_c",
    "weight_kg",
    "height_cm",
)


@transaction.atomic
def record_vitals(*, appointment, data, acting_user, now=None):
    """Create or update the appointment's single Vitals row."""
    if acting_user.role not in (Role.NURSE, Role.DOCTOR):
        raise PermissionDenied("Only nurses and doctors can record vitals.")
    appointment = Appointment.objects.select_for_update().get(pk=appointment.pk)
    if acting_user.role == Role.DOCTOR and appointment.doctor.user_id != acting_user.pk:
        raise PermissionDenied("Doctors can only record vitals for their own patients.")
    if appointment.status != AppointmentStatus.CHECKED_IN:
        raise ValidationError("Vitals can only be recorded for a checked-in patient.")
    record = record_for_appointment(appointment)
    if record and record.status == RecordStatus.FINALIZED:
        raise ValidationError("The consultation is finalized; vitals can no longer be changed.")

    vitals = Vitals.objects.filter(appointment=appointment).first() or Vitals(
        appointment=appointment, patient_id=appointment.patient_id
    )
    is_new = vitals.pk is None
    before = {} if is_new else snapshot(vitals, VITAL_FIELDS)
    for name in VITAL_FIELDS:
        setattr(vitals, name, data.get(name))
    vitals.recorded_by = acting_user
    vitals.recorded_at = now or timezone.now()
    vitals.full_clean()
    vitals.save()
    log_action(
        actor=acting_user,
        action=Action.CREATE if is_new else Action.UPDATE,
        event="records.vitals.recorded" if is_new else "records.vitals.updated",
        obj=vitals,
        patient=appointment.patient,
        changes=updated_changes(vitals, before, VITAL_FIELDS),
    )
    return vitals
