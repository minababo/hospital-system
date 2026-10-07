"""What each module writes to the audit log: one entry per successful action, with the
right action, event and patient; nothing when the action fails."""

from datetime import time, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone

from accounts import services as account_services
from accounts.models import Role
from admissions import services as admission_services
from admissions.tests.clock import NOW as ADMISSION_NOW
from appointments import services as appointment_services
from appointments.tests.clock import MONDAY, MONDAY_8AM
from audit.models import Action, AuditLog
from billing import services as billing_services
from billing.models import ChargeType
from doctors import services as doctor_services
from laboratory import services as lab_services
from patients import services as patient_services
from pharmacy import dispensing
from pharmacy import services as stock_services
from records import services as record_services
from staff import services as staff_services

pytestmark = pytest.mark.django_db


def only(event):
    entries = list(AuditLog.objects.filter(event=event))
    assert len(entries) == 1, f"expected one {event}, got {len(entries)}"
    return entries[0]


@pytest.fixture
def admin(admin_user_obj):
    return admin_user_obj


# --- accounts --------------------------------------------------------------------------


def test_user_created_updated_and_deactivated(admin):
    user = account_services.create_user(
        username="nurse_x",
        password="S3cret-Passphrase!",
        role=Role.NURSE,
        first_name="Ama",
        last_name="Fernando",
        email="ama@example.com",
        acting_user=admin,
    )
    created = only("accounts.user.created")
    assert created.action == Action.CREATE and created.actor == admin
    assert created.changes["username"] == [None, "nurse_x"]

    account_services.update_user(user, first_name="Amali", acting_user=admin)
    updated = only("accounts.user.updated")
    assert updated.changes == {"first_name": ["Ama", "Amali"]}  # only what changed

    account_services.update_user(user, role=Role.RECEPTIONIST, acting_user=admin)
    assert only("accounts.user.role_changed").changes == {"role": [Role.NURSE, Role.RECEPTIONIST]}

    account_services.set_user_active(user, False, acting_user=admin)
    entry = only("accounts.user.deactivated")
    assert entry.action == Action.STATUS_CHANGE
    assert entry.changes == {"is_active": [True, False]}


def test_no_entry_ever_contains_a_password(admin, client, user_password):
    secret = "Pl41n-Text-Secret!"
    user = account_services.create_user(
        username="acc_x",
        password=secret,
        role=Role.ACCOUNTANT,
        first_name="A",
        last_name="B",
        email="",
        acting_user=admin,
    )
    account_services.set_user_password(user, secret + "2", acting_user=admin)
    client.post("/accounts/login/", {"username": "acc_x", "password": secret})

    for entry in AuditLog.objects.all():
        assert "password" not in entry.changes
        text = str(entry.changes) + entry.message + entry.object_repr
        assert secret not in text
        assert user.password not in text  # not even the hash


# --- doctors ---------------------------------------------------------------------------


def test_department_created_and_updated(admin):
    department = doctor_services.create_department(
        name="Neurology", description="", acting_user=admin
    )
    assert only("doctors.department.created").object_id == str(department.pk)

    doctor_services.update_department(department, description="Brain", acting_user=admin)
    assert only("doctors.department.updated").changes == {"description": ["", "Brain"]}


def test_failed_create_doctor_rolls_back_the_user_entry(admin, make_doctor):
    existing = make_doctor()
    with pytest.raises(ValidationError):
        doctor_services.create_doctor(
            user_data={
                "username": "doc_x",
                "password": "S3cret-Passphrase!",
                "first_name": "D",
                "last_name": "X",
                "email": "",
            },
            profile_data={
                "department": existing.department,
                "specialization": "Surgery",
                # Already used: the profile fails after create_user has logged.
                "registration_number": existing.registration_number,
                "phone": "0771234567",
                "consultation_fee": Decimal("1000.00"),
            },
            acting_user=admin,
        )
    assert not AuditLog.objects.filter(event="accounts.user.created").exists()
    assert not AuditLog.objects.filter(event="doctors.doctor.created").exists()


# --- patients --------------------------------------------------------------------------


def test_patient_updated_logs_only_changed_fields(make_patient, admin):
    patient = make_patient(phone="0771111111")
    patient_services.update_patient(patient, data={"phone": "0772222222"}, acting_user=admin)
    entry = only("patients.patient.updated")
    assert entry.action == Action.UPDATE and entry.patient == patient
    assert entry.changes == {"phone": ["0771111111", "0772222222"]}


def test_document_uploaded_and_deleted(make_patient, make_upload, admin):
    patient = make_patient()
    document = patient_services.upload_document(
        patient=patient,
        file=make_upload("x-ray.pdf"),
        category="IMAGING",
        description="",
        acting_user=admin,
    )
    uploaded = only("patients.document.uploaded")
    assert uploaded.patient == patient and uploaded.action == Action.CREATE
    patient_services.delete_document(document, acting_user=admin)
    deleted = only("patients.document.deleted")
    assert deleted.action == Action.DELETE and deleted.object_id == str(document.pk)


