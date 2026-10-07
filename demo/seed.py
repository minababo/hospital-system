"""The demo dataset, created through the same services the web pages use.

How it stays repeatable and safe:
- The PLAN (who books which slot, which visits get invoiced, ...) comes from one
  random.Random(seed), so it is the same on every run. Values inside a scenario
  (lab results, vitals, times) use their own Random(f"{seed}-{key}") so a skipped
  scenario doesn't shift anything else.
- Catalog data is found by natural keys before creating; patients by NIC (or name and
  date of birth for children). A patient's clinical scenario only runs if that patient
  has no appointments (or admissions) yet, so a second run creates nothing.
- Each scenario runs in its own transaction.atomic(): it is either fully there or not.

Timeline: everything is relative to the moment the run starts (`self.now`, Asia/Colombo
dates) and nothing is stamped later than that. Services that accept now= get the
intended time. Fields the services always fill with the current time (auto_now_add,
auto_now, default=timezone.now) are corrected afterwards with backdate(), only for rows
created in this run, inside the same transaction. Audit entries are never changed: they
record when the seed actually wrote the data.
"""

import random
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection, transaction
from django.db.models import Max
from django.utils import timezone

from accounts import services as account_services
from accounts.models import Role
from admissions import services as admission_services
from admissions.models import Admission, Bed, BedAssignment, Ward
from appointments import services as appointment_services
from appointments.models import Appointment
from appointments.selectors import available_slots
from audit.services import Action, log_action
from billing import services as billing_services
from billing.models import Charge, Invoice
from demo import data
from doctors import services as doctor_services
from doctors.models import Department, Doctor
from laboratory import services as lab_services
from laboratory.models import LabOrder, LabOrderItem, LabTest, LabTestParameter
from laboratory.selectors import orderable_tests
from patients import services as patient_services
from patients.models import Patient
from pharmacy import dispensing
from pharmacy import services as pharmacy_services
from pharmacy.dispensing_selectors import item_progress
from pharmacy.models import DispenseItem, Medicine, StockBatch, StockMovement
from records import services as record_services
from records.models import Prescription
from staff import services as staff_services
from staff.models import Attendance, Employee, LeaveRequest

User = get_user_model()


def database_engine():
    """The default database's engine and host. A function so tests can pretend the
    database isn't SQLite."""
    settings = connection.settings_dict
    return settings["ENGINE"], settings.get("HOST") or "(local)"


def at(day, hour, minute=0):
    """Aware datetime for a local (Asia/Colombo) day and time."""
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def shift(moment, minutes):
    return moment + timedelta(minutes=minutes)


# --- Backdating ---------------------------------------------------------------------------

# The "created" column of each model, when it isn't called created_at.
CREATED_FIELD = {"User": "date_joined", "Attendance": "recorded_at"}


def backdate(model, pk, **fields):
    """Set timestamp columns of one row with QuerySet.update(), which skips auto_now /
    auto_now_add. updated_at (where the model has it) defaults to the latest given time.
    Only the seed calls this, and only for rows it created in the same run."""
    names = {f.name for f in model._meta.concrete_fields}
    fields = {name: value for name, value in fields.items() if name in names}
    if "updated_at" in names and "updated_at" not in fields and fields:
        fields["updated_at"] = max(fields.values())
    if fields:
        model._base_manager.filter(pk=pk).update(**fields)


class Stamps:
    """Intended times for rows created in the current scenario: when each was created and
    its last event (for updated_at). apply() writes them just before the scenario's
    transaction commits; later service calls in the scenario would otherwise overwrite
    updated_at again."""

    def __init__(self):
        self.rows = {}

    def created(self, obj, when):
        record = self.rows.setdefault((type(obj), obj.pk), {})
        record["created"] = when
        record["last"] = max(record.get("last", when), when)

    def touched(self, obj, when):
        record = self.rows.setdefault((type(obj), obj.pk), {})
        record["last"] = max(record.get("last", when), when)

    def apply(self):
        for (model, pk), record in self.rows.items():
            fields = {}
            if "created" in record:
                fields[CREATED_FIELD.get(model.__name__, "created_at")] = record["created"]
            fields["updated_at"] = record["last"]
            backdate(model, pk, **fields)
        self.rows.clear()


class SeedError(Exception):
    """A scenario failed in a way the seed doesn't expect; stop and report it."""


# --- Report ---------------------------------------------------------------------------


@dataclass
class Report:
    created: Counter = field(default_factory=Counter)
    skipped: Counter = field(default_factory=Counter)
    warnings: list = field(default_factory=list)
    users: list = field(default_factory=list)  # (username, role label)

    def add(self, category, created=True, count=1):
        (self.created if created else self.skipped)[category] += count

    def warn(self, message):
        self.warnings.append(message)

    def categories(self):
        seen = list(self.created) + [c for c in self.skipped if c not in self.created]
        return [(c, self.created[c], self.skipped[c]) for c in seen]

    def created_anything(self):
        return any(count for name, count in self.created.items() if name != "Passwords reset")


# --- The plan ---------------------------------------------------------------------------


@dataclass
class Visit:
    """One planned appointment for one patient."""

    doctor_username: str
    day: date
    start: time
    outcome: str  # COMPLETED, CANCELLED, NO_SHOW, BOOKED, CHECKED_IN
    diagnosis: int = 0  # index into data.DIAGNOSES
    billing: str = ""  # see Seeder.bill
    lab: str = ""  # "", COMPLETED, COLLECTED, REQUESTED, URGENT, CANCELLED
    dispense: str = "FULL"  # FULL, PARTIAL, NONE
    allergy_override: bool = False


@dataclass
class PatientPlan:
    key: str  # "nic:..." or "child:first/last/dob"
    data: dict
    visits: list = field(default_factory=list)
    admission: dict | None = None
    walk_in_lab: bool = False


# Plan positions that are children (no NIC; found again by name and date of birth).
CHILD_INDEXES = (1, 4, 7, 11)


