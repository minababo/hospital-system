import itertools
import runpy
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.conf import settings as django_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from accounts.models import Role, User
from appointments import services as appointment_services
from appointments.models import Appointment, Status
from billing.models import Charge, ChargeType, Invoice
from doctors.models import Department, Doctor, DoctorSchedule
from laboratory.models import LabOrder, LabOrderItem, LabTest, LabTestParameter
from patients.models import Patient
from pharmacy.models import Medicine
from records.models import MedicalRecord, RecordStatus


@pytest.fixture(autouse=True)
def _fast_password_hasher(settings):
    # The production hasher is slow on purpose; tests create many users.
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def _test_storages(settings):
    settings.STORAGES = {
        # Uploads stay in memory: nothing is written to disk or sent to Supabase,
        # and each test starts with an empty storage.
        "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
        # Tests run with DEBUG=False, and the manifest storage fails without collectstatic.
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


@pytest.fixture
def load_settings():
    """Run config/settings.py as a plain module, so tests can check how env vars
    change settings without touching the live django.conf.settings."""

    def _load():
        return runpy.run_path(str(django_settings.BASE_DIR / "config" / "settings.py"))

    return _load


@pytest.fixture
def user_password():
    return "Str0ng-Test-Passw0rd!"


@pytest.fixture
def make_user(db, user_password):
    counter = itertools.count(1)

    def _make_user(role=Role.RECEPTIONIST, password=None, **kwargs):
        n = next(counter)
        username = kwargs.pop("username", f"{role.lower()}{n}")
        kwargs.setdefault("first_name", "Test")
        kwargs.setdefault("last_name", f"User{n}")
        kwargs.setdefault("email", f"{username}@example.com")
        return User.objects.create_user(
            username=username, password=password or user_password, role=role, **kwargs
        )

    return _make_user


@pytest.fixture
def make_department(db):
    counter = itertools.count(1)

    def _make_department(**kwargs):
        kwargs.setdefault("name", f"Department {next(counter)}")
        return Department.objects.create(**kwargs)

    return _make_department


@pytest.fixture
def make_doctor(make_user, make_department):
    """Creates a DOCTOR user plus their Doctor profile. User fields can be passed
    with a user__ prefix, e.g. make_doctor(user__first_name="Ana")."""
    counter = itertools.count(1)

    def _make_doctor(**kwargs):
        n = next(counter)
        user_kwargs = {
            k.removeprefix("user__"): kwargs.pop(k) for k in list(kwargs) if k.startswith("user__")
        }
        # Not setdefault(): that would create a user/department even when one is passed.
        if "user" not in kwargs:
            kwargs["user"] = make_user(role=Role.DOCTOR, **user_kwargs)
        if "department" not in kwargs:
            kwargs["department"] = make_department()
        kwargs.setdefault("specialization", "General Medicine")
        kwargs.setdefault("registration_number", f"SLMC{n:05d}")
        kwargs.setdefault("phone", "0771234567")
        kwargs.setdefault("consultation_fee", Decimal("1500.00"))
        return Doctor.objects.create(**kwargs)

    return _make_doctor


@pytest.fixture
def make_patient(db):
    counter = itertools.count(1)

    def _make_patient(**kwargs):
        n = next(counter)
        kwargs.setdefault("first_name", "Patient")
        kwargs.setdefault("last_name", f"Number{n}")
        kwargs.setdefault("date_of_birth", date(1990, 1, 1))
        kwargs.setdefault("gender", Patient.Gender.FEMALE)
        kwargs.setdefault("phone", f"07700{n:05d}")
        kwargs.setdefault("address", "1 Main Street, Colombo")
        return Patient.objects.create(**kwargs)

    return _make_patient


@pytest.fixture
def make_scheduled_doctor(make_doctor):
    """A doctor who works 09:00-12:00 in 15-minute slots on the given weekdays
    (every day by default), so any date can be used in booking tests."""

    def _make_scheduled_doctor(weekdays=range(7), start=time(9), end=time(12), **kwargs):
        doctor = make_doctor(**kwargs)
        for weekday in weekdays:
            DoctorSchedule.objects.create(
                doctor=doctor, weekday=weekday, start_time=start, end_time=end, slot_minutes=15
            )
        return doctor

    return _make_scheduled_doctor


@pytest.fixture
def make_appointment(make_patient, make_doctor):
    """Creates an Appointment directly (no service checks). Default: Monday 5 Oct 2026,
    09:00-09:15, status BOOKED."""

    def _make_appointment(**kwargs):
        if "patient" not in kwargs:
            kwargs["patient"] = make_patient()
        if "doctor" not in kwargs:
            kwargs["doctor"] = make_doctor()
        kwargs.setdefault("date", date(2026, 10, 5))
        kwargs.setdefault("start_time", time(9, 0))
        start = datetime.combine(kwargs["date"], kwargs["start_time"])
        kwargs.setdefault("end_time", (start + timedelta(minutes=15)).time())
        kwargs.setdefault("reason", "General check-up")
        kwargs.setdefault("consultation_fee", kwargs["doctor"].consultation_fee)
        return Appointment.objects.create(**kwargs)

    return _make_appointment


@pytest.fixture
def make_checked_in_appointment(make_appointment):
    """A CHECKED_IN appointment for today (real date), ready for a consultation."""

    def _make_checked_in_appointment(**kwargs):
        kwargs.setdefault("date", timezone.localdate())
        kwargs.setdefault("status", Status.CHECKED_IN)
        kwargs.setdefault("checked_in_at", timezone.now())
        return make_appointment(**kwargs)

    return _make_checked_in_appointment


@pytest.fixture
def make_record(make_checked_in_appointment):
    """A MedicalRecord (DRAFT by default) created directly, without service checks."""

    def _make_record(appointment=None, status=RecordStatus.DRAFT, **kwargs):
        appointment = appointment or make_checked_in_appointment()
        kwargs.setdefault("presenting_complaint", appointment.reason)
        if status == RecordStatus.FINALIZED:
            kwargs.setdefault("finalized_at", timezone.now())
        return MedicalRecord.objects.create(
            appointment=appointment,
            patient=appointment.patient,
            doctor=appointment.doctor,
            status=status,
            **kwargs,
        )

    return _make_record


@pytest.fixture
def make_medicine(db):
    counter = itertools.count(1)

    def _make_medicine(**kwargs):
        kwargs.setdefault("name", f"Medicine {next(counter)}")
        kwargs.setdefault("strength", "500 mg")
        kwargs.setdefault("form", Medicine.Form.TABLET)
        return Medicine.objects.create(**kwargs)

    return _make_medicine


@pytest.fixture
def make_completed_appointment(make_checked_in_appointment, make_record):
    """A COMPLETED appointment reached the real way: finalized record, then complete."""

    def _make_completed_appointment(**kwargs):
        appointment = make_checked_in_appointment(**kwargs)
        make_record(appointment=appointment, status=RecordStatus.FINALIZED)
        return appointment_services.complete_appointment(
            appointment, acting_user=appointment.doctor.user
        )

    return _make_completed_appointment


@pytest.fixture
def make_charge(make_patient):
    """An unbilled charge created directly. amount is quantity x unit_price."""

    def _make_charge(**kwargs):
        if "patient" not in kwargs:
            kwargs["patient"] = make_patient()
        kwargs.setdefault("charge_type", ChargeType.OTHER)
        kwargs.setdefault("description", "Test charge")
        kwargs.setdefault("quantity", 1)
        kwargs["unit_price"] = Decimal(str(kwargs.get("unit_price", "1000.00")))
        kwargs.setdefault(
            "amount", (kwargs["quantity"] * kwargs["unit_price"]).quantize(Decimal("0.01"))
        )
        return Charge.objects.create(**kwargs)

    return _make_charge


@pytest.fixture
def make_invoice(make_patient, make_charge):
    """An invoice (DRAFT by default) with one charge per amount in `amounts`."""

    def _make_invoice(patient=None, amounts=("1000.00",), **kwargs):
        patient = patient or make_patient()
        invoice = Invoice.objects.create(patient=patient, **kwargs)
        for amount in amounts:
            make_charge(patient=patient, unit_price=amount, invoice=invoice)
        return invoice

    return _make_invoice


HAEMOGLOBIN = {"name": "Haemoglobin", "unit": "g/dL", "ref_low": "12", "ref_high": "16"}


@pytest.fixture
def make_lab_test(db):
    """A lab test with parameters (dicts of LabTestParameter fields). Default: one
    numeric parameter, Haemoglobin 12-16 g/dL. Pass parameters=[] for none."""
    counter = itertools.count(1)

    def _make_lab_test(parameters=None, **kwargs):
        n = next(counter)
        kwargs.setdefault("code", f"T{n:03d}")
        kwargs.setdefault("name", f"Test {n}")
        kwargs.setdefault("section", "HAEMATOLOGY")
        kwargs.setdefault("specimen_type", "BLOOD")
        kwargs.setdefault("price", Decimal("1200.00"))
        test = LabTest.objects.create(**kwargs)
        for order, data in enumerate(parameters if parameters is not None else [HAEMOGLOBIN]):
            data = dict(data)
            for bound in ("ref_low", "ref_high"):
                if data.get(bound) is not None:
                    data[bound] = Decimal(str(data[bound]))
            LabTestParameter.objects.create(test=test, display_order=order, **data)
        return test

    return _make_lab_test


@pytest.fixture
def make_lab_order(make_patient, make_lab_test):
    """A lab order created directly (no billing). With record= it is a consultation
    order; otherwise a walk-in referred by "Dr. External"."""

    def _make_lab_order(record=None, tests=None, **kwargs):
        if record is not None:
            kwargs.update(record=record, patient=record.patient, ordering_doctor=record.doctor)
        else:
            kwargs.setdefault("patient", make_patient())
            kwargs.setdefault("referred_by", "Dr. External")
        order = LabOrder.objects.create(**kwargs)
        for test in tests if tests is not None else [make_lab_test()]:
            LabOrderItem.objects.create(order=order, test=test, price=test.price)
        return order

    return _make_lab_order


# Smallest byte strings that pass the file-type checks in common/validators.py.
SAMPLE_FILE_BYTES = {
    "pdf": b"%PDF-1.4\n% test document\n",
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
    "jpg": b"\xff\xd8\xff\xe0" + b"\x00" * 16,
}


@pytest.fixture
def make_upload():
    """make_upload("scan.pdf") -> an uploaded file with valid PDF bytes.
    Pass content=... to control the bytes (e.g. to fake a renamed file)."""

    def _make_upload(name="report.pdf", content=SAMPLE_FILE_BYTES["pdf"], content_type=None):
        return SimpleUploadedFile(name, content, content_type=content_type)

    return _make_upload


@pytest.fixture
def admin_user_obj(make_user):
    return make_user(role=Role.ADMIN, username="admin1")


@pytest.fixture
def client_for_role(client, make_user):
    """Returns a function: client_for_role(Role.NURSE) -> client logged in as a new nurse."""

    def _client_for_role(role):
        client.force_login(make_user(role=role))
        return client

    return _client_for_role
