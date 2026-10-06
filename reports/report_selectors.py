"""Numbers for the six reports. Each function takes the date range (and the user when
the report is scoped) and returns a dict of summaries and table rows.

Datetime columns are filtered with local_range_bounds (Sri Lankan days); DateFields
(appointment date, leave dates, expiry) are compared directly.
"""

from collections import defaultdict
from decimal import Decimal

from django.db.models import Count, F, Q, Sum
from django.db.models.functions import Coalesce, TruncDate
from django.utils import timezone

from accounts.models import Role
from admissions.models import Admission, nights_between
from appointments.models import Appointment
from appointments.models import Status as AppointmentStatus
from billing.models import ChargeType, Invoice, InvoiceStatus, Payment, PaymentMethod
from billing.selectors import MONEY, OPEN_STATUSES, ZERO, invoiced_by_charge_type, with_totals
from laboratory.models import LabOrder, LabOrderItem, LabResult, OrderStatus
from patients.models import Patient, years_between
from pharmacy.models import DispenseItem, MovementType, StockBatch, StockMovement
from pharmacy.selectors import pharmacy_alerts_summary
from reports.dates import local_range_bounds
from staff.models import LeaveType
from staff.selectors import headcount, leave_taken_between, monthly_attendance_summary

AGE_BANDS = [
    ("0–12", 0, 12),
    ("13–17", 13, 17),
    ("18–39", 18, 39),
    ("40–59", 40, 59),
    ("60+", 60, None),
]
AGING_BUCKETS = [("0–30 days", 0, 30), ("31–60 days", 31, 60), ("61+ days", 61, None)]


def percent(part, whole):
    """part as a percentage of whole, 1 decimal place; 0.0 when whole is 0."""
    if not whole:
        return Decimal("0.0")
    return (Decimal(part) * 100 / Decimal(whole)).quantize(Decimal("0.1"))


def with_bars(rows, key="count"):
    """Add `bar`, a 0–100 width for a CSS bar, relative to the row with the largest value."""
    largest = max((row[key] for row in rows), default=0)
    for row in rows:
        row["bar"] = int(row[key] * 100 / largest) if largest else 0
    return rows


