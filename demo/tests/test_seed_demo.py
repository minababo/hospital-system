"""manage.py seed_demo: safety checks, idempotency and that the seeded data is usable.

Seeding uses --scale 0.1 to keep these tests fast; the scenarios are the same as at
full size, just with fewer patients and visits.
"""

from io import StringIO

import pytest
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.urls import reverse

from accounts.models import Role
from appointments.models import Status
from audit.models import AuditLog
from demo import seed
from demo.data import DEMO_USERS
from pharmacy.tests.ledger import assert_ledger_balanced
from records.models import MedicalRecord, RecordStatus
from reports import permissions as report_permissions

pytestmark = pytest.mark.django_db

User = get_user_model()
PASSWORD = "Seed-Demo-Pass-2026!"
DEMO_USERNAMES = [username for username, *_ in DEMO_USERS]


def run(*args, password=PASSWORD, scale="0.1"):
    """Run the command; returns (stdout, stderr)."""
    out, err = StringIO(), StringIO()
    options = ["--scale", scale, *args]
    if password is not None:
        options = ["--password", password, *options]
    call_command("seed_demo", *options, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def model_counts():
    return {model._meta.label: model.objects.count() for model in apps.get_models()}


# --- Password and safety checks ------------------------------------------------------------


def test_missing_password_is_an_error(monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD", raising=False)
    with pytest.raises(CommandError, match="DEMO_PASSWORD"):
        run(password=None)
    assert not User.objects.exists()


def test_password_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("DEMO_PASSWORD", PASSWORD)
    out, err = run(password=None)
    assert User.objects.get(username="demo.admin").check_password(PASSWORD)
    assert PASSWORD not in out + err


def test_weak_password_fails_with_the_validator_message():
    with pytest.raises(CommandError) as error:
        run(password="Qx7")
    assert "too short" in str(error.value)
    assert "Qx7" not in str(error.value)  # the password itself is never echoed

    with pytest.raises(CommandError, match="too common"):
        run(password="password123")
    assert not User.objects.exists()


def test_non_sqlite_database_needs_yes(monkeypatch):
    monkeypatch.setattr(
        seed, "database_engine", lambda: ("django.db.backends.postgresql", "db.example.com")
    )
    with pytest.raises(CommandError, match="db.example.com") as error:
        run()
    assert "--yes" in str(error.value)
    assert PASSWORD not in str(error.value)
    assert not User.objects.exists()


# --- Idempotency and existing users ---------------------------------------------------------------


def test_second_run_creates_nothing_and_keeps_passwords():
    first_out, first_err = run()
    counts = model_counts()
    hashes = dict(User.objects.values_list("username", "password"))

    second_out, second_err = run(password="Another-Long-Pass-77!")

    assert model_counts() == counts
    assert dict(User.objects.values_list("username", "password")) == hashes
    for output in (first_out, first_err, second_out, second_err):
        assert PASSWORD not in output and "Another-Long-Pass-77!" not in output
    assert "Created  Skipped" in second_out


def test_reset_passwords_changes_demo_users_only(make_user):
    other = make_user(role=Role.NURSE, username="real.nurse")
    other_hash = other.password
    run()

    run("--reset-passwords", password="Brand-New-Pass-88!")

    for username in DEMO_USERNAMES:
        assert User.objects.get(username=username).check_password("Brand-New-Pass-88!")
    other.refresh_from_db()
    assert other.password == other_hash


def test_existing_users_with_another_role_and_superusers_are_untouched(make_user):
    clash = make_user(role=Role.PHARMACIST, username="demo.nurse")
    clash_hash = clash.password
    root = User.objects.create_superuser(
        "root", "root@example.com", "Root-Pass-123!", role=Role.ADMIN
    )
    root_hash = root.password

    out, _ = run("--reset-passwords")

    clash.refresh_from_db()
    root.refresh_from_db()
    assert clash.role == Role.PHARMACIST and clash.password == clash_hash
    assert not hasattr(clash, "employee_profile") or clash.employee_profile is None
    assert root.password == root_hash and root.is_superuser
    assert "demo.nurse already exists with role Pharmacist" in out
    # Seeding still finished: vitals were recorded by the doctors instead.
    assert AuditLog.objects.filter(event="records.vitals.recorded").exists()


# --- The seeded data ---------------------------------------------------------------------


@pytest.fixture
def seeded():
    run()


def test_every_demo_user_can_log_in_and_see_a_full_dashboard(client, seeded):
    for username in DEMO_USERNAMES:
        assert client.login(username=username, password=PASSWORD), username
        response = client.get(reverse("dashboard"))
        assert response.status_code == 200, username
        values = [card["value"] for card in response.context["cards"]]
        assert values and None not in values, (username, values)
        client.logout()


REPORTS = [
    ("reports:patients", report_permissions.PATIENT_REPORT),
    ("reports:appointments", report_permissions.APPOINTMENT_REPORT),
    ("reports:revenue", report_permissions.REVENUE_REPORT),
    ("reports:pharmacy", report_permissions.PHARMACY_REPORT),
    ("reports:laboratory", report_permissions.LABORATORY_REPORT),
    ("reports:staff", report_permissions.STAFF_REPORT),
]


def test_every_report_opens_for_an_allowed_demo_user(client, seeded):
    users_by_role = {
        User.objects.get(username=username).role: username for username in DEMO_USERNAMES
    }
    for url_name, roles in REPORTS:
        username = users_by_role[roles[0]]
        client.login(username=username, password=PASSWORD)
        assert client.get(reverse(url_name)).status_code == 200, url_name
        client.logout()


def test_seeded_data_is_consistent(seeded):
    assert_ledger_balanced()

    completed = Appointment_objects(Status.COMPLETED)
    assert completed, "expected completed visits"
    for appointment in completed:
        assert appointment.medical_record.status == RecordStatus.FINALIZED
    for record in MedicalRecord.objects.filter(status=RecordStatus.FINALIZED):
        assert record.appointment.status == Status.COMPLETED

    demo_actions = AuditLog.objects.filter(actor__username__startswith="demo.")
    assert demo_actions.filter(event="appointments.appointment.booked").exists()
    assert demo_actions.filter(event="records.record.finalized").exists()
    assert demo_actions.filter(event="pharmacy.prescription.dispensed").exists()
    assert demo_actions.filter(event="billing.payment.recorded").exists()


def test_dataset_has_the_cases_reviewers_look_for(seeded):
    from admissions.models import Admission
    from billing.models import Invoice
    from laboratory.models import LabOrder, OrderStatus
    from staff.models import LeaveRequest

    today_statuses = set(
        Appointment_objects(None).filter(date=_today()).values_list("status", flat=True)
    )
    assert {Status.BOOKED, Status.CHECKED_IN} <= today_statuses
    assert Invoice.objects.filter(status="PAID").exists()
    assert Invoice.objects.filter(status="VOID").exists()
    assert Invoice.objects.filter(status="DRAFT").exists()
    assert (
        Invoice.objects.exclude(discount=0)
        .filter(discount_set_by__username="demo.accountant")
        .exists()
    )
    assert LabOrder.objects.filter(status=OrderStatus.COMPLETED).exists()
    assert LabOrder.objects.filter(referred_by__startswith="Dr. K.").exists()  # the walk-in
    assert Admission.objects.filter(status="ADMITTED").exists()
    assert Admission.objects.filter(status="DISCHARGED").exists()
    assert LeaveRequest.objects.filter(status="PENDING").exists()
    assert LeaveRequest.objects.filter(status="REJECTED").exclude(decision_note="").exists()


def Appointment_objects(status):  # noqa: N802 - small local helper
    from appointments.models import Appointment

    queryset = Appointment.objects.select_related("medical_record")
    return queryset.filter(status=status) if status else queryset


def _today():
    from django.utils import timezone

    return timezone.localdate()


def test_inactive_existing_department_is_left_alone(make_department):
    paediatrics = make_department(name="Paediatrics", is_active=False)

    out, _ = run()

    paediatrics.refresh_from_db()
    assert not paediatrics.is_active
    assert "Department Paediatrics is inactive" in out
    assert not User.objects.filter(username="demo.doctor2").exists()
    assert User.objects.filter(username="demo.doctor").exists()