# --- appointments ----------------------------------------------------------------------


def test_booking_and_cancelling(make_scheduled_doctor, make_patient, make_user):
    receptionist = make_user(role=Role.RECEPTIONIST)
    patient = make_patient()
    appointment = appointment_services.book_appointment(
        patient=patient,
        doctor=make_scheduled_doctor(),
        date=MONDAY,
        start_time=time(9),
        reason="Fever",
        acting_user=receptionist,
        now=MONDAY_8AM,
    )
    booked = only("appointments.appointment.booked")
    assert booked.action == Action.CREATE and booked.patient == patient

    appointment_services.cancel_appointment(
        appointment, reason="Patient called", acting_user=receptionist, now=MONDAY_8AM
    )
    cancelled = only("appointments.appointment.cancelled")
    assert cancelled.action == Action.STATUS_CHANGE
    assert cancelled.changes == {"status": ["BOOKED", "CANCELLED"]}
    assert cancelled.message == "Cancelled: Patient called"


def test_booking_a_taken_slot_logs_nothing(make_scheduled_doctor, make_patient, make_user):
    receptionist = make_user(role=Role.RECEPTIONIST)
    doctor = make_scheduled_doctor()
    kwargs = {"doctor": doctor, "date": MONDAY, "start_time": time(9), "reason": "Fever"}
    appointment_services.book_appointment(
        patient=make_patient(), acting_user=receptionist, now=MONDAY_8AM, **kwargs
    )
    with pytest.raises(ValidationError):
        appointment_services.book_appointment(
            patient=make_patient(), acting_user=receptionist, now=MONDAY_8AM, **kwargs
        )
    assert AuditLog.objects.filter(event="appointments.appointment.booked").count() == 1


# --- records ---------------------------------------------------------------------------


def test_consultation_started_and_finalized(make_checked_in_appointment):
    appointment = make_checked_in_appointment()
    doctor_user = appointment.doctor.user
    record = record_services.start_consultation(appointment=appointment, acting_user=doctor_user)
    started = only("records.record.started")
    assert started.patient == appointment.patient and started.action == Action.CREATE

    record_services.add_diagnosis(
        record,
        data={"description": "Asthma", "diagnosis_type": "PRIMARY"},
        acting_user=doctor_user,
    )
    assert only("records.diagnosis.added").patient == appointment.patient

    record_services.finalize_record(record, acting_user=doctor_user)
    finalized = only("records.record.finalized")
    assert finalized.changes["status"] == ["DRAFT", "FINALIZED"]
    # Finalizing completes the visit; that is the appointments module's own entry.
    assert only("appointments.appointment.completed").patient == appointment.patient


# --- billing ---------------------------------------------------------------------------


def test_post_charge_logs_only_the_first_time(make_patient, make_user):
    patient = make_patient()
    kwargs = {
        "patient": patient,
        "charge_type": ChargeType.OTHER,
        "description": "Dressing",
        "quantity": 1,
        "unit_price": Decimal("500.00"),
        "source_type": "test_source",
        "source_id": 1,
        "acting_user": make_user(role=Role.NURSE),
    }
    billing_services.post_charge(**kwargs)
    billing_services.post_charge(**kwargs)  # idempotent: returns the same charge
    entry = only("billing.charge.posted")
    assert entry.patient == patient and entry.action == Action.CREATE


def test_invoice_issued_and_paid(make_patient, make_charge, make_user):
    accountant = make_user(role=Role.ACCOUNTANT)
    patient = make_patient()
    charge = make_charge(patient=patient, unit_price="1000.00")
    invoice = billing_services.create_invoice(
        patient=patient, charge_ids=[charge.pk], acting_user=accountant
    )
    billing_services.issue_invoice(invoice, acting_user=accountant)
    billing_services.record_payment(
        invoice=invoice, amount="1000.00", method="CARD", reference="TXN-1", acting_user=accountant
    )
    assert only("billing.invoice.created").patient == patient
    assert only("billing.invoice.issued").action == Action.STATUS_CHANGE
    paid = only("billing.payment.recorded")
    assert paid.patient == patient
    assert paid.message.startswith("Paid Rs. 1,000.00 by Card")


# --- laboratory ------------------------------------------------------------------------


def test_walk_in_order_created(make_patient, make_lab_test, make_user):
    patient = make_patient()
    test = make_lab_test()
    order = lab_services.create_walk_in_order(
        patient=patient,
        test_ids=[test.pk],
        referred_by="Dr. External",
        priority="ROUTINE",
        clinical_notes="",
        acting_user=make_user(role=Role.LAB_STAFF),
    )
    entry = only("laboratory.order.created")
    assert entry.patient == patient and entry.object_id == str(order.pk)
    assert only("billing.charge.posted").patient == patient  # one per test, logged by billing


# --- pharmacy --------------------------------------------------------------------------


