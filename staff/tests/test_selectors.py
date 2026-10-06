from datetime import date, timedelta

import pytest
from django.utils import timezone

from staff import selectors
from staff.models import Attendance

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def test_attendance_sheet_states(make_employee, make_leave, admin_user_obj):
    recorded = make_employee(first_name="A")
    on_leave = make_employee(first_name="B")
    blank = make_employee(first_name="C")
    make_employee(first_name="D", date_joined=TODAY + timedelta(days=1))  # not joined yet
    attendance = Attendance.objects.create(
        employee=recorded, date=TODAY, status="PRESENT", recorded_by=admin_user_obj
    )
    leave = make_leave(employee=on_leave, start=TODAY, end=TODAY)

    rows = {row.employee: row for row in selectors.attendance_sheet(TODAY)}

    assert set(rows) == {recorded, on_leave, blank}
    assert rows[recorded].attendance == attendance and rows[recorded].leave is None
    assert rows[on_leave].leave == leave
    assert rows[blank].attendance is None and rows[blank].leave is None


def test_monthly_summary_clips_leave_to_month(make_employee, make_leave, admin_user_obj):
    employee = make_employee(date_joined=date(2025, 1, 1))
    for day, status in [(3, "PRESENT"), (4, "PRESENT"), (5, "HALF_DAY"), (6, "ABSENT")]:
        Attendance.objects.create(
            employee=employee, date=date(2026, 3, day), status=status, recorded_by=admin_user_obj
        )
    # 28 Feb – 3 Mar: only 1, 2 and 3 March count for March.
    make_leave(employee=employee, start=date(2026, 2, 28), end=date(2026, 3, 3))
    make_leave(employee=employee, start=date(2026, 3, 30), end=date(2026, 4, 2))  # 30, 31
    make_leave(employee=employee, start=date(2026, 3, 20), end=date(2026, 3, 21), status="PENDING")

    [row] = [r for r in selectors.monthly_attendance_summary(2026, 3) if r.employee == employee]

    assert (row.present, row.half_day, row.absent, row.leave_days) == (2, 1, 1, 5)


def test_monthly_summary_only_employees_working_that_month(make_employee):
    current = make_employee(date_joined=date(2025, 1, 1))
    make_employee(date_joined=date(2026, 5, 1))
    make_employee(date_joined=date(2025, 1, 1), status="RESIGNED", end_date=date(2026, 2, 28))

    employees = [row.employee for row in selectors.monthly_attendance_summary(2026, 3)]

    assert employees == [current]


def test_headcount_excludes_ended(make_employee, make_department):
    nursing = make_department(name="Nursing")
    make_employee(department=nursing, category="NURSING")
    make_employee(department=nursing, category="NURSING")
    make_employee(department=nursing, status="RESIGNED", end_date=TODAY)
    make_employee(department=make_department(name="Admin"), category="ADMINISTRATIVE")

    assert selectors.headcount(by="department") == [("Admin", 1), ("Nursing", 2)]
    assert selectors.headcount(by="category") == [("Administrative", 1), ("Nursing", 2)]


def test_leave_taken_by_type(make_leave):
    make_leave(leave_type="SICK", start=date(2026, 12, 30), end=date(2027, 1, 2))  # 2 in 2026
    make_leave(leave_type="ANNUAL", start=date(2026, 5, 1), end=date(2026, 5, 3))
    make_leave(
        leave_type="ANNUAL",
        start=date(2026, 6, 1),
        end=date(2026, 6, 1),
        status="REJECTED",
        decision_note="x",
    )

    totals = selectors.leave_taken_by_type(2026)

    assert totals["SICK"] == 2 and totals["ANNUAL"] == 3 and totals["CASUAL"] == 0


def test_employee_search(make_employee):
    kamal = make_employee(first_name="Kamal", nic="123456789V", phone="0711111111")
    make_employee(first_name="Other")

    for query in ("kamal", kamal.number, "123456789v", "071 111 1111"):
        assert list(selectors.employee_list(q=query)) == [kamal], query


def test_users_without_employee(make_user, make_employee):
    linked, free = make_user(), make_user()
    make_employee(user=linked)

    users = list(selectors.users_without_employee())

    assert free in users and linked not in users
