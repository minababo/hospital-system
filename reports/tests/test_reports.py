import csv
import io
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from admissions.models import Admission
from billing.models import Payment
from laboratory.models import LabOrder, LabResult
from patients.models import Patient
from pharmacy.models import Dispense, DispenseItem, StockMovement
from reports import report_selectors
from staff.models import Attendance

pytestmark = pytest.mark.django_db


def colombo(*args):
    return timezone.make_aware(datetime(*args))


OCT1, OCT31 = date(2026, 10, 1), date(2026, 10, 31)
MAR1, MAR31 = date(2026, 3, 1), date(2026, 3, 31)


# --- Patients -----------------------------------------------------------------------------


def test_patient_report(make_patient, make_admission):
    # March 2026, well before the real clock, so patients created "now" by the
    # admission fixture fall outside the range.
    def registered(at, **kwargs):
        patient = make_patient(**kwargs)
        Patient.objects.filter(pk=patient.pk).update(created_at=at)
        return patient

    registered(colombo(2026, 3, 1, 9), gender="FEMALE", date_of_birth=date(2020, 1, 1))  # 6
    registered(colombo(2026, 3, 1, 23, 50), gender="MALE", date_of_birth=date(2010, 1, 1))  # 16
    registered(colombo(2026, 3, 2, 8), gender="MALE", date_of_birth=date(1950, 1, 1))  # 76
    registered(colombo(2026, 2, 28, 23, 0))  # February: outside

    stay = make_admission(admitted_at=colombo(2026, 3, 3, 10))
    Admission.objects.filter(pk=stay.pk).update(
        status="DISCHARGED",
        discharged_at=colombo(2026, 3, 6, 9),
        discharge_type="HOME",
    )  # 3 nights
    short = make_admission(admitted_at=colombo(2026, 3, 7, 9))
    Admission.objects.filter(pk=short.pk).update(
        status="DISCHARGED", discharged_at=colombo(2026, 3, 7, 15), discharge_type="HOME"
    )  # same day: counts as 1

    data = report_selectors.patient_report(MAR1, MAR31)

    assert data["total"] == 3
    assert [(row["day"], row["count"]) for row in data["per_day"]] == [
        (date(2026, 3, 1), 2),
        (date(2026, 3, 2), 1),
    ]
    assert {row["label"]: row["count"] for row in data["by_gender"]} == {"Female": 1, "Male": 2}
    assert {row["label"]: row["count"] for row in data["by_age"]} == {
        "0–12": 1,
        "13–17": 1,
        "18–39": 0,
        "40–59": 0,
        "60+": 1,
    }
    assert (data["admissions"], data["discharges"]) == (2, 2)
    assert data["average_stay"] == Decimal("2.0")


# --- Appointments ---------------------------------------------------------------------


def test_appointment_report_and_doctor_scope(make_doctor, make_appointment, admin_user_obj):
    mine, other = make_doctor(), make_doctor()
    day = date(2026, 10, 5)
    for hour, status in enumerate(["COMPLETED", "COMPLETED", "CANCELLED", "NO_SHOW"], start=8):
        make_appointment(doctor=mine, date=day, status=status, start_time=time(hour))
    make_appointment(doctor=other, date=day, status="BOOKED", start_time=time(8))
    make_appointment(doctor=mine, date=date(2026, 11, 2))  # outside

    admin_view = report_selectors.appointment_report(admin_user_obj, OCT1, OCT31)
    doctor_view = report_selectors.appointment_report(mine.user, OCT1, OCT31)

    assert admin_view["total"] == 5
    assert admin_view["cancellation_rate"] == Decimal("20.0")
    assert admin_view["no_show_rate"] == Decimal("20.0")
    assert len(admin_view["by_doctor"]) == 2
    assert doctor_view["total"] == 4
    assert [row["doctor"] for row in doctor_view["by_doctor"]] == [str(mine)]
    assert doctor_view["by_doctor"][0]["completed"] == 2
    assert doctor_view["cancellation_rate"] == Decimal("25.0")


# --- Revenue ------------------------------------------------------------------------------


