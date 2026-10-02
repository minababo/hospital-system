from datetime import time
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from accounts.models import Role, User
from doctors import services
from doctors.models import Doctor, DoctorSchedule, Weekday

USER_DATA = {
    "username": "drsilva",
    "password": "Str0ng-Passw0rd!",
    "first_name": "Ana",
    "last_name": "Silva",
    "email": "ana.silva@example.com",
}


def profile_data(department, **overrides):
    data = {
        "department": department,
        "specialization": "Cardiology",
        "registration_number": "slmc-1001",
        "qualification": "MBBS",
        "phone": "0771234567",
        "consultation_fee": Decimal("2500.00"),
    }
    data.update(overrides)
    return data


def test_create_doctor_creates_user_and_profile(admin_user_obj, make_department):
    doctor = services.create_doctor(
        user_data=USER_DATA,
        profile_data=profile_data(make_department()),
        acting_user=admin_user_obj,
    )

    assert doctor.user.role == Role.DOCTOR
    assert doctor.user.check_password("Str0ng-Passw0rd!")
    assert doctor.registration_number == "SLMC-1001"


@pytest.mark.parametrize("problem", ["inactive-department", "duplicate-registration"])
def test_invalid_profile_leaves_no_user_behind(
    admin_user_obj, make_department, make_doctor, problem
):
    if problem == "inactive-department":
        data = profile_data(make_department(is_active=False))
    else:
        make_doctor(registration_number="SLMC-1001")
        data = profile_data(make_department())

    with pytest.raises(ValidationError):
        services.create_doctor(user_data=USER_DATA, profile_data=data, acting_user=admin_user_obj)

    assert not User.objects.filter(username="drsilva").exists()


def test_create_doctor_profile_for_existing_user(admin_user_obj, make_user, make_department):
    user = make_user(role=Role.DOCTOR)

    doctor = services.create_doctor_profile(
        user=user, profile_data=profile_data(make_department()), acting_user=admin_user_obj
    )

    assert Doctor.objects.get(user=user) == doctor


def test_create_doctor_profile_rejects_second_profile(admin_user_obj, make_doctor, make_department):
    existing = make_doctor()

    with pytest.raises(ValidationError):
        services.create_doctor_profile(
            user=existing.user,
            profile_data=profile_data(make_department(), registration_number="OTHER"),
            acting_user=admin_user_obj,
        )


def test_update_doctor_updates_user_and_profile(admin_user_obj, make_doctor):
    doctor = make_doctor()

    services.update_doctor(
        doctor,
        user_fields={"first_name": "Nimal", "last_name": "Perera", "email": "n@example.com"},
        profile_fields={"specialization": "Neurology", "consultation_fee": Decimal("3000")},
        acting_user=admin_user_obj,
    )

    doctor.refresh_from_db()
    doctor.user.refresh_from_db()
    assert doctor.user.get_full_name() == "Nimal Perera"
    assert doctor.specialization == "Neurology"
    assert doctor.consultation_fee == Decimal("3000.00")


def test_department_services(admin_user_obj):
    department = services.create_department(name=" Surgery ", acting_user=admin_user_obj)
    assert department.name == "Surgery"

    services.update_department(department, description="Theatre", acting_user=admin_user_obj)
    services.set_department_active(department, False, acting_user=admin_user_obj)

    department.refresh_from_db()
    assert department.description == "Theatre"
    assert department.is_active is False


def test_create_department_rejects_duplicate_name(admin_user_obj, make_department):
    make_department(name="Surgery")

    with pytest.raises(ValidationError):
        services.create_department(name="surgery", acting_user=admin_user_obj)


def test_schedule_services(admin_user_obj, make_doctor):
    doctor = make_doctor()
    schedule = services.create_schedule(
        doctor=doctor,
        weekday=Weekday.MONDAY,
        start_time=time(9),
        end_time=time(12),
        slot_minutes=15,
        acting_user=admin_user_obj,
    )

    services.update_schedule(schedule, end_time=time(13), acting_user=admin_user_obj)
    schedule.refresh_from_db()
    assert schedule.end_time == time(13)

    with pytest.raises(ValidationError, match="overlaps"):
        services.create_schedule(
            doctor=doctor,
            weekday=Weekday.MONDAY,
            start_time=time(12),
            end_time=time(14),
            acting_user=admin_user_obj,
        )

    services.delete_schedule(schedule, acting_user=admin_user_obj)
    assert not DoctorSchedule.objects.exists()
