from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.models import Role
from appointments import services as appointment_services
from appointments.models import Appointment
from appointments.models import Status as AppointmentStatus
from appointments.selectors import local_now
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

# Audit logging of these actions will be added in these services (audit app).
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
        # Someone (e.g. a double click) created it a moment ago: use that one.
        return MedicalRecord.objects.get(appointment=appointment)
    return record


@transaction.atomic
def update_record(record, *, data, acting_user):
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)
    unknown = set(data) - RECORD_FIELDS
    if unknown:
        raise ValueError(f"Cannot update fields: {', '.join(sorted(unknown))}")
    for name, value in data.items():
        setattr(record, name, value)
    record.full_clean()
    record.save()
    return record


@transaction.atomic
def add_diagnosis(record, *, data, acting_user):
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)
    diagnosis = Diagnosis(record=record, **data)
    diagnosis.full_clean()  # also checks the "one primary diagnosis" constraint
    _save_or_raise(diagnosis, "This consultation already has a primary diagnosis.")
    return diagnosis


@transaction.atomic
def remove_diagnosis(diagnosis, *, acting_user):
    _ensure_own_doctor(diagnosis.record, acting_user)
    _ensure_draft(diagnosis.record)
    diagnosis.delete()


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
    return item


@transaction.atomic
def remove_prescription_item(item, *, acting_user):
    record = item.prescription.record
    _ensure_own_doctor(record, acting_user)
    _ensure_draft(record)
    item.delete()


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
    )
    return RecordReport.objects.create(record=record, document=document)


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
        else:
            prescription.delete()  # no medicines were added; don't keep an empty draft

    record.status = RecordStatus.FINALIZED
    record.finalized_at = now
    record.save()

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
    return RecordAddendum.objects.create(record=record, text=text, author=acting_user)


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
    for name in VITAL_FIELDS:
        setattr(vitals, name, data.get(name))
    vitals.recorded_by = acting_user
    vitals.recorded_at = now or timezone.now()
    vitals.full_clean()
    vitals.save()
    return vitals
