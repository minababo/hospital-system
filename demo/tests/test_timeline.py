"""The seeded data tells a consistent story in time: things happen in order, at
plausible moments, and nothing is stamped after the seed ran."""

from datetime import timedelta
from io import StringIO

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import models
from django.utils import timezone

from admissions.models import Admission
from appointments.models import Appointment
from audit.models import AuditLog
from billing.models import Charge, Invoice, Payment
from laboratory.models import LabOrder, LabOrderItem, LabResult
from patients.models import Patient
from pharmacy.models import Dispense, DispenseItem, MovementType, StockBatch, StockMovement
from records.models import MedicalRecord, Prescription
from staff.models import Attendance, Employee, LeaveRequest

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "Seed-Demo-Pass-2026!"
MINUTE = timedelta(minutes=1)


def seed():
    call_command("seed_demo", "--password", PASSWORD, "--scale", "0.1", stdout=StringIO())


@pytest.fixture
def seeded():
    seed()
    return timezone.now()  # just after the run: nothing may be later than this


def local(moment):
    return timezone.localtime(moment)


# --- People ------------------------------------------------------------------------------


def test_patients_registered_before_their_first_event_and_mostly_long_ago(seeded):
    patients = list(Patient.objects.all())
    assert patients
    for patient in patients:
        events = [
            *Appointment.objects.filter(patient=patient).values_list("created_at", flat=True),
            *Admission.objects.filter(patient=patient).values_list("admitted_at", flat=True),
            *LabOrder.objects.filter(patient=patient).values_list("created_at", flat=True),
        ]
        if events:
            assert patient.created_at <= min(events), patient
        assert patient.updated_at == patient.created_at
    long_ago = [p for p in patients if p.created_at <= seeded - timedelta(days=60)]
    assert len(long_ago) >= len(patients) // 2  # ~80% registered 2-24 months ago
    assert all(p.created_at >= seeded - timedelta(days=731) for p in patients)
    month_start = local(seeded).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    new_this_month = Patient.objects.filter(created_at__gte=month_start).count()
    assert new_this_month < len(patients)


def test_logins_and_employees_exist_months_before_the_data(seeded):
    data_start = Appointment.objects.order_by("created_at").first().created_at
    for user in User.objects.filter(username__startswith="demo."):
        assert user.date_joined <= data_start - timedelta(days=30), user
    for employee in Employee.objects.all():
        assert employee.created_at <= data_start - timedelta(days=30), employee
        assert local(employee.created_at).date() == employee.date_joined


# --- Appointments and consultations --------------------------------------------------------


def test_appointments_are_booked_before_they_happen(seeded):
    today = local(seeded).date()
    appointments = list(Appointment.objects.all())
    assert appointments
    for appointment in appointments:
        start = appointment.start_datetime
        assert appointment.created_at < start
        assert appointment.created_at <= seeded
        if appointment.date <= today:
            assert start - appointment.created_at <= timedelta(days=11), appointment
        if appointment.checked_in_at:
            assert local(appointment.checked_in_at).date() == appointment.date
            assert appointment.checked_in_at <= start + timedelta(minutes=30)
            assert appointment.checked_in_at >= appointment.created_at
        if appointment.completed_at:
            assert appointment.completed_at >= appointment.checked_in_at
            assert local(appointment.completed_at).date() == appointment.date
        if appointment.cancelled_at:
            assert appointment.created_at <= appointment.cancelled_at <= start
        assert appointment.updated_at >= appointment.created_at


def test_consultations_happen_on_the_day_after_check_in(seeded):
    records = list(MedicalRecord.objects.select_related("appointment"))
    assert records
    for record in records:
        appointment = record.appointment
        assert local(record.created_at).date() == appointment.date
        assert record.created_at >= appointment.checked_in_at
        assert record.finalized_at >= record.created_at
        assert record.updated_at == record.finalized_at
        vitals = appointment.vitals
        assert appointment.checked_in_at <= vitals.recorded_at <= record.created_at
        prescription = Prescription.objects.filter(record=record).first()
        if prescription:
            assert record.created_at <= prescription.created_at <= record.finalized_at
            assert prescription.issued_at == record.finalized_at


# --- Laboratory -----------------------------------------------------------------------------


def test_lab_orders_follow_the_consultation_and_turnaround(seeded):
    orders = list(LabOrder.objects.select_related("record").prefetch_related("items__test"))
    assert orders
    over = within = 0
    for order in orders:
        assert order.created_at <= seeded
        if order.record:
            assert order.record.created_at <= order.created_at <= order.record.finalized_at
        if order.sample_collected_at:
            gap = order.sample_collected_at - order.created_at
            assert timedelta(minutes=10) <= gap <= timedelta(minutes=60), order
            for result in LabResult.objects.filter(item__order=order):
                assert result.entered_at >= order.sample_collected_at
                if order.released_at:
                    assert result.entered_at <= order.released_at
        if order.released_at:
            assert order.released_at > order.sample_collected_at
            target = max(item.test.turnaround_hours for item in order.items.all())
            hours = (order.released_at - order.created_at).total_seconds() / 3600
            if hours > target:
                over += 1
            else:
                within += 1
        if order.cancelled_at:
            assert order.cancelled_at >= order.created_at
    assert over >= 1, "some released orders should be over their turnaround target"
    assert within >= 1


# --- Pharmacy --------------------------------------------------------------------------------