def test_revenue_report(make_invoice, make_user):
    cashier = make_user()
    today = date(2026, 10, 31)
    invoice = make_invoice(amounts=["5000"], status="ISSUED", issued_at=colombo(2026, 10, 5, 10))
    for amount, method, at, voided in [
        ("1000", "CASH", colombo(2026, 10, 5, 23, 30), False),
        ("500", "CARD", colombo(2026, 10, 6, 0, 10), False),
        ("700", "CASH", colombo(2026, 10, 6, 9), True),
        ("100", "CASH", colombo(2026, 9, 30, 23, 0), False),  # September
    ]:
        Payment.objects.create(
            invoice=invoice,
            amount=Decimal(amount),
            method=method,
            reference="R1" if method != "CASH" else "",
            received_by=cashier,
            received_at=at,
            is_voided=voided,
        )
    make_invoice(
        amounts=["300"],
        status="PAID",
        issued_at=colombo(2026, 10, 9, 9),
        discount=Decimal("50"),
        discount_reason="Staff",
    )
    make_invoice(
        amounts=["300"],
        status="VOID",
        issued_at=colombo(2026, 10, 9, 9),
        discount=Decimal("80"),
        discount_reason="x",
    )
    # Aging boundaries as of 31 Oct.
    for days in (30, 31, 60, 61):
        make_invoice(
            amounts=["100"],
            status="ISSUED",
            issued_at=colombo(2026, 10, 31, 9) - timedelta(days=days),
        )

    data = report_selectors.revenue_report(OCT1, OCT31, today)

    assert data["received_total"] == Decimal("1500.00")
    assert [(row["day"], row["total"]) for row in data["per_day"]] == [
        (date(2026, 10, 5), Decimal("1000.00")),
        (date(2026, 10, 6), Decimal("500.00")),
    ]
    assert {row["label"]: row["total"] for row in data["by_method"]}["Card"] == Decimal("500.00")
    assert data["discounts"] == Decimal("50.00")
    buckets = {row["label"]: row["total"] for row in data["aging"]}
    assert buckets["0–30 days"] == Decimal("100.00") + (Decimal("5000") - Decimal("1500") - 100)
    assert buckets["31–60 days"] == Decimal("200.00")
    assert buckets["61+ days"] == Decimal("100.00")
    ages = sorted(row["age"] for row in data["outstanding"])
    assert ages == [26, 30, 31, 60, 61]


# --- Pharmacy -----------------------------------------------------------------------------


def test_pharmacy_report(make_medicine, make_batch, make_issued_prescription, make_user):
    today = timezone.localdate()
    pharmacist = make_user(role=Role.PHARMACIST)
    amox = make_medicine(name="Amoxicillin", unit_price=Decimal("30"))
    para = make_medicine(name="Paracetamol", unit_price=Decimal("5"))
    good = make_batch(medicine=amox, qty=40, expiry=today + timedelta(days=100))
    make_batch(medicine=amox, qty=10, expiry=today - timedelta(days=1))  # expired: no value
    prescription = make_issued_prescription(items=[(amox, 10), (para, 20)])
    dispense = Dispense.objects.create(
        prescription=prescription,
        patient=prescription.patient,
        dispensed_by=pharmacist,
        dispensed_at=timezone.now(),
    )
    for item, qty, price in [
        (prescription.items.get(medicine=amox), 4, "25.00"),  # price at the time
        (prescription.items.get(medicine=para), 20, "5.00"),
    ]:
        DispenseItem.objects.create(
            dispense=dispense, prescription_item=item, quantity=qty, unit_price=Decimal(price)
        )
    StockMovement.objects.create(
        batch=good, movement_type="ADJUSTMENT", quantity=-2, reason="Broken", created_by=pharmacist
    )

    data = report_selectors.pharmacy_report(today, today, today)

    assert [(r["medicine"], r["quantity"], r["value"]) for r in data["dispensed"]] == [
        ("Amoxicillin 500 mg", 4, Decimal("100.00")),
        ("Paracetamol 500 mg", 20, Decimal("100.00")),
    ]
    assert (data["dispensed_quantity"], data["dispensed_value"]) == (24, Decimal("200.00"))
    assert [(m.quantity, m.reason) for m in data["movements"]] == [(-2, "Broken")]
    assert data["valuation"] == [
        {
            "medicine": "Amoxicillin 500 mg",
            "units": 40,
            "unit_price": Decimal("30.00"),
            "value": Decimal("1200.00"),
        }
    ]
    assert data["valuation_total"] == Decimal("1200.00")


# --- Laboratory --------------------------------------------------------------------------


def test_lab_report(make_lab_order, make_lab_test):
    fbc = make_lab_test(code="FBC", price=Decimal("1200"), turnaround_hours=24)
    created = colombo(2026, 10, 5, 8)

    def order(status, hours=None, flags=()):
        o = make_lab_order(tests=[fbc], status=status)
        update = {"created_at": created}
        if hours is not None:
            update["released_at"] = created + timedelta(hours=hours)
        LabOrder.objects.filter(pk=o.pk).update(**update)
        item = o.items.get()
        for flag in flags:
            LabResult.objects.create(
                item=item,
                parameter=fbc.parameters.get(),
                value_numeric=10,
                flag=flag,
                entered_at=created,
            )
        return o

    order("COMPLETED", hours=12, flags=["N"])
    order("COMPLETED", hours=30, flags=["H"])
    order("REQUESTED")
    order("CANCELLED")

    data = report_selectors.lab_report(OCT1, OCT31)
    [test] = data["per_test"]

    assert data["orders_total"] == 4
    assert {r["label"]: r["count"] for r in data["by_status"]}["Completed"] == 2
    assert (test["ordered"], test["revenue"]) == (3, Decimal("3600.00"))  # cancelled excluded
    assert (test["completed"], test["average_hours"], test["over_target"]) == (
        2,
        Decimal("21.0"),
        1,
    )
    assert (test["numeric"], test["abnormal"], test["abnormal_rate"]) == (2, 1, Decimal("50.0"))


