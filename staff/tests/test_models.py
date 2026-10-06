from datetime import time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from staff.models import Attendance, Employee, LeaveRequest

pytestmark = pytest.mark.django_db

TODAY = timezone.localdate()


def test_nic_unique_ignoring_case_but_many_blank(make_employee):
    make_employee(nic="123456789V")
    make_employee(nic=None)
    make_employee(nic=None)  # several employees without a NIC are fine

    with pytest.raises(IntegrityError), transaction.atomic():
        make_employee(nic="123456789v")


def test_one_employee_per_login(make_employee, make_user):
    user = make_user()
    make_employee(user=user)

    with pytest.raises(IntegrityError), transaction.atomic():
        make_employee(user=user)


@pytest.mark.parametrize(
    ("status", "end_date"),
    [("ACTIVE", TODAY), ("RESIGNED", None)],
    ids=["active-with-end-date", "resigned-without-end-date"],
)
def test_status_and_end_date_must_agree(make_employee, status, end_date):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_employee(status=status, end_date=end_date)


def test_end_date_not_before_joining(make_employee):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_employee(status="RESIGNED", date_joined=TODAY, end_date=TODAY - timedelta(days=1))


def test_clean_normalises_and_validates(make_department):
    employee = Employee(
        first_name=" Nimal ",
        last_name="Perera",
        nic="123456789v",  # the form strips spaces first
        phone="077 123-4567",
        designation="Porter",
        category="SUPPORT",
        employment_type="CONTRACT",
        department=make_department(),
        date_joined=TODAY,
    )
    employee.full_clean()
    assert (employee.first_name, employee.nic, employee.phone) == (
        "Nimal",
        "123456789V",
        "0771234567",
    )

    employee.date_joined = TODAY + timedelta(days=1)
    employee.phone = "123"
    with pytest.raises(ValidationError) as excinfo:
        employee.full_clean()
    assert {"date_joined", "phone"} <= set(excinfo.value.message_dict)


def record(employee, user, **kwargs):
    kwargs.setdefault("date", TODAY)
    kwargs.setdefault("status", "PRESENT")
    return Attendance.objects.create(employee=employee, recorded_by=user, **kwargs)


def test_attendance_one_per_employee_per_day(make_employee, admin_user_obj):
    employee = make_employee()
    record(employee, admin_user_obj)

    with pytest.raises(IntegrityError), transaction.atomic():
        record(employee, admin_user_obj, status="ABSENT")


def test_check_out_after_check_in(make_employee, admin_user_obj):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(make_employee(), admin_user_obj, check_in=time(17), check_out=time(8))


def test_absent_has_no_times(make_employee, admin_user_obj):
    with pytest.raises(IntegrityError), transaction.atomic():
        record(make_employee(), admin_user_obj, status="ABSENT", check_in=time(8))


def test_leave_end_not_before_start(make_leave):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_leave(start=TODAY, end=TODAY - timedelta(days=1))


def test_rejected_leave_needs_note(make_leave):
    with pytest.raises(IntegrityError), transaction.atomic():
        make_leave(status="REJECTED", decision_note="")


def test_leave_days_are_inclusive(make_leave):
    leave = make_leave(start=TODAY, end=TODAY + timedelta(days=2))

    assert leave.days == 3
    assert LeaveRequest.objects.get(pk=leave.pk).dates()[-1] == TODAY + timedelta(days=2)


def test_employee_number(make_employee):
    employee = make_employee()

    assert employee.number == f"EMP-{employee.pk:06d}"
    assert employee.is_active
