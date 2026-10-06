import re
from datetime import time, timedelta

import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from accounts.models import Role
from staff import services
from staff.models import Attendance, LeaveRequest, LeaveStatus
from staff.selectors import attendance_sheet

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def days(n):
    return TODAY + timedelta(days=n)


# --- Employees ------------------------------------------------------------------------


def employee_data(department, **overrides):
    data = {
        "first_name": "Nimal",
        "last_name": "Perera",
        "nic": None,
        "phone": "077 123 4567",
        "email": "",
        "address": "",
        "designation": "Staff Nurse",
        "category": "NURSING",
        "employment_type": "PERMANENT",
        "department": department,
        "date_joined": days(-30),
        "user": None,
    }
    data.update(overrides)
    return data


def test_create_and_link_login(make_department, make_user, admin_user_obj):
    user = make_user(role=Role.NURSE)

    employee = services.create_employee(
        acting_user=admin_user_obj, **employee_data(make_department(), user=user)
    )

    assert employee.phone == "0771234567"
    assert user.employee_profile == employee


def test_login_already_linked_gives_friendly_error(
    make_department, make_employee, make_user, admin_user_obj
):
    user = make_user()
    first = make_employee(user=user)
    other = make_employee()

    with pytest.raises(ValidationError, match=re.escape(f"already linked to {first}")):
        services.create_employee(
            acting_user=admin_user_obj, **employee_data(make_department(), user=user)
        )
    with pytest.raises(ValidationError, match="already linked"):
        services.update_employee(other, user=user, acting_user=admin_user_obj)


def test_end_employment(make_employee, make_leave, admin_user_obj):
    employee = make_employee()
    pending = make_leave(employee=employee, status="PENDING")

    services.end_employment(
        employee, status="RESIGNED", end_date=days(5), acting_user=admin_user_obj
    )

    employee.refresh_from_db()
    pending.refresh_from_db()
    assert (employee.status, employee.end_date) == ("RESIGNED", days(5))
    assert pending.status == LeaveStatus.CANCELLED
    assert employee not in [row.employee for row in attendance_sheet(days(6))]
    assert employee in [row.employee for row in attendance_sheet(days(5))]


def test_end_employment_rules(make_employee, admin_user_obj):
    employee = make_employee(date_joined=days(-10))
    Attendance.objects.create(
        employee=employee, date=days(-1), status="PRESENT", recorded_by=admin_user_obj
    )

    for end_date, message in [
        (days(-11), "before the joining date"),
        (days(31), "at most 30 days ahead"),
        (days(-2), "Attendance is recorded after this date"),
    ]:
        with pytest.raises(ValidationError, match=message):
            services.end_employment(
                employee, status="TERMINATED", end_date=end_date, acting_user=admin_user_obj
            )

    services.end_employment(
        employee, status="TERMINATED", end_date=TODAY, acting_user=admin_user_obj
    )
    with pytest.raises(ValidationError, match="already left"):
        services.end_employment(
            employee, status="RESIGNED", end_date=TODAY, acting_user=admin_user_obj
        )


# --- Attendance sheet -----------------------------------------------------------------


def row(employee, status="PRESENT", **kwargs):
    return {"employee_id": employee.pk, "status": status, **kwargs}


def save(rows, user, day=TODAY):
    return services.save_attendance_sheet(date=day, rows=rows, acting_user=user)


def test_sheet_saves_and_updates(make_employee, admin_user_obj):
    a, b, c = make_employee(), make_employee(), make_employee()

    save(
        [row(a, check_in=time(8), check_out=time(16)), row(b, "ABSENT"), row(c, "")], admin_user_obj
    )
    save([row(b, "HALF_DAY", check_in=time(8), check_out=time(12))], admin_user_obj)

    records = {r.employee: r for r in Attendance.objects.all()}
    assert set(records) == {a, b}  # blank status: nothing saved for c
    assert records[b].status == "HALF_DAY"
    assert Attendance.objects.filter(employee=b).count() == 1


def test_sheet_rejects_future_date(make_employee, admin_user_obj):
    with pytest.raises(ValidationError, match="future"):
        save([row(make_employee())], admin_user_obj, day=days(1))


def test_sheet_rejects_employees_not_working_that_day(make_employee, admin_user_obj):
    joined_later = make_employee(date_joined=TODAY)
    left = make_employee(date_joined=days(-100), status="RESIGNED", end_date=days(-10))

    for employee in (joined_later, left):
        with pytest.raises(ValidationError, match="wasn't working"):
            save([row(employee)], admin_user_obj, day=days(-5))


def test_sheet_rejects_employee_on_approved_leave(make_employee, make_leave, admin_user_obj):
    employee = make_employee(first_name="Kumari")
    make_leave(employee=employee, start=days(-1), end=days(1))

    with pytest.raises(ValidationError, match="Kumari .* is on approved leave"):
        save([row(employee)], admin_user_obj)