def per_local_day(queryset, field):
    """[(local date, count), ...] grouping a datetime column by Sri Lankan date."""
    return list(
        queryset.annotate(day=TruncDate(field, tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
        .values_list("day", "count")
    )


def _band(age):
    for label, low, high in AGE_BANDS:
        if age >= low and (high is None or age <= high):
            return label
    return AGE_BANDS[0][0]


# --- Patients ---------------------------------------------------------------------------


def patient_report(date_from, date_to):
    start, end = local_range_bounds(date_from, date_to)
    registered = Patient.objects.filter(created_at__gte=start, created_at__lt=end)
    patients = list(registered.order_by("created_at", "pk"))

    gender_labels = dict(Patient.Gender.choices)
    by_gender = defaultdict(int)
    by_age = {label: 0 for label, _, _ in AGE_BANDS}
    rows = []
    for patient in patients:
        by_gender[patient.gender] += 1
        age = years_between(patient.date_of_birth, timezone.localtime(patient.created_at).date())
        by_age[_band(age)] += 1
        rows.append({"patient": patient, "age": age})

    admitted = Admission.objects.filter(admitted_at__gte=start, admitted_at__lt=end).count()
    discharged = list(
        Admission.objects.filter(discharged_at__gte=start, discharged_at__lt=end).only(
            "admitted_at", "discharged_at"
        )
    )
    stays = [max(nights_between(a.admitted_at, a.discharged_at), 1) for a in discharged]
    average_stay = (Decimal(sum(stays)) / len(stays)).quantize(Decimal("0.1")) if stays else None
    return {
        "total": len(patients),
        "patients": rows,  # with age at registration
        "per_day": with_bars(
            [{"day": d, "count": n} for d, n in per_local_day(registered, "created_at")]
        ),
        "by_gender": with_bars(
            [{"label": gender_labels[g], "count": n} for g, n in sorted(by_gender.items())]
        ),
        "by_age": with_bars([{"label": label, "count": n} for label, n in by_age.items()]),
        "admissions": admitted,
        "discharges": len(discharged),
        "average_stay": average_stay,
    }


# --- Appointments ---------------------------------------------------------------------


def appointment_report(user, date_from, date_to):
    """Appointments dated in the range. Doctors only see their own."""
    appointments = Appointment.objects.filter(date__range=(date_from, date_to))
    if user.role == Role.DOCTOR:
        appointments = appointments.filter(doctor__user=user)
    total = appointments.count()
    status_rows = appointments.order_by().values("status").annotate(count=Count("id"))
    by_status = {status: 0 for status in AppointmentStatus.values}
    by_status.update({row["status"]: row["count"] for row in status_rows})

    by_doctor = [
        {
            "doctor": f"Dr. {row['doctor__user__first_name']} {row['doctor__user__last_name']}",
            "department": row["doctor__department__name"],
            "count": row["count"],
            "completed": row["completed"],
            "cancelled": row["cancelled"],
            "no_show": row["no_show"],
        }
        for row in appointments.order_by()
        .values(
            "doctor",
            "doctor__user__first_name",
            "doctor__user__last_name",
            "doctor__department__name",
        )
        .annotate(
            count=Count("id"),
            completed=Count("id", filter=Q(status=AppointmentStatus.COMPLETED)),
            cancelled=Count("id", filter=Q(status=AppointmentStatus.CANCELLED)),
            no_show=Count("id", filter=Q(status=AppointmentStatus.NO_SHOW)),
        )
        .order_by("-count", "doctor__user__first_name")
    ]
    by_department = [
        {"label": row["doctor__department__name"], "count": row["count"]}
        for row in appointments.order_by()
        .values("doctor__department__name")
        .annotate(count=Count("id"))
        .order_by("-count", "doctor__department__name")
    ]
    per_day = [
        {"day": row["date"], "count": row["count"]}
        for row in appointments.order_by()
        .values("date")
        .annotate(count=Count("id"))
        .order_by("date")
    ]
    return {
        "total": total,
        "by_status": with_bars(
            [
                {"label": AppointmentStatus(status).label, "count": n}
                for status, n in by_status.items()
            ]
        ),
        "status_counts": by_status,
        "cancellation_rate": percent(by_status[AppointmentStatus.CANCELLED], total),
        "no_show_rate": percent(by_status[AppointmentStatus.NO_SHOW], total),
        "by_doctor": with_bars(by_doctor),
        "by_department": with_bars(by_department),
        "per_day": with_bars(per_day),
    }


# --- Revenue ----------------------------------------------------------------------------


def _bucket(age_days):
    for label, low, high in AGING_BUCKETS:
        if age_days >= low and (high is None or age_days <= high):
            return label
    return AGING_BUCKETS[0][0]


def revenue_report(date_from, date_to, today):
    """Revenue = payments received (not voided) in the range. Invoiced amounts are
    shown for reference. Outstanding invoices are as of today, not range-filtered."""
    start, end = local_range_bounds(date_from, date_to)
    payments = Payment.objects.filter(is_voided=False, received_at__gte=start, received_at__lt=end)
    received_total = payments.aggregate(total=Coalesce(Sum("amount"), ZERO, output_field=MONEY))[
        "total"
    ]
    per_day = list(
        payments.annotate(day=TruncDate("received_at", tzinfo=timezone.get_current_timezone()))
        .values("day")
        .annotate(count=Count("id"), total=Sum("amount"))
        .order_by("day")
    )
    method_rows = {
        row["method"]: row
        for row in payments.order_by()
        .values("method")
        .annotate(count=Count("id"), total=Sum("amount"))
    }
    by_method = [
        {
            "label": label,
            "count": method_rows.get(value, {}).get("count", 0),
            "total": method_rows.get(value, {}).get("total") or Decimal("0.00"),
        }
        for value, label in PaymentMethod.choices
    ]
    charge_labels = dict(ChargeType.choices)
    invoiced = [
        {"label": charge_labels[kind], "total": total}
        for kind, total in invoiced_by_charge_type(date_from, date_to).items()
    ]
    discounts = (
        Invoice.objects.filter(issued_at__gte=start, issued_at__lt=end)
        .exclude(status=InvoiceStatus.VOID)
        .aggregate(total=Coalesce(Sum("discount"), ZERO, output_field=MONEY))["total"]
    )

    outstanding = []
    buckets = {label: Decimal("0.00") for label, _, _ in AGING_BUCKETS}
    open_invoices = with_totals(Invoice.objects.filter(status__in=OPEN_STATUSES)).select_related(
        "patient"
    )
    for invoice in open_invoices.order_by("issued_at", "pk"):
        age = (today - timezone.localtime(invoice.issued_at).date()).days
        bucket = _bucket(age)
        buckets[bucket] += invoice.balance_amount
        outstanding.append({"invoice": invoice, "age": age, "bucket": bucket})
    return {
        "received_total": received_total,
        "payments_count": payments.count(),
        "per_day": with_bars(per_day, key="total"),
        "by_method": with_bars(by_method, key="total"),
        "invoiced": with_bars(invoiced, key="total"),
        "invoiced_total": sum((row["total"] for row in invoiced), Decimal("0.00")),
        "discounts": discounts,
        "outstanding": outstanding,
        "aging": [{"label": label, "total": total} for label, total in buckets.items()],
        "outstanding_total": sum(buckets.values(), Decimal("0.00")),
    }


# --- Pharmacy -----------------------------------------------------------------------------

MEDICINE_FIELDS = ("name", "strength", "form")


def _medicine_label(row, prefix):
    return f"{row[prefix + 'name']} {row[prefix + 'strength']}"


def pharmacy_report(date_from, date_to, today):
    start, end = local_range_bounds(date_from, date_to)
    items = DispenseItem.objects.filter(
        dispense__dispensed_at__gte=start, dispense__dispensed_at__lt=end
    )
    line_value = Sum(F("quantity") * F("unit_price"), output_field=MONEY)
    prefix = "prescription_item__medicine__"
    dispensed = [
        {
            "medicine": _medicine_label(row, prefix),
            "quantity": row["units"],
            "value": row["value"],
        }
        for row in items.order_by()
        .values(*(prefix + field for field in MEDICINE_FIELDS), prefix + "id")
        # Alias "units", not "quantity": a "quantity" alias would shadow the column
        # that line_value multiplies.
        .annotate(units=Sum("quantity"), value=line_value)
        .order_by("-value", prefix + "name")
    ]
    dispensed_totals = items.aggregate(units=Coalesce(Sum("quantity"), 0), value=line_value)

    movements = list(
        StockMovement.objects.filter(
            movement_type__in=(MovementType.ADJUSTMENT, MovementType.EXPIRY_WRITE_OFF),
            created_at__gte=start,
            created_at__lt=end,
        )
        .select_related("batch__medicine", "created_by")
        .order_by("created_at", "id")
    )

    # Usable stock only: expired batches can't be sold, so they're not counted.
    usable = StockBatch.objects.filter(quantity_on_hand__gt=0, expiry_date__gte=today)
    valuation = [
        {
            "medicine": _medicine_label(row, "medicine__"),
            "units": row["units"],
            "unit_price": row["medicine__unit_price"],
            "value": row["value"],
        }
        for row in usable.order_by()
        .values("medicine__id", "medicine__name", "medicine__strength", "medicine__unit_price")
        .annotate(
            units=Sum("quantity_on_hand"),
            value=Sum(F("quantity_on_hand") * F("medicine__unit_price"), output_field=MONEY),
        )
        .order_by("medicine__name", "medicine__strength")
    ]
    return {
        "dispensed": dispensed,
        "top_dispensed": with_bars(dispensed[:20], key="value"),
        "dispensed_quantity": dispensed_totals["units"],
        "dispensed_value": dispensed_totals["value"] or Decimal("0.00"),
        "movements": movements,
        "valuation": valuation,
        "valuation_total": sum((row["value"] for row in valuation), Decimal("0.00")),
        "alerts": pharmacy_alerts_summary(today),
    }


# --- Laboratory ---------------------------------------------------------------------------


def lab_report(date_from, date_to):
    start, end = local_range_bounds(date_from, date_to)
    created = LabOrder.objects.filter(created_at__gte=start, created_at__lt=end)
    status_rows = {
        row["status"]: row["count"]
        for row in created.order_by().values("status").annotate(count=Count("id"))
    }
    by_status = with_bars(
        [
            {"label": label, "count": status_rows.get(value, 0)}
            for value, label in OrderStatus.choices
        ]
    )

    tests = {}

    def row_for(test_id, code, name, target):
        return tests.setdefault(
            test_id,
            {
                "code": code,
                "name": name,
                "target": target,
                "ordered": 0,
                "revenue": Decimal("0.00"),
                "completed": 0,
                "hours_total": Decimal("0"),
                "over_target": 0,
                "numeric": 0,
                "abnormal": 0,
            },
        )

    ordered = (
        LabOrderItem.objects.filter(order__in=created)
        .exclude(order__status=OrderStatus.CANCELLED)
        .order_by()
        .values("test", "test__code", "test__name", "test__turnaround_hours")
        .annotate(count=Count("id"), revenue=Coalesce(Sum("price"), ZERO, output_field=MONEY))
    )
    for row in ordered:
        test = row_for(
            row["test"], row["test__code"], row["test__name"], row["test__turnaround_hours"]
        )
        test["ordered"], test["revenue"] = row["count"], row["revenue"]

    released = LabOrder.objects.filter(
        status=OrderStatus.COMPLETED, released_at__gte=start, released_at__lt=end
    )
    for item in LabOrderItem.objects.filter(order__in=released).select_related("order", "test"):
        seconds = Decimal((item.order.released_at - item.order.created_at).total_seconds())
        hours = seconds / 3600
        test = row_for(item.test_id, item.test.code, item.test.name, item.test.turnaround_hours)
        test["completed"] += 1
        test["hours_total"] += hours
        if hours > item.test.turnaround_hours:
            test["over_target"] += 1

    results = (
        LabResult.objects.filter(item__order__in=released, value_numeric__isnull=False)
        .order_by()
        .values(
            "item__test", "item__test__code", "item__test__name", "item__test__turnaround_hours"
        )
        .annotate(numeric=Count("id"), abnormal=Count("id", filter=Q(flag__in=["L", "H"])))
    )
    for row in results:
        test = row_for(
            row["item__test"],
            row["item__test__code"],
            row["item__test__name"],
            row["item__test__turnaround_hours"],
        )
        test["numeric"], test["abnormal"] = row["numeric"], row["abnormal"]

    per_test = sorted(tests.values(), key=lambda t: t["code"])
    for test in per_test:
        test["average_hours"] = (
            (test["hours_total"] / test["completed"]).quantize(Decimal("0.1"))
            if test["completed"]
            else None
        )
        test["abnormal_rate"] = percent(test["abnormal"], test["numeric"])
    return {
        "orders_total": created.count(),
        "by_status": by_status,
        "per_test": per_test,
        "tests_ordered": sum(t["ordered"] for t in per_test),
        "revenue_total": sum((t["revenue"] for t in per_test), Decimal("0.00")),
    }


# --- Staff ------------------------------------------------------------------------------


def staff_report(date_from, date_to):
    leave_labels = dict(LeaveType.choices)
    leave = leave_taken_between(date_from, date_to)
    return {
        "by_department": with_bars(
            [{"label": name, "count": n} for name, n in headcount(by="department")]
        ),
        "by_category": with_bars(
            [{"label": name, "count": n} for name, n in headcount(by="category")]
        ),
        "month": date_to.replace(day=1),
        "attendance": monthly_attendance_summary(date_to.year, date_to.month),
        "leave": with_bars([{"label": leave_labels[k], "count": n} for k, n in leave.items()]),
        "leave_total": sum(leave.values()),
    }
