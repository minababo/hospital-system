from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import PermissionDenied, ValidationError

from accounts.models import Role
from admissions import services
from admissions.models import AdmissionStatus, BedAssignment
from admissions.tests.clock import NOW, colombo
from billing.models import Charge

pytestmark = pytest.mark.django_db


@pytest.fixture
def nurse(make_user):
    return make_user(role=Role.NURSE)


@pytest.fixture
def doctor(make_doctor):
    return make_doctor()


def admit(patient, bed, doctor, user, **kwargs):
    kwargs.setdefault("source", "DIRECT")
    kwargs.setdefault("now", NOW)
    return services.admit_patient(
        patient=patient,
        bed=bed,
        admitting_doctor=doctor,
        reason="Community-acquired pneumonia",
        acting_user=user,
        **kwargs,
    )


def bed_charges():
    return list(Charge.objects.filter(source_type="bed_assignment").order_by("pk"))


# --- Admitting ------------------------------------------------------------------------


def test_admit_creates_first_assignment_with_rate_snapshot(
    make_patient, make_bed, make_ward, doctor, nurse
):
    ward = make_ward(daily_rate="4500")
    bed = make_bed(ward=ward)

    admission = admit(make_patient(), bed, doctor, nurse)
    ward.daily_rate = Decimal("9999")
    ward.save()

    assignment = admission.assignments.get()
    assert admission.status == AdmissionStatus.ADMITTED
    assert admission.admitted_at == NOW and admission.admitted_by == nurse
    assert (assignment.bed, assignment.daily_rate, assignment.ended_at) == (
        bed,
        Decimal("4500.00"),
        None,
    )


def test_admit_rejects_already_admitted_patient(
    make_admission, make_patient, make_bed, doctor, nurse
):
    patient = make_patient()
    current = make_admission(patient=patient)

    with pytest.raises(ValidationError, match=f"already admitted \\({current.number}\\)"):
        admit(patient, make_bed(), doctor, nurse)


def test_admit_rejects_occupied_bed(make_admission, make_patient, make_bed, doctor, nurse):
    bed = make_bed()
    make_admission(bed=bed)

    with pytest.raises(ValidationError, match="already occupied"):
        admit(make_patient(), bed, doctor, nurse)


@pytest.mark.parametrize("what", ["bed", "ward"])
def test_admit_rejects_inactive_bed_or_ward(make_patient, make_bed, doctor, nurse, what):
    bed = make_bed()
    target = bed if what == "bed" else bed.ward
    target.is_active = False
    target.save()

    with pytest.raises(ValidationError, match="not in use"):
        admit(make_patient(), bed, doctor, nurse)


def test_admit_rejects_inactive_doctor(make_patient, make_bed, doctor, nurse):
    doctor.user.is_active = False
    doctor.user.save()

    with pytest.raises(ValidationError, match="not active"):
        admit(make_patient(), make_bed(), doctor, nurse)


@pytest.mark.parametrize(
    ("admitted_at", "message"),
    [
        (NOW + timedelta(minutes=1), "future"),
        (NOW - timedelta(days=8), "more than 7 days ago"),
    ],
)
def test_admit_time_rules(make_patient, make_bed, doctor, nurse, admitted_at, message):
    with pytest.raises(ValidationError, match=message):
        admit(make_patient(), make_bed(), doctor, nurse, admitted_at=admitted_at)


def test_admit_with_appointment_rules(make_patient, make_bed, make_appointment, doctor, nurse):
    patient = make_patient()
    own = make_appointment(patient=patient, status="COMPLETED")
    other = make_appointment(status="COMPLETED", start_time=own.start_time.replace(hour=10))

    with pytest.raises(ValidationError, match="another patient"):
        admit(patient, make_bed(), doctor, nurse, appointment=other, source="OPD")
    with pytest.raises(ValidationError, match="OPD source"):
        admit(patient, make_bed(), doctor, nurse, appointment=own, source="DIRECT")
    with pytest.raises(ValidationError, match="Choose the outpatient appointment"):
        admit(patient, make_bed(), doctor, nurse, source="OPD")

    admission = admit(patient, make_bed(), doctor, nurse, appointment=own, source="OPD")
    assert admission.appointment == own


def test_bed_race_gives_friendly_error(
    make_admission, make_patient, make_bed, doctor, nurse, monkeypatch
):
    # Simulate a race: our availability check didn't see the other admission yet.
    bed = make_bed()
    make_admission(bed=bed)
    monkeypatch.setattr(services, "_check_bed_available", lambda bed: None)

    with pytest.raises(ValidationError, match="just taken"):
        admit(make_patient(), bed, doctor, nurse)


# --- Transfers and discharge -------------------------------------------------------------


@pytest.fixture
def two_wards(make_ward, make_bed):
    return make_bed(ward=make_ward(daily_rate="1000")), make_bed(ward=make_ward(daily_rate="3000"))


def test_transfer_closes_old_opens_new_and_bills_old_nights(make_admission, two_wards, nurse):
    bed_a, bed_b = two_wards
    admission = make_admission(bed=bed_a, admitted_at=colombo(2026, 10, 8, 12))

    new = services.transfer_bed(
        admission,
        new_bed=bed_b,
        transferred_at=colombo(2026, 10, 9, 12),
        acting_user=nurse,
        now=NOW,
    )

    old = admission.assignments.get(bed=bed_a)
    assert old.ended_at == colombo(2026, 10, 9, 12)
    assert (new.bed, new.daily_rate, new.ended_at) == (bed_b, Decimal("3000.00"), None)
    [charge] = bed_charges()
    assert (charge.charge_type, charge.quantity, charge.unit_price) == (
        "ADMISSION",
        1,
        Decimal("1000.00"),
    )