def test_sheet_is_all_or_nothing(make_employee, admin_user_obj):
    good, bad = make_employee(), make_employee()

    with pytest.raises(ValidationError, match="Check-out must be after check-in"):
        save(
            [row(good), row(bad, check_in=time(17), check_out=time(9))],
            admin_user_obj,
        )
    assert not Attendance.objects.exists()


# --- Leave ----------------------------------------------------------------------------


def record_leave(employee, user, start, end, **kwargs):
    return services.record_leave(
        employee=employee,
        leave_type="ANNUAL",
        start_date=start,
        end_date=end,
        reason="Holiday",
        acting_user=user,
        **kwargs,
    )


def test_record_leave_is_approved_immediately(make_employee, admin_user_obj):
    leave = record_leave(make_employee(), admin_user_obj, days(5), days(7))

    assert leave.status == LeaveStatus.APPROVED
    assert leave.decided_by == admin_user_obj


@pytest.mark.parametrize("existing_status", ["PENDING", "APPROVED"])
def test_overlapping_leave_refused(make_employee, make_leave, admin_user_obj, existing_status):
    employee = make_employee()
    make_leave(employee=employee, start=days(5), end=days(7), status=existing_status)

    with pytest.raises(ValidationError, match="This overlaps"):
        record_leave(employee, admin_user_obj, days(7), days(9))  # shares day 7
    record_leave(employee, admin_user_obj, days(8), days(9))  # next day: fine


def test_request_leave_rules(make_employee, make_user):
    with pytest.raises(ValidationError, match="isn't linked"):
        services.request_leave(
            user=make_user(), leave_type="SICK", start_date=TODAY, end_date=TODAY, reason="x"
        )

    user = make_user(role=Role.NURSE)
    make_employee(user=user)
    with pytest.raises(ValidationError, match="past"):
        services.request_leave(
            user=user, leave_type="SICK", start_date=days(-1), end_date=TODAY, reason="x"
        )

    leave = services.request_leave(
        user=user, leave_type="CASUAL", start_date=TODAY, end_date=days(1), reason="Errand"
    )
    assert leave.status == LeaveStatus.PENDING and leave.requested_by == user


def test_approve_refused_when_attendance_recorded(make_employee, make_leave, admin_user_obj):
    employee = make_employee()
    leave = make_leave(employee=employee, start=days(-3), end=days(-1), status="PENDING")
    for offset in (-3, -2):
        Attendance.objects.create(
            employee=employee, date=days(offset), status="PRESENT", recorded_by=admin_user_obj
        )

    with pytest.raises(ValidationError) as excinfo:
        services.approve_leave(leave, acting_user=admin_user_obj)
    message = excinfo.value.messages[0]
    assert f"{days(-3):%d %b}" in message and f"{days(-2):%d %b}" in message
    leave.refresh_from_db()
    assert leave.status == LeaveStatus.PENDING


def test_doctor_conflicts_need_confirmation(
    make_doctor, make_employee, make_leave, make_appointment, admin_user_obj
):
    doctor = make_doctor()
    employee = make_employee(user=doctor.user, category="MEDICAL")
    leave = make_leave(employee=employee, start=days(5), end=days(7), status="PENDING")
    make_appointment(doctor=doctor, date=days(6))

    with pytest.raises(ValidationError, match="1 booked appointment") as excinfo:
        services.approve_leave(leave, acting_user=admin_user_obj)
    assert excinfo.value.code == "doctor_conflicts"

    services.approve_leave(leave, confirm_doctor_conflicts=True, acting_user=admin_user_obj)
    leave.refresh_from_db()
    assert leave.status == LeaveStatus.APPROVED


def test_reject_needs_note(make_leave, admin_user_obj):
    leave = make_leave(status="PENDING")

    with pytest.raises(ValidationError):
        services.reject_leave(leave, note=" ", acting_user=admin_user_obj)
    services.reject_leave(leave, note="Short staffed", acting_user=admin_user_obj)
    leave.refresh_from_db()
    assert (leave.status, leave.decision_note) == ("REJECTED", "Short staffed")


def test_cancel_rules(make_employee, make_leave, make_user, admin_user_obj):
    owner = make_user(role=Role.NURSE)
    employee = make_employee(user=owner)
    future = make_leave(employee=employee, start=days(5))
    started = make_leave(employee=employee, start=TODAY, end=TODAY)

    with pytest.raises(PermissionDenied):
        services.cancel_leave(future, acting_user=make_user(role=Role.NURSE))
    with pytest.raises(ValidationError, match="before it starts"):
        services.cancel_leave(started, acting_user=owner)

    services.cancel_leave(future, acting_user=owner)
    future.refresh_from_db()
    assert future.status == LeaveStatus.CANCELLED

    other = make_leave(start=days(20), status="PENDING")
    services.cancel_leave(other, acting_user=admin_user_obj)  # the admin can cancel anyone's
    assert LeaveRequest.objects.get(pk=other.pk).status == LeaveStatus.CANCELLED
