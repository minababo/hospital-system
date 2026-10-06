from datetime import time, timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from staff.models import Attendance, LeaveStatus

TODAY = timezone.localdate()
HR = {Role.ADMIN}


@pytest.fixture
def objects(make_employee, make_leave):
    employee = make_employee()
    return {"employee": employee, "leave": make_leave(employee=employee, status="PENDING")}


def build(name, o):
    return {
        "staff:employee_detail": [o["employee"].pk],
        "staff:employee_update": [o["employee"].pk],
        "staff:end_employment": [o["employee"].pk],
        "staff:leave_detail": [o["leave"].pk],
        "staff:leave_approve": [o["leave"].pk],
        "staff:leave_reject": [o["leave"].pk],
        "staff:leave_cancel": [o["leave"].pk],
    }.get(name, [])


# (url name, method, status for the admin). Every other role gets 403 (cancel: the
# service refuses anyone who isn't the employee or the admin).
URLS = [
    ("staff:employee_list", "get", 200),
    ("staff:employee_create", "get", 200),
    ("staff:employee_detail", "get", 200),
    ("staff:employee_update", "get", 200),
    ("staff:attendance", "get", 200),
    ("staff:attendance_monthly", "get", 200),
    ("staff:leave_list", "get", 200),
    ("staff:leave_detail", "get", 200),
    ("staff:end_employment", "post", 302),
    ("staff:leave_approve", "post", 302),
    ("staff:leave_reject", "post", 302),
    ("staff:leave_cancel", "post", 302),
]
POST_ONLY = [url for url in URLS if url[1] == "post"]


@pytest.mark.parametrize(("name", "method", "_status"), URLS)
def test_anonymous_redirected(client, objects, name, method, _status):
    response = getattr(client, method)(reverse(name, args=build(name, objects)))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "method", "status"), URLS)
def test_rbac_matrix(client_for_role, objects, role, name, method, status):
    client = client_for_role(role)

    response = getattr(client, method)(reverse(name, args=build(name, objects)))

    assert response.status_code == (status if role in HR else 403)


@pytest.mark.parametrize(("name", "_method", "_status"), POST_ONLY)
def test_post_only_reject_get(client_for_role, objects, name, _method, _status):
    response = client_for_role(Role.ADMIN).get(reverse(name, args=build(name, objects)))

    assert response.status_code == 405


# --- My leave ------------------------------------------------------------------------


def test_my_leave_access(client, make_user, make_employee):
    url = reverse("staff:my_leave")
    assert client.get(url).url.startswith(reverse("accounts:login"))

    without = make_user(role=Role.PHARMACIST)
    client.force_login(without)
    response = client.get(url)
    assert (
        response.status_code == 404
        and "isn't linked to an employee record" in response.content.decode()
    )

    with_record = make_user(role=Role.PHARMACIST)
    make_employee(user=with_record)
    client.force_login(with_record)
    assert client.get(url).status_code == 200


def test_request_and_cancel_own_leave(client, make_user, make_employee):
    user = make_user(role=Role.NURSE)
    make_employee(user=user)
    client.force_login(user)
    start = TODAY + timedelta(days=5)

    client.post(
        reverse("staff:my_leave"),
        {"leave_type": "CASUAL", "start_date": start, "end_date": start, "reason": "Errand"},
    )
    leave = user.employee_profile.leave_requests.get()
    response = client.post(reverse("staff:leave_cancel", args=[leave.pk]))

    assert response.url == reverse("staff:my_leave")
    leave.refresh_from_db()
    assert leave.status == LeaveStatus.CANCELLED


# --- Attendance formset -------------------------------------------------------------


def formset_data(rows):
    data = {"form-TOTAL_FORMS": len(rows), "form-INITIAL_FORMS": len(rows)}
    for i, row in enumerate(rows):
        for key, value in row.items():
            data[f"form-{i}-{key}"] = value
    return data


def test_attendance_formset_saves_several_rows(client_for_role, make_employee):
    a, b = make_employee(), make_employee()
    client = client_for_role(Role.ADMIN)
    url = f"{reverse('staff:attendance')}?date={TODAY:%Y-%m-%d}"

    page = client.get(url)
    response = client.post(
        url,
        formset_data(
            [
                {
                    "employee_id": a.pk,
                    "status": "PRESENT",
                    "check_in": "08:00",
                    "check_out": "16:30",
                },
                {"employee_id": b.pk, "status": "ABSENT", "notes": "Sick call"},
            ]
        ),
    )

    assert len(page.context["formset"].forms) == 2
    assert response.status_code == 302
    saved = {r.employee: r for r in Attendance.objects.all()}
    assert saved[a].check_out == time(16, 30) and saved[b].notes == "Sick call"