# --- Staff -------------------------------------------------------------------------------


def test_staff_report(make_employee, make_department, make_leave, admin_user_obj):
    ward = make_department(name="Ward")
    nurse = make_employee(department=ward, category="NURSING", date_joined=date(2025, 1, 1))
    make_employee(
        department=ward, status="RESIGNED", end_date=date(2026, 9, 1), date_joined=date(2025, 1, 1)
    )
    make_leave(employee=nurse, start=date(2026, 9, 29), end=date(2026, 10, 3))  # 3 days in Oct
    Attendance.objects.create(
        employee=nurse, date=date(2026, 10, 6), status="PRESENT", recorded_by=admin_user_obj
    )

    data = report_selectors.staff_report(OCT1, OCT31)

    assert [(r["label"], r["count"]) for r in data["by_department"]] == [("Ward", 1)]
    assert {r["label"]: r["count"] for r in data["leave"]}["Annual"] == 3
    assert data["month"] == OCT1
    [row] = data["attendance"]
    assert (row.present, row.leave_days) == (1, 3)


# --- Views: CSV, print, access ------------------------------------------------------------


def test_csv_export_escapes_malicious_names(client_for_role, make_patient):
    make_patient(first_name='=HYPERLINK("http://evil")', last_name="X")

    response = client_for_role(Role.RECEPTIONIST).get(
        reverse("reports:patients"), {"export": "csv"}
    )

    assert response["Content-Type"].startswith("text/csv")
    assert response["Content-Disposition"].startswith('attachment; filename="patients-report-')
    rows = list(csv.reader(io.StringIO(response.content.decode())))
    assert rows[0][:2] == ["MRN", "Name"]
    assert rows[1][1].startswith("'=HYPERLINK")


def test_print_variant_uses_print_layout(client_for_role):
    response = client_for_role(Role.ADMIN).get(reverse("reports:staff"), {"print": "1"})

    assert "print-button" in response.content.decode()
    assert 'id="sidebar"' not in response.content.decode()


def test_invalid_range_falls_back_with_message(client_for_role):
    response = client_for_role(Role.ADMIN).get(
        reverse("reports:patients"), {"date_from": "2026-10-10", "date_to": "2026-10-01"}
    )

    assert response.status_code == 200
    assert "on or before" in response.content.decode()


REPORT_ROLES = {
    "reports:patients": {Role.ADMIN, Role.RECEPTIONIST},
    "reports:appointments": {Role.ADMIN, Role.RECEPTIONIST, Role.DOCTOR},
    "reports:revenue": {Role.ADMIN, Role.ACCOUNTANT},
    "reports:pharmacy": {Role.ADMIN, Role.PHARMACIST},
    "reports:laboratory": {Role.ADMIN, Role.LAB_STAFF},
    "reports:staff": {Role.ADMIN},
}
ANY_REPORT = set().union(*REPORT_ROLES.values())
VARIANTS = [{}, {"export": "csv"}, {"print": "1"}]


@pytest.mark.parametrize("params", VARIANTS, ids=["page", "csv", "print"])
@pytest.mark.parametrize("name", [*REPORT_ROLES, "reports:index"])
def test_anonymous_redirected(client, name, params):
    response = client.get(reverse(name), params)

    assert response.status_code == 302
    assert response.url.startswith(reverse("accounts:login"))


@pytest.mark.parametrize("role", list(Role))
@pytest.mark.parametrize("params", VARIANTS, ids=["page", "csv", "print"])
@pytest.mark.parametrize("name", list(REPORT_ROLES))
def test_report_rbac(client_for_role, role, name, params):
    response = client_for_role(role).get(reverse(name), params)

    assert response.status_code == (200 if role in REPORT_ROLES[name] else 403)


@pytest.mark.parametrize("role", list(Role))
def test_index_lists_only_permitted_reports(client_for_role, role):
    response = client_for_role(role).get(reverse("reports:index"))

    if role not in ANY_REPORT:
        assert response.status_code == 403
        return
    listed = {report["url"] for report in response.context["reports"]}
    allowed = {reverse(name) for name, roles in REPORT_ROLES.items() if role in roles}
    assert listed == allowed


@pytest.mark.parametrize("role", list(Role))
def test_reports_nav_link(client_for_role, role):
    labels = [
        i["label"] for i in client_for_role(role).get(reverse("dashboard")).context["nav_items"]
    ]

    assert ("Reports" in labels) == (role in ANY_REPORT)
    assert Role.NURSE not in ANY_REPORT
