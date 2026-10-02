from datetime import time

import pytest
from django.urls import reverse

from accounts.models import Role, User
from doctors.models import Doctor, DoctorSchedule, Weekday

VIEW_ROLES = {Role.ADMIN, Role.RECEPTIONIST, Role.NURSE, Role.DOCTOR}

# (url name, object the URL needs or None, method, status for an allowed role)
MANAGE_URLS = [
    ("doctors:department_list", None, "get", 200),
    ("doctors:department_create", None, "get", 200),
    ("doctors:department_update", "department", "get", 200),
    ("doctors:department_toggle_active", "department", "post", 302),
    ("doctors:doctor_create", None, "get", 200),
    ("doctors:doctor_complete_profile", "user_without_profile", "get", 200),
    ("doctors:doctor_update", "doctor", "get", 200),
    ("doctors:schedule_create", "doctor", "get", 200),
    ("doctors:schedule_update", "schedule", "get", 200),
    ("doctors:schedule_delete", "schedule", "post", 302),
]
VIEW_URLS = [
    ("doctors:doctor_list", None, "get", 200),
    ("doctors:doctor_detail", "doctor", "get", 200),
]
POST_ONLY_URLS = [url for url in MANAGE_URLS if url[2] == "post"]


@pytest.fixture
def objects(make_doctor, make_user):
    doctor = make_doctor()
    schedule = DoctorSchedule.objects.create(
        doctor=doctor, weekday=Weekday.MONDAY, start_time=time(9), end_time=time(12)
    )
    return {
        "doctor": doctor,
        "department": doctor.department,
        "schedule": schedule,
        "user_without_profile": make_user(role=Role.DOCTOR),
    }


def url_for(name, key, objects):
    return reverse(name, args=[objects[key].pk]) if key else reverse(name)


def request(client, method, url):
    return getattr(client, method)(url)


# --- RBAC -------------------------------------------------------------------


@pytest.mark.parametrize(("name", "key", "method", "_"), MANAGE_URLS + VIEW_URLS)
def test_anonymous_is_redirected_to_login(client, objects, name, key, method, _):
    response = request(client, method, url_for(name, key, objects))

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "key", "method", "allowed_status"), MANAGE_URLS)
def test_manage_urls_are_admin_only(
    client_for_role, objects, role, name, key, method, allowed_status
):
    response = request(client_for_role(role), method, url_for(name, key, objects))

    assert response.status_code == (allowed_status if role == Role.ADMIN else 403)


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize(("name", "key", "method", "allowed_status"), VIEW_URLS)
def test_view_urls_by_role(client_for_role, objects, role, name, key, method, allowed_status):
    response = request(client_for_role(role), method, url_for(name, key, objects))

    assert response.status_code == (allowed_status if role in VIEW_ROLES else 403)


@pytest.mark.parametrize(("name", "key", "_method", "_status"), POST_ONLY_URLS)
def test_post_only_urls_reject_get(client_for_role, objects, name, key, _method, _status):
    response = client_for_role(Role.ADMIN).get(url_for(name, key, objects))

    assert response.status_code == 405


def test_missing_objects_give_404(client_for_role):
    client = client_for_role(Role.ADMIN)

    assert client.get(reverse("doctors:doctor_detail", args=[999])).status_code == 404
    assert client.get(reverse("doctors:schedule_update", args=[999])).status_code == 404


def test_complete_profile_404_for_user_who_does_not_qualify(
    client_for_role, make_doctor, make_user
):
    client = client_for_role(Role.ADMIN)
    has_profile = make_doctor().user
    nurse = make_user(role=Role.NURSE)
    inactive = make_user(role=Role.DOCTOR, is_active=False)

    for user in (has_profile, nurse, inactive):
        url = reverse("doctors:doctor_complete_profile", args=[user.pk])
        assert client.get(url).status_code == 404


# --- My profile -------------------------------------------------------------


def test_me_redirects_doctor_to_own_detail_page(client, make_doctor):
    doctor = make_doctor()
    client.force_login(doctor.user)

    response = client.get(reverse("doctors:me"))

    assert response.status_code == 302
    assert response.url == reverse("doctors:doctor_detail", args=[doctor.pk])


