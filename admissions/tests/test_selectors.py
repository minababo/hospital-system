from datetime import timedelta

import pytest
from django.utils import timezone

from accounts.models import Role
from admissions import selectors, services
from admissions.models import Admission
from patients.selectors import patient_history

pytestmark = pytest.mark.django_db


def test_free_beds(make_bed, make_ward, make_admission):
    ward = make_ward()
    free = make_bed(ward=ward)
    occupied = make_bed(ward=ward)
    make_admission(bed=occupied)
    make_bed(ward=ward, is_active=False)
    make_bed(ward=make_ward(is_active=False))

    assert list(selectors.free_beds()) == [free]
    assert selectors.free_beds_by_ward() == [(ward, [free])]


def test_bed_board_states_and_occupancy(make_bed, make_ward, make_admission):
    ward = make_ward()
    free = make_bed(ward=ward, bed_number="A1")
    taken = make_bed(ward=ward, bed_number="A2")
    off = make_bed(ward=ward, bed_number="A3", is_active=False)
    admission = make_admission(bed=taken)

    [(board_ward, tiles)] = selectors.bed_board()
    states = {tile.bed: tile.state for tile in tiles}

    assert board_ward == ward
    assert states == {free: "FREE", taken: "OCCUPIED", off: "INACTIVE"}
    assert next(t for t in tiles if t.bed == taken).admission == admission
    assert selectors.occupancy_summary() == {"total": 2, "occupied": 1, "free": 1, "percent": 50}


def test_occupancy_with_no_beds():
    assert selectors.occupancy_summary() == {"total": 0, "occupied": 0, "free": 0, "percent": 0}


def test_current_and_discharged_lists(
    make_admission, make_bed, make_ward, make_doctor, make_patient
):
    icu = make_ward(name="ICU")
    in_icu = make_admission(bed=make_bed(ward=icu), patient=make_patient(first_name="Kamal"))
    elsewhere = make_admission(admitted_at=timezone.now() - timedelta(days=1))
    services.discharge_patient(
        elsewhere,
        discharge_type="HOME",
        discharge_summary="Well",
        acting_user=make_doctor().user,
    )

    assert list(selectors.current_admissions()) == [in_icu]
    assert list(selectors.current_admissions(ward=icu)) == [in_icu]
    assert list(selectors.current_admissions(q="kamal")) == [in_icu]
    assert list(selectors.current_admissions(q=in_icu.number)) == [in_icu]
    today = timezone.localdate()
    assert list(selectors.discharged_admissions(date_from=today, date_to=today)) == [
        Admission.objects.get(pk=elsewhere.pk)
    ]


def test_outpatients_today(make_appointment, make_user):
    today = make_appointment(date=timezone.localdate())
    make_appointment(date=timezone.localdate() + timedelta(days=1))

    assert list(selectors.outpatients_today(make_user(role=Role.RECEPTIONIST))) == [today]


def test_history_events(make_admission, make_bed, make_ward, make_doctor, make_user):
    ward_b = make_ward(name="Ward B")
    admission = make_admission(admitted_at=timezone.now() - timedelta(days=1))
    services.transfer_bed(
        admission,
        new_bed=make_bed(ward=ward_b, bed_number="B7"),
        acting_user=make_user(role=Role.NURSE),
    )
    services.discharge_patient(
        admission, discharge_type="HOME", discharge_summary="Well", acting_user=make_doctor().user
    )

    titles = [event.title for event in patient_history(admission.patient)]

    assert any(t.startswith(f"Admitted ({admission.number}) to ") for t in titles)
    assert f"Transferred to Ward B/B7 ({admission.number})" in titles
    assert "Discharged (Discharged home)" in titles