def test_stock_received(make_medicine, make_user):
    medicine = make_medicine()
    batch = stock_services.receive_stock(
        medicine=medicine,
        batch_number="LOT-9",
        expiry_date=timezone.localdate() + timedelta(days=200),
        quantity=40,
        supplier="",
        notes="",
        acting_user=make_user(role=Role.PHARMACIST),
    )
    entry = only("pharmacy.batch.received")
    assert entry.object_id == str(batch.pk) and entry.patient is None
    assert "40" in entry.message


def test_dispensing_logs_one_entry_with_the_patient(
    make_issued_prescription, make_batch, make_medicine, make_user
):
    medicine = make_medicine(unit_price=Decimal("10.00"))
    make_batch(medicine=medicine, qty=50)
    prescription = make_issued_prescription(items=[(medicine, 10)])
    item = prescription.items.get()
    dispensing.dispense_prescription(
        prescription=prescription,
        quantities={item.pk: 10},
        notes="",
        acting_user=make_user(role=Role.PHARMACIST),
    )
    entry = only("pharmacy.prescription.dispensed")
    assert entry.patient == prescription.patient and entry.action == Action.CREATE
    assert only("records.prescription.dispensing_updated").patient == prescription.patient


def test_failed_dispensing_leaves_no_audit_rows(
    make_issued_prescription, make_batch, make_medicine, make_user, monkeypatch
):
    first, second = make_medicine(unit_price=Decimal("10.00")), make_medicine()
    make_batch(medicine=first, qty=50)
    make_batch(medicine=second, qty=50)
    prescription = make_issued_prescription(items=[(first, 5), (second, 5)])
    items = list(prescription.items.order_by("id"))
    real_allocate = stock_services.allocate_and_issue
    calls = []

    def allocate_then_fail(medicine, quantity, **kwargs):
        # The first item is issued and billed (billing logs it); the second runs out
        # of stock under the row lock, which must roll back everything.
        calls.append(medicine)
        if len(calls) == 2:
            raise ValidationError("Not enough stock.")
        return real_allocate(medicine, quantity, **kwargs)

    monkeypatch.setattr(stock_services, "allocate_and_issue", allocate_then_fail)
    with pytest.raises(ValidationError):
        dispensing.dispense_prescription(
            prescription=prescription,
            quantities={items[0].pk: 5, items[1].pk: 5},
            notes="",
            acting_user=make_user(role=Role.PHARMACIST),
        )
    assert len(calls) == 2
    assert not AuditLog.objects.exists()


# --- admissions ------------------------------------------------------------------------


def test_admitted_and_discharged(make_patient, make_bed, make_doctor, make_user):
    patient = make_patient()
    bed = make_bed()
    doctor = make_doctor()
    admission = admission_services.admit_patient(
        patient=patient,
        bed=bed,
        admitting_doctor=doctor,
        reason="Pneumonia",
        source="DIRECT",
        admitted_at=ADMISSION_NOW - timedelta(days=2),
        acting_user=make_user(role=Role.NURSE),
        now=ADMISSION_NOW,
    )
    admitted = only("admissions.admission.admitted")
    assert admitted.patient == patient and admitted.action == Action.CREATE
    assert admitted.changes["bed"][1] == f"{bed} (#{bed.pk})"

    admission_services.discharge_patient(
        admission,
        discharge_type="HOME",
        discharge_summary="Recovered.",
        acting_user=doctor.user,
        now=ADMISSION_NOW,
    )
    discharged = only("admissions.admission.discharged")
    assert discharged.action == Action.STATUS_CHANGE and discharged.patient == patient
    assert discharged.changes["status"] == ["ADMITTED", "DISCHARGED"]
    assert only("billing.charge.posted").patient == patient  # the bed charge


# --- staff -----------------------------------------------------------------------------


def test_attendance_sheet_logs_one_entry_per_changed_row(make_employee, admin):
    a, b = make_employee(), make_employee()
    today = timezone.localdate()

    def save(rows):
        staff_services.save_attendance_sheet(date=today, rows=rows, acting_user=admin)

    save([{"employee_id": a.pk, "status": "PRESENT"}, {"employee_id": b.pk, "status": "ABSENT"}])
    assert AuditLog.objects.filter(event="staff.attendance.recorded").count() == 2

    # Same sheet again with one change: only that row is logged.
    save([{"employee_id": a.pk, "status": "PRESENT"}, {"employee_id": b.pk, "status": "PRESENT"}])
    entry = only("staff.attendance.updated")
    assert entry.changes == {"status": ["ABSENT", "PRESENT"]}


def test_leave_recorded_then_cancelled(make_employee, admin):
    employee = make_employee()
    start = timezone.localdate() + timedelta(days=5)
    leave = staff_services.record_leave(
        employee=employee,
        leave_type="ANNUAL",
        start_date=start,
        end_date=start + timedelta(days=1),
        reason="Holiday",
        acting_user=admin,
    )
    assert only("staff.leave.recorded").changes == {"status": [None, "APPROVED"]}
    staff_services.cancel_leave(leave, acting_user=admin)
    assert only("staff.leave.cancelled").changes == {"status": ["APPROVED", "CANCELLED"]}