class Seeder:
    def __init__(self, *, password, reset_passwords=False, seed=42, scale=1.0, stdout=None):
        self.password = password
        self.reset_passwords = reset_passwords
        self.seed = seed
        self.scale = scale
        self.rng = random.Random(seed)  # the plan only; never the global random module
        self.now = timezone.now()  # the run time: nothing is stamped later than this
        self.today = timezone.localdate(self.now)
        self.report = Report()
        self.stamps = Stamps()
        self.users = {}  # username -> User (only demo users with the right role)
        self.doctors = {}  # username -> Doctor
        self.released_lab_orders = 0  # every 4th released order goes over its turnaround
        self.log = stdout or (lambda message: None)

    # --- helpers --------------------------------------------------------------------

    def scaled(self, n, minimum=1):
        return max(minimum, round(n * self.scale))

    def scenario_rng(self, key):
        return random.Random(f"{self.seed}-{key}")

    def clamp(self, moment):
        """Never later than the run itself (a recent event can't be in the future)."""
        return min(moment, self.now - timedelta(minutes=1))

    def days_ago(self, days, hour=9, minute=0):
        return at(self.today - timedelta(days=days), hour, minute)

    def user(self, username):
        return self.users.get(username)

    def actor(self, role):
        """The demo user for a role, falling back to the demo admin."""
        username = {
            Role.ADMIN: "demo.admin",
            Role.NURSE: "demo.nurse",
            Role.RECEPTIONIST: "demo.reception",
            Role.LAB_STAFF: "demo.lab",
            Role.PHARMACIST: "demo.pharmacist",
            Role.ACCOUNTANT: "demo.accountant",
        }[role]
        return self.users.get(username) or self.users.get("demo.admin")

    def step(self, name, function):
        """Run one scenario in its own transaction (backdating included); stop everything
        on an unexpected error."""
        try:
            with transaction.atomic():
                result = function()
                self.stamps.apply()
                return result
        except Exception as error:  # noqa: BLE001 - re-raised with the scenario name
            self.stamps.rows.clear()
            raise SeedError(f"{name}: {type(error).__name__}: {error}") from error

    # --- entry point ------------------------------------------------------------------

    def run(self):
        self.step("users", self.seed_users)
        if "demo.admin" not in self.users:
            raise SeedError("users: the demo.admin account is needed to create the rest.")
        self.step("departments", self.seed_departments)
        self.step("doctors", self.seed_doctors)
        self.step("medicines", self.seed_medicines)
        self.step("lab tests", self.seed_lab_tests)
        self.keep_orderable_tests()
        self.step("wards", self.seed_wards)
        self.step("employees", self.seed_employees)
        plans = self.make_plan()
        for plan in plans:
            self.step(f"patient {plan.key}", lambda plan=plan: self.seed_patient(plan))
        self.step("attendance", self.seed_attendance)
        self.step("leave", self.seed_leave)
        self.report.warn("No documents were uploaded (the demo has no files).")
        if self.report.created_anything():
            self.step("summary", self.log_summary)
        return self.report

    def log_summary(self):
        """One audit entry for the whole run. The seeded rows' own audit entries carry the
        time they were written (audit entries are never backdated or changed)."""
        created = self.report.created
        parts = [
            f"{created['Patients']} patients",
            f"{sum(created[c] for c in created if c.startswith('Appointments'))} appointments",
            f"{created['Consultations']} consultations",
            f"{created['Lab orders']} lab orders",
            f"{created['Dispenses']} dispenses",
            f"{created['Invoices']} invoices",
            f"{created['Admissions (current)'] + created['Admissions (discharged)']} admissions",
            f"{created['Employees']} employees",
        ]
        log_action(
            actor=self.user("demo.admin"),
            action=Action.CREATE,
            event="demo.seed.completed",
            message="Demo data seeded: " + ", ".join(parts),
        )

    # --- users ---------------------------------------------------------------------------

    def joined(self, index):
        """Logins and catalog rows exist long before the activity starts."""
        return self.days_ago(420 + 23 * index, 9, 30)

    def seed_users(self):
        admin = None
        for index, (username, role, first, last) in enumerate(data.DEMO_USERS):
            if role == Role.DOCTOR:
                continue  # created with their profile in seed_doctors
            user = self.ensure_user(username, role, first, last, acting_user=admin, index=index)
            if username == "demo.admin":
                admin = user
        for row in data.DOCTORS:
            self.ensure_user(row[0], Role.DOCTOR, row[1], row[2], acting_user=admin, index=0)

    def ensure_user(self, username, role, first, last, *, acting_user, index):
        existing = User.objects.filter(username__iexact=username).first()
        if existing is None:
            if role == Role.DOCTOR:
                return None  # seed_doctors creates the user and profile together
            user = account_services.create_user(
                username=username,
                password=self.password,
                role=role,
                first_name=first,
                last_name=last,
                email=f"{username.replace('.', '_')}@hms-demo.lk",
                acting_user=acting_user,
            )
            self.stamps.created(user, self.joined(index))
            self.users[username] = user
            self.report.add("Users")
            self.report.users.append((username, user.get_role_display()))
            return user
        if existing.is_superuser or existing.role != role:
            self.report.warn(
                f"User {existing.username} already exists with role "
                f"{existing.get_role_display()}{' (superuser)' if existing.is_superuser else ''}; "
                "left untouched and not used by the demo."
            )
            self.report.add("Users", created=False)
            return None
        if self.reset_passwords:
            account_services.set_user_password(
                existing, self.password, acting_user=acting_user or existing
            )
            self.report.add("Passwords reset")
        self.users[username] = existing
        self.report.add("Users", created=False)
        self.report.users.append((username, existing.get_role_display()))
        return existing

    # --- catalog ---------------------------------------------------------------------------

    def seed_departments(self):
        admin = self.user("demo.admin")
        self.departments = {}
        for name, description in data.DEPARTMENTS:
            department = Department.objects.filter(name__iexact=name).first()
            if department is None:
                department = doctor_services.create_department(
                    name=name, description=description, acting_user=admin
                )
                self.stamps.created(department, self.days_ago(700))
                self.report.add("Departments")
            else:
                self.report.add("Departments", created=False)
            self.departments[name] = department

    def seed_doctors(self):
        admin = self.user("demo.admin")
        for index, row in enumerate(data.DOCTORS):
            username, first, last, dept, spec, reg, phone, fee, blocks = row
            if not self.departments[dept].is_active:
                # An existing department someone deactivated: leave it alone. A new doctor
                # can't join it (and it couldn't take bookings), so skip this doctor.
                self.report.warn(
                    f"Department {dept} is inactive; doctor {username} and their visits "
                    "were skipped."
                )
                continue
            existing = User.objects.filter(username__iexact=username).first()
            profile = {
                "department": self.departments[dept],
                "specialization": spec,
                "registration_number": reg,
                "qualification": "MBBS (Colombo)",
                "phone": phone,
                "consultation_fee": fee,
            }
            joined = self.days_ago(450 + 31 * index, 10)
            if existing is None:
                doctor = doctor_services.create_doctor(
                    user_data={
                        "username": username,
                        "password": self.password,
                        "first_name": first,
                        "last_name": last,
                        "email": f"{username.replace('.', '_')}@hms-demo.lk",
                    },
                    profile_data=profile,
                    acting_user=admin,
                )
                self.stamps.created(doctor.user, joined)
                self.stamps.created(doctor, joined)
                self.users[username] = doctor.user
                self.report.users.append((username, doctor.user.get_role_display()))
                self.report.add("Users")
                self.report.add("Doctors")
            elif username not in self.users:
                continue  # wrong role or superuser: warned in seed_users
            else:
                doctor = Doctor.objects.filter(user=existing).first()
                if doctor is None:
                    doctor = doctor_services.create_doctor_profile(
                        user=existing, profile_data=profile, acting_user=admin
                    )
                    self.stamps.created(doctor, joined)
                    self.report.add("Doctors")
                else:
                    self.report.add("Doctors", created=False)
            self.doctors[username] = doctor
            if doctor.schedules.exists():
                self.report.add("Schedule blocks", created=False, count=len(blocks))
                continue
            for weekday, start, end, minutes in blocks:
                doctor_services.create_schedule(
                    doctor=doctor,
                    acting_user=admin,
                    weekday=weekday,
                    start_time=start,
                    end_time=end,
                    slot_minutes=minutes,
                )
                self.report.add("Schedule blocks")

    def seed_medicines(self):
        pharmacist = self.actor(Role.PHARMACIST)
        self.medicines = {}
        for name, generic, form, strength, price, reorder, plan in data.MEDICINES:
            medicine = Medicine.objects.filter(
                name__iexact=name, strength__iexact=strength, form=form
            ).first()
            if medicine is None:
                medicine = pharmacy_services.create_medicine(
                    acting_user=pharmacist,
                    name=name,
                    generic_name=generic,
                    form=form,
                    strength=strength,
                    unit_price=Decimal(price),
                    reorder_level=reorder,
                )
                self.stamps.created(medicine, self.days_ago(400))
                self.report.add("Medicines")
            else:
                self.report.add("Medicines", created=False)
            self.medicines[name] = medicine
            self.stock_medicine(medicine, plan, reorder, pharmacist)

    def stock_medicine(self, medicine, plan, reorder, pharmacist):
        """Batches by stock plan. A medicine that already has batches is left alone.
        Stock arrives 110-180 days ago: 1-6 months before the visits that use it."""
        if plan == "none":
            return
        if StockBatch.objects.filter(medicine=medicine).exists():
            self.report.add("Stock batches", created=False)
            return
        code = medicine.name[:3].upper()
        rng = self.scenario_rng(f"stock-{medicine.name}")
        received = self.days_ago(110 + rng.randrange(0, 70), 10)
        batches = []
        if plan == "expired":
            # Received long ago; expired 10 days ago and still on the shelf (Stock alerts
            # lists it for write-off). A newer batch keeps the medicine itself in stock.
            batches.append(("X01", self.today - timedelta(days=10), 120, self.days_ago(300, 10)))
            batches.append(("A01", self.today + timedelta(days=500), 1000, received))
        elif plan == "low":
            batches.append(
                ("L01", self.today + timedelta(days=300), max(2, reorder // 2), received)
            )
        else:
            batches.append(("A01", self.today + timedelta(days=420), 3000, received))
            batches.append(("A02", self.today + timedelta(days=600), 2000, received))
            if plan == "expiring":
                batches.append(("E01", self.today + timedelta(days=45), 400, received))
        for suffix, expiry, quantity, when in batches:
            batch = pharmacy_services.receive_stock(
                medicine=medicine,
                batch_number=f"{code}-{suffix}",
                expiry_date=expiry,
                quantity=quantity,
                supplier="State Pharmaceuticals Corporation",
                notes="",
                acting_user=pharmacist,
                now=when,
            )
            # The RECEIVE movement is the batch's first ledger row, at the receipt time.
            self.stamps.created(batch.movements.order_by("pk").first(), when)
            self.report.add("Stock batches")

    def seed_lab_tests(self):
        lab = self.actor(Role.LAB_STAFF)
        self.lab_tests = {}
        for code, name, section, specimen, price, hours, parameters in data.LAB_TESTS:
            test = LabTest.objects.filter(code__iexact=code).first()
            if test is not None:
                self.report.add("Lab tests", created=False)
                self.lab_tests[code] = test
                continue
            test = lab_services.create_lab_test(
                acting_user=lab,
                code=code,
                name=name,
                section=section,
                specimen_type=specimen,
                price=Decimal(price),
                turnaround_hours=hours,
            )
            self.stamps.created(test, self.days_ago(400, 11))
            for order, (pname, unit, low, high) in enumerate(parameters):
                fields = {"name": pname, "unit": unit or "", "display_order": order}
                if unit is None:
                    fields["result_type"] = LabTestParameter.ResultType.TEXT
                    fields["ref_text"] = high
                else:
                    fields["ref_low"] = Decimal(low) if low else None
                    fields["ref_high"] = Decimal(high) if high else None
                lab_services.add_parameter(test, acting_user=lab, **fields)
            self.report.add("Lab tests")
            self.lab_tests[code] = test

    def keep_orderable_tests(self):
        orderable = set(orderable_tests().values_list("pk", flat=True))
        for code, test in list(self.lab_tests.items()):
            if test.pk not in orderable:
                self.report.warn(
                    f"Lab test {code} exists but is inactive or has no parameters; "
                    "the demo doesn't order it."
                )
                del self.lab_tests[code]

    def seed_wards(self):
        admin = self.user("demo.admin")
        self.beds = []
        for name, ward_type, dept, rate, beds in data.WARDS:
            ward = Ward.objects.filter(name__iexact=name).first()
            if ward is None:
                ward = admission_services.create_ward(
                    acting_user=admin,
                    name=name,
                    ward_type=ward_type,
                    department=self.departments[dept],
                    daily_rate=rate,
                )
                self.stamps.created(ward, self.days_ago(650))
                self.report.add("Wards")
            else:
                self.report.add("Wards", created=False)
            prefix = "".join(word[0] for word in name.split()).upper()
            for n in range(1, beds + 1):
                number = f"{prefix}-{n:02d}"
                bed = Bed.objects.filter(ward=ward, bed_number__iexact=number).first()
                if bed is None:
                    bed = admission_services.add_bed(ward, acting_user=admin, bed_number=number)
                    self.report.add("Beds")
                else:
                    self.report.add("Beds", created=False)
                self.beds.append(bed)

    # --- staff -------------------------------------------------------------------------------

    def seed_employees(self):
        admin = self.user("demo.admin")
        rng = self.scenario_rng("employees")
        people = []
        for username, (designation, category, dept) in data.DEMO_EMPLOYEES.items():
            user = self.user(username)
            if user is None:
                continue
            gender = (
                "FEMALE"
                if user.first_name in {"Shanika", "Fathima", "Dilani", "Kavindi", "Selvi"}
                else "MALE"
            )
            people.append(
                (user.first_name, user.last_name, designation, category, dept, gender, user)
            )
        for extra in data.EXTRA_STAFF[: self.scaled(len(data.EXTRA_STAFF), minimum=4)]:
            people.append((*extra, None))
        for index, (first, last, designation, category, dept, gender, user) in enumerate(people):
            born = date(1975 + index % 20, 1 + index % 12, 1 + index % 27)
            nic = make_nic(born, gender, 7000 + index, old_format=index % 3 == 0)
            if Employee.objects.filter(nic__iexact=nic).exists():
                self.report.add("Employees", created=False)
                continue
            if user is not None and Employee.objects.filter(user=user).exists():
                self.report.add("Employees", created=False)
                continue
            joined = self.today - timedelta(days=200 + 37 * index)
            employee = staff_services.create_employee(
                acting_user=admin,
                user=user,
                first_name=first,
                last_name=last,
                nic=nic,
                phone=f"07{rng.choice('0125678')}{rng.randrange(1000000, 9999999)}",
                email=f"{first.lower()}.{last.lower()}@hms-demo.lk",
                address=(
                    f"{rng.randrange(1, 200)}, {rng.choice(data.STREETS)}, {rng.choice(data.TOWNS)}"
                ),
                designation=designation,
                category=category,
                employment_type="PERMANENT" if index % 4 else "CONTRACT",
                department=self.departments[dept],
                date_joined=joined,
            )
            # HR registered them on their first morning.
            self.stamps.created(employee, at(joined, 8, 30))
            self.report.add("Employees")

    def seed_attendance(self):
        """The last 14 working days (Mon-Fri). A day that already has demo attendance
        is skipped, so the sheet is never re-saved. Each sheet is recorded at the end of
        that day's shift."""
        admin = self.user("demo.admin")
        employees = list(Employee.objects.filter(email__endswith="@hms-demo.lk").order_by("pk"))
        if not employees:
            return
        days, day = [], self.today
        while len(days) < 14:
            if day.weekday() < 5:
                days.append(day)
            day -= timedelta(days=1)
        for day in reversed(days):
            if Attendance.objects.filter(date=day, employee__in=employees).exists():
                self.report.add("Attendance days", created=False)
                continue
            rng = self.scenario_rng(f"attendance-{day.isoformat()}")
            rows = []
            for employee in employees:
                if employee.date_joined > day:
                    continue
                roll = rng.random()
                if roll < 0.05:
                    rows.append({"employee_id": employee.pk, "status": "ABSENT"})
                elif roll < 0.1:
                    rows.append(
                        {
                            "employee_id": employee.pk,
                            "status": "HALF_DAY",
                            "check_in": time(8),
                            "check_out": time(12, 30),
                        }
                    )
                else:
                    rows.append(
                        {
                            "employee_id": employee.pk,
                            "status": "PRESENT",
                            "check_in": time(7, 45 + rng.randrange(0, 14)),
                            "check_out": time(16, rng.randrange(0, 59)),
                        }
                    )
            recorded = max(self.clamp(at(day, 16, 45)), at(day, 0, 1))
            records = staff_services.save_attendance_sheet(
                date=day, rows=rows, acting_user=admin, now=recorded
            )
            for record in records:
                self.stamps.created(record, recorded)
            self.report.add("Attendance days")

    def seed_leave(self):
        """Approved (recorded by the admin), pending and rejected requests, all for future
        dates so they never clash with attendance or with doctors' bookings. Requests and
        decisions happened in the last few days."""
        admin = self.user("demo.admin")
        plans = [
            ("record", "Sajith", "ANNUAL", 10, 12, "Family wedding in Kandy", 6),
            ("pending", "demo.nurse", "ANNUAL", 18, 19, "Attending a training course", 2),
            ("pending", "demo.lab", "CASUAL", 6, 6, "Personal matter", 1),
            ("rejected", "demo.reception", "ANNUAL", 3, 7, "Holiday in Ella", 5),
        ]
        for kind, who, leave_type, start, end, reason, asked_days_ago in plans:
            if who.startswith("demo."):
                user = self.user(who)  # None when that login was left untouched
                employee = Employee.objects.filter(user=user).first() if user else None
            else:
                employee = Employee.objects.filter(
                    first_name=who, email__endswith="@hms-demo.lk"
                ).first()
            if employee is None:
                continue
            if LeaveRequest.objects.filter(employee=employee).exists():
                self.report.add("Leave requests", created=False)
                continue
            first, last = self.today + timedelta(days=start), self.today + timedelta(days=end)
            asked = self.clamp(self.days_ago(asked_days_ago, 10, 15))
            if kind == "record":
                staff_services.record_leave(
                    employee=employee,
                    leave_type=leave_type,
                    start_date=first,
                    end_date=last,
                    reason=reason,
                    acting_user=admin,
                    now=asked,
                )
            else:
                leave = staff_services.request_leave(
                    user=employee.user,
                    leave_type=leave_type,
                    start_date=first,
                    end_date=last,
                    reason=reason,
                    now=asked,
                )
                if kind == "rejected":
                    staff_services.reject_leave(
                        leave,
                        note="Two receptionists are already on leave that week.",
                        acting_user=admin,
                        now=self.clamp(asked + timedelta(days=1, hours=2)),
                    )
            self.report.add("Leave requests")

    # --- the patient plan -----------------------------------------------------------------

    def make_plan(self):
        """Patients and everything that happens to them. Pure: no database access, so the
        plan is identical on every run with the same seed and scale."""
        rng = self.rng
        n_patients = self.scaled(40, minimum=10)
        n_current, n_discharged = self.scaled(4), self.scaled(6)
        plans = [self.make_patient(i, rng) for i in range(n_patients)]
        # The last patients are admissions only; the rest are outpatients.
        admission_plans = plans[-(n_current + n_discharged) :]
        outpatients = plans[: n_patients - len(admission_plans)]

        doctor_days = []  # (day, username) where the doctor works, past 30 days
        for offset in range(30, 0, -1):
            day = self.today - timedelta(days=offset)
            for username, *_rest, blocks in data.DOCTORS:
                if any(block[0] == day.weekday() for block in blocks):
                    doctor_days.append((day, username))
        rng.shuffle(doctor_days)

        used = defaultdict(set)  # (username, day) -> start times taken
        busy = defaultdict(set)  # day -> patient indexes with a visit that day

        def slot_for(username, day):
            blocks = next(row[8] for row in data.DOCTORS if row[0] == username)
            starts = []
            for weekday, start, end, minutes in blocks:
                if weekday != day.weekday():
                    continue
                current = datetime.combine(day, start)
                while current + timedelta(minutes=minutes) <= datetime.combine(day, end):
                    starts.append(current.time())
                    current += timedelta(minutes=minutes)
            free = [s for s in starts if s not in used[(username, day)]]
            if not free:
                return None
            choice = free[rng.randrange(len(free))]
            used[(username, day)].add(choice)
            return choice

        def add_visit(day, username, outcome, patients=outpatients):
            order = list(range(len(patients)))
            rng.shuffle(order)
            for index in order:
                if index in busy[day]:
                    continue
                start = slot_for(username, day)
                if start is None:
                    return None
                busy[day].add(index)
                visit = Visit(
                    username, day, start, outcome, diagnosis=rng.randrange(len(data.DIAGNOSES))
                )
                patients[index].visits.append(visit)
                return visit
            return None

        # Past 30 days: mostly completed, some cancelled and no-shows.
        target = self.scaled(110, minimum=12)
        made, progress = 0, True
        while made < target and progress:
            progress = False
            for day, username in doctor_days:
                if made >= target:
                    break
                roll = rng.random()
                outcome = "COMPLETED" if roll < 0.8 else ("CANCELLED" if roll < 0.92 else "NO_SHOW")
                if add_visit(day, username, outcome):
                    made += 1
                    progress = True
        # Two older completed visits, so the invoice aging report has 31-60 and 61-90 days.
        for offset in (45, 75):
            day = self.today - timedelta(days=offset)
            visit = add_visit(day, "demo.dr.ashraf", "COMPLETED")
            if visit:
                visit.billing = "OUTSTANDING_OLD"
        # Today: a queue for the dashboards (booked and checked in).
        working_today = [
            row[0] for row in data.DOCTORS if any(b[0] == self.today.weekday() for b in row[8])
        ]
        for n in range(self.scaled(10, minimum=3)):
            add_visit(
                self.today,
                working_today[n % len(working_today)],
                "CHECKED_IN" if n % 2 else "BOOKED",
            )
        # Next 14 days.
        future_days = [
            (self.today + timedelta(days=offset), row[0])
            for offset in range(1, 15)
            for row in data.DOCTORS
            if any(b[0] == (self.today + timedelta(days=offset)).weekday() for b in row[8])
        ]
        rng.shuffle(future_days)
        for day, username in future_days[: self.scaled(30, minimum=4)]:
            add_visit(day, username, "BOOKED")

        self.plan_outcomes(outpatients, rng)
        self.plan_admissions(admission_plans, n_current)
        outpatients[0].walk_in_lab = True
        return plans

    def plan_outcomes(self, outpatients, rng):
        """Billing, lab and dispensing outcomes for completed visits, including one of
        each special case the reviewers should be able to find."""
        completed = sorted(
            (
                v
                for p in outpatients
                for v in p.visits
                if v.outcome == "COMPLETED" and not v.billing
            ),
            key=lambda v: (v.day, v.start),
        )
        specials = ["DRAFT", "DISCOUNT", "VOID", "VOIDED_PAYMENT"]
        for index, visit in enumerate(completed):
            if index < len(specials):
                visit.billing = specials[index]
            else:
                roll = rng.random()
                visit.billing = (
                    "PAID_CASH"
                    if roll < 0.55
                    else "PAID_CARD"
                    if roll < 0.7
                    else "PARTIAL"
                    if roll < 0.82
                    else "OUTSTANDING"
                    if roll < 0.92
                    else "NONE"
                )
            roll = rng.random()
            visit.dispense = "FULL" if roll < 0.85 else ("PARTIAL" if roll < 0.95 else "NONE")
        # Lab orders on about a third of completed visits whose diagnosis has tests.
        with_tests = [v for v in completed if data.DIAGNOSES[v.diagnosis][3]]
        lab_target = self.scaled(28, minimum=5)
        cycle = [
            "COMPLETED",
            "COMPLETED",
            "COLLECTED",
            "COMPLETED",
            "URGENT",
            "COMPLETED",
            "REQUESTED",
            "COMPLETED",
            "COLLECTED",
            "COMPLETED",
            "URGENT",
        ]
        for index, visit in enumerate(with_tests[:lab_target]):
            visit.lab = "CANCELLED" if index == 0 else cycle[(index - 1) % len(cycle)]
        # One prescription despite a recorded allergy (Amoxicillin for a penicillin allergy).
        for patient in outpatients:
            if "amoxicillin" in patient.data["allergies"].lower():
                for visit in patient.visits:
                    if visit.outcome == "COMPLETED":
                        visit.diagnosis = 0  # URTI: Amoxicillin is on the list
                        visit.allergy_override = True
                        return

    def plan_admissions(self, plans, n_current):
        reasons = data.ADMISSION_REASONS
        bed_index = 0
        doctors = ["demo.doctor", "demo.dr.kumaran", "demo.dr.wijesinghe", "demo.dr.fernando"]
        for index, plan in enumerate(plans):
            current = index >= len(plans) - n_current
            ward = (
                2
                if "delivery" in reasons[index % len(reasons)][0]
                else (1 if index % 5 == 1 else 0)
            )
            if current:
                admitted = at(self.today - timedelta(days=1 + index % 5), 10 + index % 6)
                discharged = None
            else:
                admitted = at(self.today - timedelta(days=8 + 3 * index), 9 + index % 8)
                discharged = admitted + timedelta(days=2 + index % 4, hours=3)
            plan.admission = {
                "ward": ward,
                "bed": bed_index,
                "reason": reasons[index % len(reasons)],
                "doctor": doctors[index % len(doctors)],
                "admitted": admitted,
                "discharged": discharged,
                "transfer": current and index == len(plans) - n_current,
                "invoice": "PAID_CASH" if index % 2 == 0 else "OUTSTANDING",
            }
            bed_index += 2  # leave room for a transfer bed

    def make_patient(self, index, rng):
        """One fictional patient. Every adult has a NIC (old or new format); a few
        children have none and are identified by name and date of birth."""
        if index in CHILD_INDEXES:
            position = CHILD_INDEXES.index(index)
            first, last, gender = data.CHILD_NAMES[position]
            born = self.today - timedelta(days=365 * (3 + 3 * position) + 45)
            nic = None
        else:
            community = [data.SINHALA, data.SINHALA, data.TAMIL, data.MUSLIM][index % 4]
            first, gender = community[0][index * 7 % len(community[0])]
            last = community[1][index * 5 % len(community[1])]
            born = date(1945 + (index * 13) % 55, 1 + index % 12, 1 + (index * 3) % 27)
            nic = make_nic(
                born, gender, 1000 + index, old_format=born.year < 1990 and index % 2 == 0
            )
        allergies = ""
        if index in (2, 9, 17):
            allergies = "Penicillin (amoxicillin) - rash"
        elif index in (5, 21):
            allergies = "Sulfa drugs"
        town = data.TOWNS[index % len(data.TOWNS)]
        phone_prefix = ["071", "072", "075", "076", "077", "078", "070"][index % 7]
        patient = {
            "first_name": first,
            "last_name": last,
            "date_of_birth": born,
            "gender": gender,
            "nic": nic,
            "phone": f"{phone_prefix}{rng.randrange(1000000, 9999999)}",
            "email": "" if index % 3 else f"{first.lower()}{index}@example.lk",
            "address": f"No. {rng.randrange(1, 250)}, {rng.choice(data.STREETS)}, {town}",
            "blood_group": rng.choice(["A+", "B+", "O+", "AB+", "O-", "A-", "UNKNOWN"]),
            "allergies": allergies,
            "emergency_contact_name": f"{rng.choice(data.SINHALA[0])[0]} {last}",
            "emergency_contact_phone": f"077{rng.randrange(1000000, 9999999)}",
        }
        key = f"nic:{nic}" if nic else f"child:{first}/{last}/{born.isoformat()}"
        return PatientPlan(key=key, data=patient)

    # --- running one patient's scenario ---------------------------------------------------

    def find_or_register(self, plan):
        data_ = plan.data
        if data_["nic"]:
            patient = Patient.objects.filter(nic__iexact=data_["nic"]).first()
        else:
            patient = Patient.objects.filter(
                first_name=data_["first_name"],
                last_name=data_["last_name"],
                date_of_birth=data_["date_of_birth"],
            ).first()
        if patient is not None:
            self.report.add("Patients", created=False)
            return patient, False
        patient = patient_services.register_patient(
            data=data_, acting_user=self.actor(Role.RECEPTIONIST)
        )
        self.report.add("Patients")
        return patient, True

    def seed_patient(self, plan):
        patient, created = self.find_or_register(plan)
        has_history = (
            Appointment.objects.filter(patient=patient).exists()
            or Admission.objects.filter(patient=patient).exists()
            or LabOrder.objects.filter(patient=patient).exists()
        )
        if has_history:
            self.report.add("Patient scenarios", created=False)
            return
        floors = {
            "charge": Charge.objects.aggregate(top=Max("pk"))["top"] or 0,
            "movement": StockMovement.objects.aggregate(top=Max("pk"))["top"] or 0,
        }
        rng = self.scenario_rng(plan.key)
        self.first_event = None  # the patient's earliest event in this scenario
        for visit in sorted(plan.visits, key=lambda v: (v.day, v.start)):
            self.run_visit(patient, visit, rng)
        if plan.admission:
            self.run_admission(patient, plan.admission, rng)
        if plan.walk_in_lab:
            self.walk_in_lab(patient, rng)
        if created:
            self.stamps.created(patient, self.registration_time(rng))
        # Rows whose times follow from the events above (written after the stamps).
        self.stamps.apply()
        self.backdate_charges(patient, floors["charge"])
        self.backdate_dispense_movements(patient, floors["movement"])
        self.report.add("Patient scenarios")

    def event(self, moment):
        """Remember the patient's earliest event (registration must come before it)."""
        if self.first_event is None or moment < self.first_event:
            self.first_event = moment

    def registration_time(self, rng):
        """About 80% registered 2-24 months ago, the rest shortly before their first
        visit; always before their first event."""
        if rng.random() < 0.8 or self.first_event is None:
            moment = self.days_ago(rng.randrange(61, 730), rng.randrange(8, 17), rng.randrange(60))
        else:
            moment = self.first_event - timedelta(hours=rng.randrange(1, 72))
        if self.first_event is not None:
            moment = min(moment, self.first_event - timedelta(hours=1))
        return self.clamp(moment)

    def backdate_charges(self, patient, floor):
        """A charge is created when its source happens: the completed consultation, the lab
        order, the dispense or the end of a bed period."""
        sources = {
            "appointment": lambda pk: Appointment.objects.get(pk=pk).completed_at,
            "lab_order_item": lambda pk: (
                LabOrderItem.objects.select_related("order").get(pk=pk).order.created_at
            ),
            "dispense_item": lambda pk: (
                DispenseItem.objects.select_related("dispense").get(pk=pk).dispense.dispensed_at
            ),
            "bed_assignment": lambda pk: BedAssignment.objects.get(pk=pk).ended_at,
        }
        for charge in Charge.objects.filter(patient=patient, pk__gt=floor):
            source = sources.get(charge.source_type)
            if source:
                backdate(Charge, charge.pk, created_at=source(charge.source_id))

    def backdate_dispense_movements(self, patient, floor):
        """Stock leaves the shelf at the dispense time."""
        movements = StockMovement.objects.filter(
            pk__gt=floor, dispense_item__dispense__patient=patient
        ).select_related("dispense_item__dispense")
        for movement in movements:
            backdate(
                StockMovement, movement.pk, created_at=movement.dispense_item.dispense.dispensed_at
            )

    def booking_time(self, start, future, rng):
        """When the appointment was booked: 1-10 days ahead, a few same-day walk-ins;
        upcoming appointments were booked in the last few days."""
        if future:
            moment = self.now - timedelta(hours=rng.randrange(2, 96))
        elif rng.random() < 0.1:
            moment = start - timedelta(minutes=rng.randrange(60, 180))
        else:
            day = start.date() - timedelta(days=rng.randint(1, 10))
            moment = at(day, rng.randrange(8, 18), rng.randrange(60))
        return self.clamp(moment)

    def on_day(self, moment, day):
        """Clamp to the run time, but stay on the appointment's day."""
        return max(self.clamp(moment), at(day, 0, 1))

    def run_visit(self, patient, visit, rng):
        doctor = self.doctors.get(visit.doctor_username)
        if doctor is None:
            return
        receptionist = self.actor(Role.RECEPTIONIST)
        diagnosis = data.DIAGNOSES[visit.diagnosis]
        future = visit.day > self.today
        start = at(visit.day, visit.start.hour, visit.start.minute)
        booked_at = self.booking_time(start, future, rng)
        # Only free slots are booked (the plan avoids clashes; this double-checks).
        slots = [s for s, _ in available_slots(doctor, visit.day, now=booked_at)]
        if visit.start not in slots:
            self.report.warn(
                f"{visit.day} {visit.start:%H:%M} with {doctor} was not free; skipped."
            )
            return
        appointment = appointment_services.book_appointment(
            patient=patient,
            doctor=doctor,
            date=visit.day,
            start_time=visit.start,
            reason=data.COMPLAINTS[diagnosis[0]],
            acting_user=receptionist,
            now=booked_at,
        )
        self.stamps.created(appointment, booked_at)
        self.event(booked_at)
        label = (
            "Appointments (future)"
            if future
            else ("Appointments (today)" if visit.day == self.today else "Appointments (past)")
        )
        self.report.add(label)

        if visit.outcome == "CANCELLED":
            gap = (start - booked_at) * rng.uniform(0.3, 0.9)
            cancelled = self.clamp(booked_at + gap)
            appointment_services.cancel_appointment(
                appointment,
                reason=rng.choice(
                    [
                        "Patient called to cancel",
                        "Doctor unavailable",
                        "Patient travelling",
                        "Rescheduling at a later date",
                    ]
                ),
                acting_user=receptionist,
                now=cancelled,
            )
            self.stamps.touched(appointment, cancelled)
            return
        if visit.outcome == "NO_SHOW":
            marked = self.clamp(shift(start, 60))
            appointment_services.mark_no_show(appointment, acting_user=receptionist, now=marked)
            self.stamps.touched(appointment, marked)
            return
        if visit.outcome == "BOOKED":
            return

        # Checked in shortly before the slot; vitals by the nurse (or the doctor when
        # there is no demo nurse) a few minutes later.
        checked_in = self.on_day(shift(start, -rng.randrange(5, 25)), visit.day)
        appointment_services.check_in_appointment(
            appointment, acting_user=receptionist, now=checked_in
        )
        self.stamps.touched(appointment, checked_in)
        nurse = self.user("demo.nurse") or doctor.user
        record_services.record_vitals(
            appointment=appointment,
            data=vitals(rng, patient),
            acting_user=nurse,
            now=self.on_day(shift(checked_in, rng.randrange(3, 10)), visit.day),
        )
        if visit.outcome == "CHECKED_IN":
            return
        self.consultation(patient, appointment, doctor, visit, diagnosis, start, rng)

    def consultation(self, patient, appointment, doctor, visit, diagnosis, start, rng):
        code, description, medicines, tests = diagnosis
        vitals_at = appointment.vitals.recorded_at
        began = max(start, shift(vitals_at, 2))
        record = record_services.start_consultation(
            appointment=appointment, acting_user=doctor.user, now=began
        )
        self.stamps.created(record, began)
        record_services.update_record(
            record,
            data={
                "clinical_notes": "History taken. No red flag symptoms.",
                "examination_findings": rng.choice(
                    [
                        "Alert, afebrile. Chest clear. Abdomen soft.",
                        "Mild pharyngeal congestion. Lungs clear.",
                        "BP as recorded. Heart sounds normal.",
                        "Tender lower lumbar region; neurology intact.",
                    ]
                ),
                "treatment_plan": "Medicines as prescribed. Return if symptoms worsen.",
                "follow_up_date": visit.day + timedelta(days=14)
                if code in ("I10", "E11.9")
                else None,
            },
            acting_user=doctor.user,
        )
        record_services.add_diagnosis(
            record,
            data={
                "description": description,
                "icd10_code": code,
                "diagnosis_type": "PRIMARY",
                "notes": "",
            },
            acting_user=doctor.user,
        )
        if rng.random() < 0.3:
            extra = data.DIAGNOSES[(data.DIAGNOSES.index(diagnosis) + 1) % len(data.DIAGNOSES)]
            record_services.add_diagnosis(
                record,
                data={
                    "description": extra[1],
                    "icd10_code": extra[0],
                    "diagnosis_type": "SECONDARY",
                    "notes": "",
                },
                acting_user=doctor.user,
            )
        count = len(medicines) if visit.allergy_override else rng.randint(1, 3)
        for name, dose, frequency, days, quantity in medicines[:count]:
            medicine = self.medicines.get(name)
            if medicine is None:
                continue
            item = {
                "medicine": medicine,
                "dose": dose,
                "frequency": frequency,
                "route": "ORAL",
                "duration_days": days,
                "quantity": quantity,
                "instructions": "After meals",
            }
            try:
                record_services.add_prescription_item(
                    record,
                    data=item,
                    allergy_override=visit.allergy_override,
                    acting_user=doctor.user,
                )
            except Exception as error:  # noqa: BLE001
                if getattr(error, "code", None) != "allergy":
                    raise
                # A recorded allergy: a careful doctor leaves that medicine out.
        last_event = began
        if visit.lab:
            ordered = self.lab_order(record, doctor, tests, visit.lab, shift(began, 5), rng)
            if ordered:
                last_event = max(last_event, ordered)
        finalized = shift(began, rng.randrange(10, 20))
        record_services.finalize_record(record, acting_user=doctor.user, now=finalized)
        self.stamps.touched(record, finalized)
        self.stamps.touched(appointment, finalized)
        last_event = max(last_event, finalized)

        prescription = Prescription.objects.filter(record=record).first()
        if prescription:
            self.stamps.created(prescription, shift(began, 3))
            self.stamps.touched(prescription, finalized)
            if visit.dispense != "NONE" and self.user("demo.pharmacist"):
                dispensed = self.clamp(shift(finalized, rng.randrange(10, 90)))
                if self.dispense(prescription, visit, dispensed):
                    self.stamps.touched(prescription, dispensed)
                    last_event = max(last_event, dispensed)
        self.report.add("Consultations")
        self.bill(patient, visit.billing, last_event, rng)

    def dispense(self, prescription, visit, when):
        """Hand over the prescription (or part of it), never more than is in stock then.
        A medicine that already existed with little stock (not the seed's batches) just
        makes this a partial dispense instead of an error. Returns True if anything was
        handed over."""
        progress = item_progress(prescription, timezone.localdate(when))
        quantities = {}
        for index, row in enumerate(progress):
            wanted = row.item.quantity
            if visit.dispense == "PARTIAL" and index == 0:
                wanted = max(1, wanted // 2)
            quantity = min(wanted, row.usable_stock)
            if quantity > 0:
                quantities[row.item.pk] = quantity
        if not quantities:
            return False
        partial = any(quantities.get(row.item.pk, 0) < row.item.quantity for row in progress)
        dispensing.dispense_prescription(
            prescription=prescription,
            quantities=quantities,
            notes="Balance to collect later." if partial else "",
            acting_user=self.user("demo.pharmacist"),
            now=when,
        )
        self.report.add("Dispenses")
        return True

    def lab_order(self, record, doctor, tests, outcome, ordered, rng):
        """Order tests during the consultation. Returns the order time (or None)."""
        test_ids = [self.lab_tests[code].pk for code in tests if code in self.lab_tests]
        if not test_ids:
            return None
        order = lab_services.order_tests_for_record(
            record=record,
            test_ids=test_ids,
            priority="URGENT" if outcome == "URGENT" else "ROUTINE",
            clinical_notes="Please process today." if outcome == "URGENT" else "",
            acting_user=doctor.user,
        )
        self.stamps.created(order, ordered)
        self.report.add("Lab orders")
        self.lab_workflow(order, outcome, ordered, rng)
        return ordered

    def lab_workflow(self, order, outcome, ordered, rng):
        """Collection 10-60 minutes after the order, results entered between collection
        and release, release within the tests' turnaround (every 4th released order
        goes over it, so the laboratory report has cases over target)."""
        lab = self.actor(Role.LAB_STAFF)
        if outcome == "CANCELLED":
            cancelled = self.clamp(shift(ordered, rng.randrange(30, 120)))
            lab_services.cancel_order(
                order,
                reason="Sample haemolysed; patient to repeat.",
                acting_user=lab,
                now=cancelled,
            )
            self.stamps.touched(order, cancelled)
            return
        if outcome in ("REQUESTED", "URGENT"):
            return
        turnaround = max(item.test.turnaround_hours for item in order.items.select_related("test"))
        collected = self.clamp(shift(ordered, rng.randrange(10, 61)))
        if outcome == "COMPLETED":
            over = self.released_lab_orders % 4 == 0
            self.released_lab_orders += 1
            hours = turnaround * (rng.uniform(1.2, 1.8) if over else rng.uniform(0.4, 0.9))
            # At least a few minutes after collection, and never after the run itself.
            released = self.clamp(max(ordered + timedelta(hours=hours), shift(collected, 4)))
        else:
            released = None
        latest = released or self.clamp(shift(collected, rng.randrange(30, 180)))
        entered = collected + (latest - collected) * 0.6
        lab_services.collect_sample(order, notes="", acting_user=lab, now=collected)
        for item in order.items.select_related("test").prefetch_related("test__parameters"):
            values = {p.pk: result_value(p, rng) for p in item.test.parameters.all()}
            lab_services.save_results(
                order,
                item=item,
                values=values,
                comment=rng.choice(["", "", "Repeat in 3 months.", "Correlate clinically."]),
                acting_user=lab,
                now=entered,
            )
        self.stamps.touched(order, entered)
        if released:
            lab_services.release_results(order, acting_user=lab, now=released)
            self.stamps.touched(order, released)

    def walk_in_lab(self, patient, rng):
        """A walk-in with an outside referral, four days ago in the morning."""
        test_ids = [self.lab_tests[code].pk for code in ("FBC", "FBS") if code in self.lab_tests]
        if not test_ids:
            return
        ordered = self.clamp(self.days_ago(4, 9, 30))
        order = lab_services.create_walk_in_order(
            patient=patient,
            test_ids=test_ids,
            referred_by="Dr. K. Sivapalan (Private clinic, Wellawatte)",
            priority="ROUTINE",
            clinical_notes="Annual check-up",
            acting_user=self.actor(Role.LAB_STAFF),
        )
        self.stamps.created(order, ordered)
        self.event(ordered)
        self.report.add("Lab orders")
        self.lab_workflow(order, "COMPLETED", ordered, rng)

    # --- billing --------------------------------------------------------------------------

    def bill(self, patient, outcome, after, rng):
        """Invoice everything still unbilled for the patient, a little after the last
        charge (`after`), then the planned outcome: PAID_CASH, PAID_CARD, PARTIAL,
        OUTSTANDING(_OLD), DRAFT, DISCOUNT, VOID, VOIDED_PAYMENT or NONE (left unbilled)."""
        if outcome == "NONE":
            return
        cashier = self.actor(Role.RECEPTIONIST)
        created = self.clamp(shift(after, rng.randrange(5, 60)))
        issued = self.clamp(shift(created, rng.randrange(2, 10)))
        charge_ids = list(
            Charge.objects.filter(
                patient=patient, invoice__isnull=True, is_voided=False
            ).values_list("pk", flat=True)
        )
        invoice = billing_services.create_invoice(
            patient=patient, charge_ids=charge_ids, acting_user=cashier
        )
        self.stamps.created(invoice, created)
        self.report.add("Invoices")
        if outcome == "DRAFT":
            return
        if outcome == "DISCOUNT":
            billing_services.set_discount(
                invoice,
                amount=min(Decimal("1000.00"), invoice.subtotal / 4),
                reason="Senior citizen discount",
                acting_user=self.actor(Role.ACCOUNTANT),
                now=self.clamp(shift(created, 1)),
            )
        billing_services.issue_invoice(invoice, acting_user=cashier, now=issued)
        self.stamps.touched(invoice, issued)
        invoice = Invoice.objects.get(pk=invoice.pk)
        total = invoice.total
        admin = self.user("demo.admin")
        if outcome == "VOID":
            voided = self.clamp(shift(issued, rng.randrange(20, 120)))
            billing_services.void_invoice(
                invoice, reason="Issued to the wrong patient.", acting_user=admin, now=voided
            )
            self.stamps.touched(invoice, voided)
            return
        if outcome in ("OUTSTANDING", "OUTSTANDING_OLD"):
            return
        method, reference = ("CASH", "")
        if outcome == "PAID_CARD":
            method, reference = ("CARD", f"VISA-{rng.randrange(100000, 999999)}")
        amount = total if outcome != "PARTIAL" else billing_services.money(total / 2)
        if amount <= 0:
            return
        paid = self.clamp(shift(issued, rng.randrange(1, 15)))
        payment = billing_services.record_payment(
            invoice=invoice,
            amount=amount,
            method=method,
            reference=reference,
            acting_user=cashier,
            now=paid,
        )
        self.stamps.touched(invoice, paid)
        self.report.add("Payments")
        if outcome == "VOIDED_PAYMENT":
            voided = self.clamp(shift(paid, rng.randrange(30, 180)))
            billing_services.void_payment(
                payment,
                reason="Card payment declined by the bank.",
                acting_user=admin,
                now=voided,
            )
            self.stamps.touched(invoice, voided)
            self.report.add("Voided payments")

    # --- admissions -----------------------------------------------------------------------

    def run_admission(self, patient, plan, rng):
        doctor = self.doctors.get(plan["doctor"])
        if doctor is None or not self.beds:
            return
        nurse = self.user("demo.nurse") or doctor.user
        reason, summary = plan["reason"]
        ward_name = data.WARDS[plan["ward"]][0]
        ward_beds = [b for b in self.beds if b.ward.name == ward_name]
        start = plan["bed"] % len(ward_beds)
        bed = next(
            (b for b in ward_beds[start:] + ward_beds[:start] if not is_occupied(b)),
            None,
        )
        if bed is None:
            self.report.warn(f"No free bed in {ward_name}; an admission was skipped.")
            return
        admitted = plan["admitted"]
        discharged = plan["discharged"]
        current = discharged is None
        admission = admission_services.admit_patient(
            patient=patient,
            bed=bed,
            admitting_doctor=doctor,
            reason=reason,
            source=rng.choice(["EMERGENCY", "REFERRAL", "DIRECT"]),
            admitted_at=admitted,
            acting_user=nurse,
            now=admitted,
        )
        self.stamps.created(admission, admitted)
        self.event(admitted)
        self.report.add("Admissions (current)" if current else "Admissions (discharged)")
        # Notes between admission and discharge (or now, for current patients).
        for offset, note_type, text, author in (
            (4, "NURSING", "Settled in the ward. Observations stable.", nurse),
            (22, "DOCTOR_ROUND", "Reviewed on the round. Plan continued.", doctor.user),
        ):
            written = max(self.clamp(admitted + timedelta(hours=offset)), shift(admitted, 1))
            admission_services.add_progress_note(
                admission, note_type=note_type, text=text, acting_user=author, now=written
            )
            self.stamps.touched(admission, written)
        if plan["transfer"]:
            free = [b for b in self.beds if b.ward_id != bed.ward_id and not is_occupied(b)]
            if free:
                # 6 hours after admission: always in the past (admitted at least a day ago).
                moved = admitted + timedelta(hours=6)
                admission_services.transfer_bed(
                    admission,
                    new_bed=free[0],
                    transferred_at=moved,
                    acting_user=nurse,
                    now=self.now,
                )
                self.stamps.touched(admission, moved)
                self.report.add("Bed transfers")
        if current:
            return
        admission_services.discharge_patient(
            admission,
            discharge_type="HOME",
            discharge_summary=summary,
            discharged_at=discharged,
            acting_user=doctor.user,
            now=discharged,
        )
        self.stamps.touched(admission, discharged)
        self.bill(patient, plan["invoice"], discharged, rng)


# --- small generators -----------------------------------------------------------------


def is_occupied(bed):
    return bed.assignments.filter(ended_at__isnull=True).exists()


def make_nic(born, gender, serial, *, old_format=False):
    """A NIC in the valid shape for this birth date (day of year + 500 for women).
    The serial keeps every demo NIC unique; none belongs to a real person."""
    day = born.timetuple().tm_yday + (500 if gender == "FEMALE" else 0)
    if old_format and born.year < 2000:
        return f"{born.year % 100:02d}{day:03d}{serial % 10000:04d}V"
    return f"{born.year}{day:03d}{serial % 100000:05d}"


def vitals(rng, patient):
    child = (timezone.localdate() - patient.date_of_birth).days < 365 * 14
    return {
        "bp_systolic": rng.randint(105, 150) if not child else rng.randint(90, 110),
        "bp_diastolic": rng.randint(65, 95) if not child else rng.randint(55, 70),
        "pulse_bpm": rng.randint(64, 104),
        "respiratory_rate": rng.randint(14, 22),
        "spo2_percent": rng.randint(95, 100),
        "temperature_c": Decimal(f"{rng.uniform(36.4, 38.6):.1f}"),
        "weight_kg": Decimal(f"{rng.uniform(12, 30) if child else rng.uniform(45, 92):.1f}"),
        "height_cm": Decimal(f"{rng.uniform(85, 140) if child else rng.uniform(148, 182):.1f}"),
    }


def result_value(parameter, rng):
    """A plausible result: mostly in range, sometimes high or low (flags come from the
    service's own compute_flag)."""
    if parameter.result_type == LabTestParameter.ResultType.TEXT:
        return (
            parameter.ref_text
            if rng.random() < 0.75
            else rng.choice(["Trace", "+", "E. coli > 10^5 CFU/ml"])
        )
    low = parameter.ref_low if parameter.ref_low is not None else Decimal("0")
    high = parameter.ref_high if parameter.ref_high is not None else low * 2 + 10
    span = high - low
    roll = rng.random()
    if roll < 0.15 and parameter.ref_low is not None:
        value = low - span * Decimal(rng.uniform(0.05, 0.3))
    elif roll < 0.35:
        value = high + span * Decimal(rng.uniform(0.05, 0.5))
    else:
        value = low + span * Decimal(rng.uniform(0.1, 0.9))
    return f"{max(value, Decimal('0')):.1f}"