def test_attendance_sheet_shows_leave_read_only(client_for_role, make_employee, make_leave):
    working, away = make_employee(), make_employee(first_name="Away")
    make_leave(employee=away, start=TODAY, end=TODAY)

    page = client_for_role(Role.ADMIN).get(reverse("staff:attendance"))

    ids = [form["employee_id"].value() for form in page.context["formset"]]
    assert ids == [working.pk]
    assert "On approved annual leave" in page.content.decode()


def test_attendance_errors_shown(client_for_role, make_employee):
    employee = make_employee()

    response = client_for_role(Role.ADMIN).post(
        reverse("staff:attendance"),
        formset_data([{"employee_id": employee.pk, "status": "ABSENT", "check_in": "08:00"}]),
    )

    assert response.status_code == 200
    assert "no check-in or check-out time" in response.content.decode()
    assert not Attendance.objects.exists()


def test_monthly_page_navigation(client_for_role, make_employee):
    make_employee()

    response = client_for_role(Role.ADMIN).get(
        reverse("staff:attendance_monthly"), {"month": "2026-03"}
    )

    content = response.content.decode()
    assert "March 2026" in content and "month=2026-02" in content and "month=2026-04" in content


# --- Doctor conflicts -----------------------------------------------------------------


def test_doctor_conflict_checkbox_flow(
    client_for_role, make_doctor, make_employee, make_leave, make_appointment
):
    doctor = make_doctor()
    start = TODAY + timedelta(days=5)
    leave = make_leave(
        employee=make_employee(user=doctor.user), start=start, end=start, status="PENDING"
    )
    make_appointment(doctor=doctor, date=start)
    client = client_for_role(Role.ADMIN)
    url = reverse("staff:leave_approve", args=[leave.pk])

    first = client.post(url, {"note": ""})
    assert first.status_code == 200 and first.context["show_confirm"] is True
    assert "1 booked appointment" in first.content.decode()

    second = client.post(url, {"note": "", "confirm_doctor_conflicts": "on"})
    assert second.status_code == 302
    leave.refresh_from_db()
    assert leave.status == LeaveStatus.APPROVED


def test_record_leave_from_list_page(client_for_role, make_employee):
    employee = make_employee()
    start = TODAY + timedelta(days=3)

    response = client_for_role(Role.ADMIN).post(
        reverse("staff:leave_list"),
        {
            "employee": employee.pk,
            "leave_type": "SICK",
            "start_date": start,
            "end_date": start,
            "reason": "Flu",
        },
    )

    assert response.status_code == 302
    assert employee.leave_requests.get().status == LeaveStatus.APPROVED


def test_register_employee(client_for_role, make_department):
    response = client_for_role(Role.ADMIN).post(
        reverse("staff:employee_create"),
        {
            "first_name": "Sunil",
            "last_name": "Silva",
            "nic": "2000 1234 5678",
            "phone": "077 765 4321",
            "designation": "Porter",
            "category": "SUPPORT",
            "employment_type": "CONTRACT",
            "department": make_department().pk,
            "date_joined": TODAY,
        },
    )

    assert response.status_code == 302


# --- Navigation ------------------------------------------------------------------------


@pytest.mark.parametrize("role", list(Role))
def test_hr_links_only_for_admin(client_for_role, role):
    labels = [
        i["label"] for i in client_for_role(role).get(reverse("dashboard")).context["nav_items"]
    ]

    assert ({"Staff", "Attendance", "Leave"} <= set(labels)) == (role in HR)


def test_my_leave_link_only_with_active_employee_record(client, make_user, make_employee):
    def labels(user):
        client.force_login(user)
        return [i["label"] for i in client.get(reverse("dashboard")).context["nav_items"]]

    plain = make_user(role=Role.NURSE)
    linked = make_user(role=Role.NURSE)
    make_employee(user=linked)
    ended = make_user(role=Role.NURSE)
    make_employee(user=ended, status="RESIGNED", end_date=TODAY)

    assert "My leave" not in labels(plain)
    assert labels(linked)[-2:] == ["My leave", "Change password"]
    assert "My leave" not in labels(ended)