def test_me_without_profile_shows_friendly_page(client_for_role):
    response = client_for_role(Role.DOCTOR).get(reverse("doctors:me"))

    assert response.status_code == 200
    assert b"isn't set up yet" in response.content


@pytest.mark.parametrize("role", [role for role in Role if role != Role.DOCTOR])
def test_me_is_doctor_only(client_for_role, role):
    assert client_for_role(role).get(reverse("doctors:me")).status_code == 403


# --- List and detail --------------------------------------------------------


def test_list_shows_fee_and_missing_profile_warning_for_admin(
    client_for_role, make_doctor, make_user
):
    make_doctor()
    missing = make_user(role=Role.DOCTOR, first_name="Kasun", last_name="Missing")

    response = client_for_role(Role.ADMIN).get(reverse("doctors:doctor_list"))
    content = response.content.decode()

    assert "Rs. 1,500.00" in content
    assert reverse("doctors:doctor_complete_profile", args=[missing.pk]) in content


def test_list_hides_missing_profile_warning_from_receptionist(client_for_role, make_user):
    make_user(role=Role.DOCTOR)

    response = client_for_role(Role.RECEPTIONIST).get(reverse("doctors:doctor_list"))

    assert list(response.context["users_without_profile"]) == []
    assert b"Add doctor" not in response.content


def test_list_filters_by_department(client_for_role, make_doctor, make_department):
    cardiology = make_department(name="Cardiology")
    in_cardiology = make_doctor(department=cardiology)
    make_doctor()

    response = client_for_role(Role.NURSE).get(
        reverse("doctors:doctor_list"), {"department": cardiology.pk}
    )

    assert list(response.context["doctors"]) == [in_cardiology]


def test_detail_shows_schedule_and_hides_manage_buttons_from_nurse(client_for_role, objects):
    response = client_for_role(Role.NURSE).get(
        reverse("doctors:doctor_detail", args=[objects["doctor"].pk])
    )
    content = response.content.decode()

    assert "09:00" in content and "12:00" in content
    assert reverse("doctors:schedule_create", args=[objects["doctor"].pk]) not in content


# --- Add / edit doctor ------------------------------------------------------


def add_doctor_data(department, **overrides):
    data = {
        "account-username": "drperera",
        "account-first_name": "Nimal",
        "account-last_name": "Perera",
        "account-email": "nimal@example.com",
        "account-password1": "Very-Str0ng-Passw0rd",
        "account-password2": "Very-Str0ng-Passw0rd",
        "profile-department": department.pk,
        "profile-specialization": "Cardiology",
        "profile-registration_number": "slmc-777",
        "profile-qualification": "MBBS, MD",
        "profile-phone": "077 123 4567",
        "profile-consultation_fee": "2500.00",
    }
    data.update(overrides)
    return data


def test_add_doctor_happy_path(client_for_role, make_department):
    client = client_for_role(Role.ADMIN)

    response = client.post(reverse("doctors:doctor_create"), add_doctor_data(make_department()))

    doctor = Doctor.objects.get(user__username="drperera")
    assert response.status_code == 302
    assert response.url == reverse("doctors:doctor_detail", args=[doctor.pk])
    assert doctor.user.role == Role.DOCTOR
    assert doctor.registration_number == "SLMC-777"
    assert doctor.phone == "0771234567"


def test_add_doctor_with_invalid_profile_creates_nothing(client_for_role, make_department):
    client = client_for_role(Role.ADMIN)
    data = add_doctor_data(
        make_department(), **{"profile-phone": "123", "profile-consultation_fee": "-5"}
    )

    response = client.post(reverse("doctors:doctor_create"), data)

    assert response.status_code == 200
    errors = response.context["profile_form"].errors
    assert "phone" in errors
    assert "consultation_fee" in errors
    assert not User.objects.filter(username="drperera").exists()


