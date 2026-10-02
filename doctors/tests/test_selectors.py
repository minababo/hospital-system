from datetime import date, time

from accounts.models import Role
from doctors import selectors
from doctors.models import DoctorSchedule, Weekday

MONDAY = date(2026, 10, 5)
TUESDAY = date(2026, 10, 6)


def add_block(doctor, start, end, slot_minutes=15, weekday=Weekday.MONDAY, is_active=True):
    return DoctorSchedule.objects.create(
        doctor=doctor,
        weekday=weekday,
        start_time=time(*start),
        end_time=time(*end),
        slot_minutes=slot_minutes,
        is_active=is_active,
    )


# --- slots_for_date ---------------------------------------------------------


def test_one_hour_block_in_15_minute_slots(make_doctor):
    doctor = make_doctor()
    add_block(doctor, (9, 0), (10, 0), slot_minutes=15)

    assert selectors.slots_for_date(doctor, MONDAY) == [
        time(9, 0),
        time(9, 15),
        time(9, 30),
        time(9, 45),
    ]


def test_two_blocks_merged_and_sorted(make_doctor):
    doctor = make_doctor()
    add_block(doctor, (14, 0), (14, 30), slot_minutes=15)
    add_block(doctor, (9, 0), (9, 30), slot_minutes=15)

    assert selectors.slots_for_date(doctor, MONDAY) == [
        time(9, 0),
        time(9, 15),
        time(14, 0),
        time(14, 15),
    ]


def test_partial_final_slot_is_dropped(make_doctor):
    doctor = make_doctor()
    add_block(doctor, (9, 0), (9, 50), slot_minutes=20)

    assert selectors.slots_for_date(doctor, MONDAY) == [time(9, 0), time(9, 20)]


def test_other_weekday_has_no_slots(make_doctor):
    doctor = make_doctor()
    add_block(doctor, (9, 0), (10, 0))

    assert selectors.slots_for_date(doctor, TUESDAY) == []


def test_inactive_block_is_ignored(make_doctor):
    doctor = make_doctor()
    add_block(doctor, (9, 0), (9, 30), is_active=False)

    assert selectors.slots_for_date(doctor, MONDAY) == []


def test_inactive_doctor_has_no_slots(make_doctor):
    doctor = make_doctor(user__is_active=False)
    add_block(doctor, (9, 0), (10, 0))

    assert selectors.slots_for_date(doctor, MONDAY) == []


# --- lists ------------------------------------------------------------------


def test_doctor_list_search(make_doctor):
    ana = make_doctor(user__first_name="Ana", user__last_name="Silva", specialization="Cardiology")
    make_doctor(user__first_name="Bimal", specialization="Neurology", registration_number="X99")

    assert list(selectors.doctor_list(search="silva")) == [ana]
    assert list(selectors.doctor_list(search="cardio")) == [ana]
    assert [d.registration_number for d in selectors.doctor_list(search="x99")] == ["X99"]


def test_doctor_list_filters(make_doctor, make_department):
    cardiology = make_department(name="Cardiology")
    active = make_doctor(department=cardiology)
    inactive = make_doctor(department=cardiology, user__is_active=False)
    make_doctor()

    assert set(selectors.doctor_list(department=cardiology)) == {active, inactive}
    assert list(selectors.doctor_list(department=cardiology, is_active=True)) == [active]
    assert list(selectors.doctor_list(is_active=False)) == [inactive]


def test_active_doctors_excludes_inactive_user_and_department(make_doctor, make_department):
    bookable = make_doctor()
    make_doctor(user__is_active=False)
    make_doctor(department=make_department(is_active=False))

    assert list(selectors.active_doctors()) == [bookable]


def test_department_list_counts_doctors(make_doctor, make_department):
    cardiology = make_department(name="Cardiology")
    make_doctor(department=cardiology)
    make_doctor(department=cardiology)
    make_department(name="Empty", is_active=False)

    counts = {d.name: d.doctor_count for d in selectors.department_list()}
    assert counts == {"Cardiology": 2, "Empty": 0}
    assert [d.name for d in selectors.department_list(is_active=False)] == ["Empty"]
    assert [d.name for d in selectors.department_list(search="card")] == ["Cardiology"]


def test_doctor_users_without_profile(make_user, make_doctor):
    missing = make_user(role=Role.DOCTOR)
    make_user(role=Role.DOCTOR, is_active=False)
    make_user(role=Role.NURSE)
    make_doctor()

    assert list(selectors.doctor_users_without_profile()) == [missing]


def test_weekly_schedule_groups_by_day(make_doctor):
    doctor = make_doctor()
    monday = add_block(doctor, (9, 0), (12, 0))
    friday = add_block(doctor, (14, 0), (16, 0), weekday=Weekday.FRIDAY)

    week = dict(selectors.doctor_weekly_schedule(doctor))

    assert list(week) == [label for _, label in Weekday.choices]
    assert week["Monday"] == [monday]
    assert week["Friday"] == [friday]
    assert week["Sunday"] == []