def test_same_day_transfer_bills_nothing(make_admission, two_wards, nurse):
    bed_a, bed_b = two_wards
    admission = make_admission(bed=bed_a, admitted_at=NOW - timedelta(hours=2))

    services.transfer_bed(admission, new_bed=bed_b, acting_user=nurse, now=NOW)

    assert bed_charges() == []


def test_transfer_rules(make_admission, make_bed, two_wards, nurse):
    bed_a, bed_b = two_wards
    admission = make_admission(bed=bed_a, admitted_at=NOW - timedelta(days=1))
    occupied = make_bed()
    make_admission(bed=occupied)

    with pytest.raises(ValidationError, match="already in this bed"):
        services.transfer_bed(admission, new_bed=bed_a, acting_user=nurse, now=NOW)
    with pytest.raises(ValidationError, match="already occupied"):
        services.transfer_bed(admission, new_bed=occupied, acting_user=nurse, now=NOW)
    with pytest.raises(ValidationError, match="can't be before"):
        services.transfer_bed(
            admission,
            new_bed=bed_b,
            transferred_at=NOW - timedelta(days=2),
            acting_user=nurse,
            now=NOW,
        )


def discharge(admission, user, **kwargs):
    kwargs.setdefault("discharge_type", "HOME")
    kwargs.setdefault("discharge_summary", "Recovered. Review in clinic in 2 weeks.")
    kwargs.setdefault("now", NOW)
    return services.discharge_patient(admission, acting_user=user, **kwargs)


def test_three_nights_with_transfer_after_one(make_admission, two_wards, doctor, nurse):
    bed_a, bed_b = two_wards
    admission = make_admission(bed=bed_a, admitted_at=colombo(2026, 10, 7, 12))
    services.transfer_bed(
        admission,
        new_bed=bed_b,
        transferred_at=colombo(2026, 10, 8, 12),
        acting_user=nurse,
        now=NOW,
    )

    discharge(admission, doctor.user, discharged_at=colombo(2026, 10, 10, 9))

    charges = [(c.quantity, c.unit_price, c.amount) for c in bed_charges()]
    assert charges == [
        (1, Decimal("1000.00"), Decimal("1000.00")),
        (2, Decimal("3000.00"), Decimal("6000.00")),
    ]
    admission.refresh_from_db()
    assert admission.status == AdmissionStatus.DISCHARGED
    assert admission.discharged_by == doctor.user


def test_same_day_stay_is_billed_one_day_at_last_rate(make_admission, two_wards, doctor, nurse):
    bed_a, bed_b = two_wards
    admission = make_admission(bed=bed_a, admitted_at=NOW - timedelta(hours=5))
    services.transfer_bed(admission, new_bed=bed_b, acting_user=nurse, now=NOW - timedelta(hours=2))

    discharge(admission, doctor.user)

    [charge] = bed_charges()
    assert (charge.quantity, charge.unit_price) == (1, Decimal("3000.00"))


def test_discharge_rules(make_admission, doctor, nurse):
    admission = make_admission(admitted_at=NOW - timedelta(days=1))

    with pytest.raises(PermissionDenied):
        discharge(admission, nurse)
    with pytest.raises(ValidationError, match="discharge summary"):
        discharge(admission, doctor.user, discharge_summary="  ")
    with pytest.raises(ValidationError, match="Choose how"):
        discharge(admission, doctor.user, discharge_type="")

    discharge(admission, doctor.user)
    with pytest.raises(ValidationError, match="already been discharged"):
        discharge(admission, doctor.user)
    assert len(bed_charges()) == 1


def test_bed_charges_are_idempotent(make_admission, doctor):
    admission = make_admission(admitted_at=NOW - timedelta(days=2))
    discharge(admission, doctor.user)
    assignment = BedAssignment.objects.get(admission=admission)

    services._post_assignment_charge(assignment, acting_user=doctor.user)

    assert len(bed_charges()) == 1


# --- Notes and ward/bed management -----------------------------------------------------


def test_progress_notes_only_while_admitted(make_admission, doctor, nurse):
    admission = make_admission(admitted_at=NOW - timedelta(days=1))

    note = services.add_progress_note(
        admission, note_type="NURSING", text=" Afebrile ", acting_user=nurse
    )
    assert note.text == "Afebrile"
    with pytest.raises(ValidationError):
        services.add_progress_note(admission, note_type="NURSING", text=" ", acting_user=nurse)

    discharge(admission, doctor.user)
    admission.refresh_from_db()
    with pytest.raises(ValidationError, match="while the patient is admitted"):
        services.add_progress_note(admission, note_type="OTHER", text="Late", acting_user=nurse)


def test_cannot_deactivate_occupied_ward_or_bed(make_admission, make_bed, admin_user_obj):
    bed = make_bed()
    make_admission(bed=bed)

    with pytest.raises(ValidationError, match="occupied"):
        services.set_bed_active(bed, False, acting_user=admin_user_obj)
    with pytest.raises(ValidationError, match="patients in it"):
        services.set_ward_active(bed.ward, False, acting_user=admin_user_obj)

    free = make_bed()
    services.set_bed_active(free, False, acting_user=admin_user_obj)
    free.refresh_from_db()
    assert free.is_active is False