def test_add_doctor_duplicate_registration_creates_nothing(
    client_for_role, make_department, make_doctor
):
    make_doctor(registration_number="SLMC-777")
    client = client_for_role(Role.ADMIN)

    response = client.post(reverse("doctors:doctor_create"), add_doctor_data(make_department()))

    assert response.status_code == 200
    assert "registration number already exists" in str(response.context["profile_form"].errors)
    assert not User.objects.filter(username="drperera").exists()


def test_add_doctor_cannot_pick_inactive_department(client_for_role, make_department):
    client = client_for_role(Role.ADMIN)

    response = client.post(
        reverse("doctors:doctor_create"), add_doctor_data(make_department(is_active=False))
    )

    assert "department" in response.context["profile_form"].errors
    assert not User.objects.filter(username="drperera").exists()


def test_complete_profile(client_for_role, make_user, make_department):
    doctor_user = make_user(role=Role.DOCTOR)
    client = client_for_role(Role.ADMIN)

    response = client.post(
        reverse("doctors:doctor_complete_profile", args=[doctor_user.pk]),
        {
            "department": make_department().pk,
            "specialization": "Paediatrics",
            "registration_number": "SLMC-1",
            "phone": "+94771234567",
            "consultation_fee": "1000",
        },
    )

    assert response.status_code == 302
    assert Doctor.objects.filter(user=doctor_user).exists()


def test_edit_doctor_keeps_deactivated_department_selectable(client_for_role, make_doctor):
    doctor = make_doctor()
    doctor.department.is_active = False
    doctor.department.save()
    client = client_for_role(Role.ADMIN)

    response = client.post(
        reverse("doctors:doctor_update", args=[doctor.pk]),
        {
            "user-first_name": "Updated",
            "user-last_name": "Name",
            "user-email": "updated@example.com",
            "profile-department": doctor.department.pk,
            "profile-specialization": "Oncology",
            "profile-registration_number": doctor.registration_number,
            "profile-phone": doctor.phone,
            "profile-consultation_fee": "1800",
        },
    )

    assert response.status_code == 302
    doctor.refresh_from_db()
    assert doctor.specialization == "Oncology"
    assert doctor.user.first_name == "Updated"


# --- Schedules --------------------------------------------------------------


def test_schedule_create_with_overlap_shows_form_error(client_for_role, objects):
    client = client_for_role(Role.ADMIN)
    doctor = objects["doctor"]  # already has Monday 09:00-12:00

    response = client.post(
        reverse("doctors:schedule_create", args=[doctor.pk]),
        {
            "weekday": Weekday.MONDAY,
            "start_time": "11:00",
            "end_time": "13:00",
            "slot_minutes": 15,
            "is_active": "on",  # unchecked would mean inactive, and inactive blocks may overlap
        },
    )

    assert response.status_code == 200
    assert "overlaps" in str(response.context["form"].non_field_errors())
    assert doctor.schedules.count() == 1


def test_schedule_create_success(client_for_role, objects):
    client = client_for_role(Role.ADMIN)
    doctor = objects["doctor"]

    response = client.post(
        reverse("doctors:schedule_create", args=[doctor.pk]),
        {
            "weekday": Weekday.MONDAY,
            "start_time": "12:00",
            "end_time": "14:00",
            "slot_minutes": 20,
            "is_active": "on",
        },
    )

    assert response.status_code == 302
    assert doctor.schedules.count() == 2


def test_schedule_delete(client_for_role, objects):
    client = client_for_role(Role.ADMIN)

    response = client.post(reverse("doctors:schedule_delete", args=[objects["schedule"].pk]))

    assert response.status_code == 302
    assert not DoctorSchedule.objects.exists()


def test_department_toggle_and_create(client_for_role, objects):
    client = client_for_role(Role.ADMIN)
    department = objects["department"]

    client.post(reverse("doctors:department_toggle_active", args=[department.pk]))
    department.refresh_from_db()
    assert department.is_active is False

    response = client.post(reverse("doctors:department_create"), {"name": department.name.upper()})
    assert response.status_code == 200
    assert "already exists" in str(response.context["form"].non_field_errors())