def test_stock_moves_in_time_order_and_dispenses_follow_finalizing(seeded):
    for batch in StockBatch.objects.all():
        movements = list(batch.movements.order_by("created_at", "pk"))
        assert movements[0].movement_type == MovementType.RECEIVE
        assert movements[0].created_at == batch.received_at
        dispenses = [m for m in movements if m.movement_type == MovementType.DISPENSE]
        if dispenses:
            first_use = dispenses[0].created_at - batch.received_at
            assert timedelta(days=28) <= first_use <= timedelta(days=186), batch
    for movement in StockMovement.objects.filter(movement_type=MovementType.DISPENSE):
        assert movement.created_at == movement.dispense_item.dispense.dispensed_at
    dispenses = list(Dispense.objects.select_related("prescription__record"))
    assert dispenses
    for dispense in dispenses:
        gap = dispense.dispensed_at - dispense.prescription.record.finalized_at
        assert timedelta(minutes=10) <= gap <= timedelta(minutes=90), dispense


# --- Billing -----------------------------------------------------------------------------------


def source_time(charge):
    if charge.source_type == "appointment":
        return Appointment.objects.get(pk=charge.source_id).completed_at
    if charge.source_type == "lab_order_item":
        return LabOrderItem.objects.get(pk=charge.source_id).order.created_at
    if charge.source_type == "dispense_item":
        return DispenseItem.objects.get(pk=charge.source_id).dispense.dispensed_at
    from admissions.models import BedAssignment

    return BedAssignment.objects.get(pk=charge.source_id).ended_at


def test_charges_invoices_and_payments_are_in_order(seeded):
    seen = set()
    for charge in Charge.objects.all():
        assert charge.created_at == source_time(charge), charge
        seen.add(charge.source_type)
    assert {"appointment", "lab_order_item", "dispense_item", "bed_assignment"} <= seen
    for invoice in Invoice.objects.prefetch_related("charges", "payments"):
        charges = [c.created_at for c in invoice.charges.all()]
        if charges:  # a voided invoice has released its charges
            last_charge = max(charges)
            assert last_charge <= invoice.created_at <= last_charge + timedelta(days=1), invoice
        if invoice.issued_at:
            assert (
                invoice.created_at <= invoice.issued_at <= invoice.created_at + timedelta(hours=1)
            )
        if invoice.discount_set_at:
            assert invoice.created_at <= invoice.discount_set_at <= invoice.issued_at
        if invoice.voided_at:
            assert invoice.voided_at > invoice.issued_at
        for payment in invoice.payments.all():
            assert payment.received_at >= invoice.issued_at
            if payment.voided_at:
                assert payment.voided_at > payment.received_at
        assert invoice.updated_at >= invoice.created_at
    # The planned aging spread: open invoices issued more than 30 and 60 days ago.
    open_issued = Invoice.objects.filter(status__in=["ISSUED", "PARTIALLY_PAID"])
    assert open_issued.filter(issued_at__lt=seeded - timedelta(days=30)).exists()
    assert open_issued.filter(issued_at__lt=seeded - timedelta(days=60)).exists()
    assert Payment.objects.filter(is_voided=True).exists()


# --- Admissions and staff ------------------------------------------------------------------------


def test_admissions_notes_and_beds_line_up(seeded):
    admissions = list(Admission.objects.prefetch_related("notes", "assignments"))
    assert admissions
    for admission in admissions:
        assert admission.created_at == admission.admitted_at
        end = admission.discharged_at or seeded
        for note in admission.notes.all():
            assert admission.admitted_at <= note.created_at <= end
        assignments = sorted(admission.assignments.all(), key=lambda a: a.started_at)
        assert assignments[0].started_at == admission.admitted_at
        for previous, following in zip(assignments, assignments[1:], strict=False):
            assert previous.ended_at == following.started_at
        if admission.discharged_at:
            assert assignments[-1].ended_at == admission.discharged_at
        else:
            assert assignments[-1].ended_at is None


def test_attendance_and_leave_timeline(seeded):
    sheets = list(Attendance.objects.all())
    assert sheets
    for record in sheets:
        assert local(record.recorded_at).date() == record.date
        assert record.updated_at == record.recorded_at
    leave = list(LeaveRequest.objects.all())
    assert leave
    for request in leave:
        assert request.requested_at <= seeded
        if request.decided_at:
            assert request.requested_at <= request.decided_at <= seeded
        if request.status == "APPROVED":
            assert local(request.decided_at).date() < request.start_date


# --- The whole run ----------------------------------------------------------------------


SEEDED_APPS = [
    "accounts",
    "doctors",
    "patients",
    "appointments",
    "records",
    "laboratory",
    "pharmacy",
    "billing",
    "admissions",
    "staff",
    "audit",
]


def test_nothing_is_stamped_after_the_run(seeded):
    for label in SEEDED_APPS:
        for model in apps.get_app_config(label).get_models():
            for column in model._meta.concrete_fields:
                if isinstance(column, models.DateTimeField):
                    latest = model.objects.aggregate(latest=models.Max(column.name))["latest"]
                    assert latest is None or latest <= seeded, f"{model.__name__}.{column.name}"


MAIN_MODELS = [
    User,
    Patient,
    Appointment,
    MedicalRecord,
    Prescription,
    LabOrder,
    StockMovement,
    Dispense,
    Charge,
    Invoice,
    Payment,
    Admission,
    Employee,
    Attendance,
    LeaveRequest,
]


def snapshot():
    rows = {}
    for model in MAIN_MODELS:
        names = [f.name for f in model._meta.concrete_fields if isinstance(f, models.DateTimeField)]
        rows[model.__name__] = sorted(model._base_manager.values_list("pk", *names))
    return rows


def test_second_run_changes_nothing_and_logs_one_summary():
    seed()
    before = snapshot()
    seed()
    assert snapshot() == before
    summaries = AuditLog.objects.filter(event="demo.seed.completed")
    assert summaries.count() == 1
    entry = summaries.get()
    assert entry.actor.username == "demo.admin"
    assert entry.message.startswith("Demo data seeded: ")
    assert "patients" in entry.message and "appointments" in entry.message
