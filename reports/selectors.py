"""Dashboard numbers, one function per role: dashboard_<role>(user, now=None) -> dict.

reports is the top-level aggregator: it may read any app, and no app imports reports.
Each function returns raw numbers (tested directly) plus "cards" for the template. A
card links to a page only when the user's role may open that page.
"""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.urls import reverse
from django.utils import timezone

from accounts.models import Role
from accounts.permissions import user_has_role
from admissions import permissions as admissions_permissions
from admissions.models import Admission, AdmissionStatus
from admissions.selectors import current_admissions, occupancy_summary
from appointments import permissions as appointments_permissions
from appointments.models import Status as AppointmentStatus
from appointments.selectors import todays_appointments
from billing import permissions as billing_permissions
from billing.models import Charge, Invoice, PaymentMethod
from billing.selectors import MONEY, OPEN_STATUSES, ZERO, payments_received, with_totals
from laboratory import permissions as laboratory_permissions
from laboratory.models import LabOrder, OrderStatus, Priority
from laboratory.selectors import pending_requests_count
from patients.models import Patient
from patients.views import VIEW_ROLES as PATIENT_VIEW_ROLES
from pharmacy import permissions as pharmacy_permissions
from pharmacy.models import Dispense
from pharmacy.selectors import pharmacy_alerts_summary
from records.models import MedicalRecord, Prescription, PrescriptionStatus, RecordStatus
from reports import permissions as report_permissions
from reports.dates import local_day_bounds, local_range_bounds, this_month
from staff import permissions as staff_permissions
from staff.models import Attendance, AttendanceStatus
from staff.selectors import pending_leave_count

ZERO_AMOUNT = Decimal("0.00")

FINISHED_APPOINTMENTS = (
    AppointmentStatus.COMPLETED,
    AppointmentStatus.CANCELLED,
    AppointmentStatus.NO_SHOW,
)


def _today(now):
    return timezone.localdate(now or timezone.now())


def link(user, roles, url_name, query=""):
    """The page's URL if this user's role may open it, else None (no link shown)."""
    if not user_has_role(user, *roles):
        return None
    return reverse(url_name) + (f"?{query}" if query else "")


def revenue_link(user, date_from, date_to):
    """The revenue report for a date range (it lists the payments behind the total)."""
    return link(
        user,
        report_permissions.REVENUE_REPORT,
        "reports:revenue",
        f"date_from={date_from:%Y-%m-%d}&date_to={date_to:%Y-%m-%d}",
    )


def checked_in_link(user):
    return link(
        user,
        appointments_permissions.VIEW_ROLES,
        "appointments:appointment_list",
        "status=CHECKED_IN",
    )


def card(label, value, *, sub="", href=None, money=False):
    return {"label": label, "value": value, "sub": sub, "href": href, "money": money}


# --- Shared queries ---------------------------------------------------------------------


def patient_counts(today):
    month_start, month_end = local_range_bounds(*this_month(today))
    return Patient.objects.aggregate(
        total=Count("id"),
        new_this_month=Count("id", filter=Q(created_at__gte=month_start, created_at__lt=month_end)),
    )


def appointment_status_counts(user, now=None):
    """{status: count} for today's appointments (doctors: their own only)."""
    rows = (
        todays_appointments(user=user, now=now).order_by().values("status").annotate(n=Count("id"))
    )
    counts = {status: 0 for status in AppointmentStatus.values}
    counts.update({row["status"]: row["n"] for row in rows})
    return counts


def outstanding_invoices():
    """Count and total balance of issued, not fully paid invoices (as of now)."""
    totals = with_totals(Invoice.objects.filter(status__in=OPEN_STATUSES)).aggregate(
        count=Count("id"), total=Coalesce(Sum("balance_amount"), ZERO, output_field=MONEY)
    )
    return totals["count"], totals["total"]


def lab_order_counts(now=None):
    day_start, day_end = local_day_bounds(_today(now))
    open_statuses = (OrderStatus.REQUESTED, OrderStatus.SAMPLE_COLLECTED)
    return LabOrder.objects.aggregate(
        requested=Count("id", filter=Q(status=OrderStatus.REQUESTED)),
        sample_collected=Count("id", filter=Q(status=OrderStatus.SAMPLE_COLLECTED)),
        urgent_open=Count("id", filter=Q(status__in=open_statuses, priority=Priority.URGENT)),
        completed_today=Count(
            "id",
            filter=Q(
                status=OrderStatus.COMPLETED, released_at__gte=day_start, released_at__lt=day_end
            ),
        ),
    )


def alerts_sub(alerts):
    return (
        f"Expired {alerts['expired']} · Expiring soon {alerts['expiring_soon']} · "
        f"Low {alerts['low_stock']} · Out {alerts['out_of_stock']}"
    )


def status_sub(counts):
    return " · ".join(f"{AppointmentStatus(status).label} {n}" for status, n in counts.items() if n)


# --- Dashboards -----------------------------------------------------------------------


def dashboard_admin(user, now=None):
    today = _today(now)
    first, last = this_month(today)
    patients = patient_counts(today)
    appointments = appointment_status_counts(user, now)
    collected_today = payments_received(today, today)["total"]
    collected_month = payments_received(first, last)["total"]
    outstanding_count, outstanding_total = outstanding_invoices()
    lab = lab_order_counts(now)
    pharmacy_alerts = pharmacy_alerts_summary(today)
    occupancy = occupancy_summary()
    pending_leave = pending_leave_count()
    staff_present = Attendance.objects.filter(
        date=today, status__in=(AttendanceStatus.PRESENT, AttendanceStatus.HALF_DAY)
    ).count()

    billing_roles = billing_permissions.VIEW_BILLING
    data = {
        "patients": patients,
        "appointments": appointments,
        "appointments_total": sum(appointments.values()),
        "collected_today": collected_today,
        "collected_month": collected_month,
        "outstanding_count": outstanding_count,
        "outstanding_total": outstanding_total,
        "lab_pending": lab["requested"] + lab["sample_collected"],
        "lab_urgent": lab["urgent_open"],
        "pharmacy_alerts": pharmacy_alerts,
        "occupancy": occupancy,
        "pending_leave": pending_leave,
        "staff_present": staff_present,
    }
    data["cards"] = [
        card(
            "Total patients",
            patients["total"],
            sub=f"{patients['new_this_month']} new this month",
            href=link(user, PATIENT_VIEW_ROLES, "patients:patient_list"),
        ),
        card(
            "Today's appointments",
            data["appointments_total"],
            sub=status_sub(appointments),
            href=link(user, appointments_permissions.VIEW_ROLES, "appointments:appointment_list"),
        ),
        card(
            "Collected today",
            collected_today,
            money=True,
            href=revenue_link(user, today, today),
        ),
        card(
            "Collected this month",
            collected_month,
            money=True,
            sub=f"{first:%B %Y}",
            href=revenue_link(user, first, last),
        ),
        card(
            "Outstanding balance",
            outstanding_total,
            money=True,
            sub=f"{outstanding_count} unpaid invoice(s)",
            href=link(user, billing_roles, "billing:invoice_list", "outstanding_only=on"),
        ),
        card(
            "Laboratory requests",
            data["lab_pending"],
            sub=f"{lab['urgent_open']} urgent",
            href=link(user, laboratory_permissions.VIEW_LAB, "laboratory:worklist"),
        ),
        card(
            "Pharmacy alerts",
            sum(pharmacy_alerts.values()),
            sub=alerts_sub(pharmacy_alerts),
            href=link(user, pharmacy_permissions.MANAGE_MEDICINES, "pharmacy:alerts"),
        ),
        card(
            "Bed occupancy",
            f"{occupancy['occupied']}/{occupancy['total']}",
            sub=f"{occupancy['percent']}% occupied",
            href=link(user, admissions_permissions.VIEW, "admissions:bed_board"),
        ),
        card(
            "Pending leave",
            pending_leave,
            href=link(user, staff_permissions.HR, "staff:leave_list"),
        ),
        card(
            "Staff present today",
            staff_present,
            sub="Present or half day",
            href=link(user, staff_permissions.HR, "staff:attendance"),
        ),
    ]
    return data


def dashboard_receptionist(user, now=None):
    today = _today(now)
    upcoming = list(
        todays_appointments(user=user, now=now)
        .exclude(status__in=FINISHED_APPOINTMENTS)
        .order_by("start_time")[:10]
    )
    counts = appointment_status_counts(user, now)
    outstanding_count, outstanding_total = outstanding_invoices()
    data = {
        "upcoming": upcoming,
        "appointments_total": sum(counts.values()),
        "checked_in": counts[AppointmentStatus.CHECKED_IN],
        "total_patients": patient_counts(today)["total"],
        "unpaid_count": outstanding_count,
        "outstanding_total": outstanding_total,
    }
    billing_roles = billing_permissions.VIEW_BILLING
    data["cards"] = [
        card(
            "Today's appointments",
            data["appointments_total"],
            sub=status_sub(counts),
            href=link(user, appointments_permissions.VIEW_ROLES, "appointments:appointment_list"),
        ),
        card(
            "Checked in", data["checked_in"], sub="Waiting to be seen", href=checked_in_link(user)
        ),
        card(
            "Total patients",
            data["total_patients"],
            href=link(user, PATIENT_VIEW_ROLES, "patients:patient_list"),
        ),
        card(
            "Unpaid invoices",
            outstanding_count,
            href=link(user, billing_roles, "billing:invoice_list", "outstanding_only=on"),
        ),
        card("Outstanding balance", outstanding_total, money=True),
    ]
    return data


def doctor_action(appointment):
    """(label, url) for the next step on one of the doctor's appointments."""
    record = getattr(appointment, "medical_record", None)
    if record is not None:
        label = "Open consultation" if record.status == RecordStatus.DRAFT else "View record"
        return label, reverse("records:record_detail", args=[record.pk])
    detail = reverse("appointments:appointment_detail", args=[appointment.pk])
    if appointment.status == AppointmentStatus.CHECKED_IN:
        return "Start consultation", detail  # starting is a POST button on that page
    return "Open", detail


def dashboard_doctor(user, now=None):
    """Only the doctor's own appointments, records, lab orders and inpatients."""
    now = now or timezone.now()
    appointments = list(
        todays_appointments(user=user, now=now)
        .select_related("medical_record")
        .order_by("start_time")
    )
    for appointment in appointments:
        appointment.action_label, appointment.action_url = doctor_action(appointment)
    drafts = MedicalRecord.objects.filter(doctor__user=user, status=RecordStatus.DRAFT)
    lab_results = list(
        LabOrder.objects.filter(
            ordering_doctor__user=user,
            status=OrderStatus.COMPLETED,
            released_at__gte=now - timedelta(days=7),
        )
        .select_related("patient")
        .annotate(abnormal=Count("items__results", filter=Q(items__results__flag__in=["L", "H"])))
        .order_by("-released_at")[:10]
    )
    inpatients = list(current_admissions().filter(admitting_doctor__user=user))
    waiting = sum(1 for a in appointments if a.status == AppointmentStatus.CHECKED_IN)
    data = {
        "appointments": appointments,
        "waiting": waiting,
        "drafts": list(drafts.select_related("patient", "appointment").order_by("created_at")[:10]),
        "draft_count": drafts.count(),
        "lab_results": lab_results,
        "inpatients": inpatients,
    }
    data["cards"] = [
        card("Today's appointments", len(appointments), sub="Your own"),
        card("Waiting (checked in)", waiting),
        card("Draft consultations", data["draft_count"], sub="Not finalized yet"),
        card(
            "Lab results (7 days)",
            len(lab_results),
            sub=f"{sum(1 for o in lab_results if o.abnormal)} with abnormal values",
        ),
        card(
            "Your inpatients",
            len(inpatients),
            href=link(user, admissions_permissions.VIEW, "admissions:admission_list", "mine=on"),
        ),
    ]
    return data


def dashboard_nurse(user, now=None):
    checked_in = list(
        todays_appointments(user=user, now=now)
        .filter(status=AppointmentStatus.CHECKED_IN)
        .order_by("start_time")
    )
    occupancy = occupancy_summary()
    inpatients = Admission.objects.filter(status=AdmissionStatus.ADMITTED).count()
    pending_lab = pending_requests_count()
    data = {
        "checked_in": checked_in,
        "inpatients": inpatients,
        "occupancy": occupancy,
        "pending_lab": pending_lab,
    }
    data["cards"] = [
        card("Checked in today", len(checked_in), href=checked_in_link(user)),
        card(
            "Current inpatients",
            inpatients,
            href=link(user, admissions_permissions.VIEW, "admissions:admission_list"),
        ),
        card(
            "Bed occupancy",
            f"{occupancy['occupied']}/{occupancy['total']}",
            sub=f"{occupancy['percent']}% occupied · {occupancy['free']} free",
            href=link(user, admissions_permissions.VIEW, "admissions:bed_board"),
        ),
        card(
            "Pending lab requests",
            pending_lab,
            href=link(user, laboratory_permissions.VIEW_LAB, "laboratory:worklist"),
        ),
    ]
    return data


def dashboard_lab_staff(user, now=None):
    counts = lab_order_counts(now)

    def worklist(query):
        return link(user, laboratory_permissions.VIEW_LAB, "laboratory:worklist", query)

    data = dict(counts)
    data["cards"] = [
        card(
            "Requested",
            counts["requested"],
            sub="Sample not collected yet",
            href=worklist("status=REQUESTED"),
        ),
        card(
            "Sample collected",
            counts["sample_collected"],
            sub="Waiting for results",
            href=worklist("status=SAMPLE_COLLECTED"),
        ),
        # The worklist shows open orders by default, so this is urgent AND open.
        card("Urgent (open)", counts["urgent_open"], href=worklist("priority=URGENT")),
        # No link: the worklist's date filter is the request date, not the release date.
        card("Completed today", counts["completed_today"]),
    ]
    return data


def dashboard_pharmacist(user, now=None):
    today = _today(now)
    day_start, day_end = local_day_bounds(today)
    # Same statuses as the dispensing queue (records owns prescription statuses).
    awaiting = Prescription.objects.filter(
        status__in=(PrescriptionStatus.ISSUED, PrescriptionStatus.PARTIALLY_DISPENSED)
    ).count()
    pharmacy_alerts = pharmacy_alerts_summary(today)
    dispensed_today = Dispense.objects.filter(
        dispensed_at__gte=day_start, dispensed_at__lt=day_end
    ).count()
    data = {
        "awaiting": awaiting,
        "pharmacy_alerts": pharmacy_alerts,
        "dispensed_today": dispensed_today,
    }
    data["cards"] = [
        card(
            "Prescriptions waiting",
            awaiting,
            sub="Issued or partially dispensed",
            href=link(user, pharmacy_permissions.DISPENSE_VIEW, "pharmacy:dispensing_queue"),
        ),
        card(
            "Stock alerts",
            sum(pharmacy_alerts.values()),
            sub=alerts_sub(pharmacy_alerts),
            href=link(user, pharmacy_permissions.MANAGE_MEDICINES, "pharmacy:alerts"),
        ),
        card("Dispensed today", dispensed_today),
    ]
    return data


def dashboard_accountant(user, now=None):
    today = _today(now)
    first, last = this_month(today)
    day_start, day_end = local_day_bounds(today)
    received_today = payments_received(today, today)
    received_month = payments_received(first, last)
    outstanding_count, outstanding_total = outstanding_invoices()
    issued_today = Invoice.objects.filter(issued_at__gte=day_start, issued_at__lt=day_end).count()
    unbilled = Charge.objects.filter(is_voided=False, invoice__isnull=True).aggregate(
        total=Coalesce(Sum("amount"), ZERO, output_field=MONEY)
    )["total"]
    data = {
        "received_today": received_today,
        "received_month": received_month,
        "outstanding_count": outstanding_count,
        "outstanding_total": outstanding_total,
        "issued_today": issued_today,
        "unbilled_total": unbilled,
        "method_rows": [
            {
                "label": label,
                "today": received_today["by_method"].get(method, ZERO_AMOUNT),
                "month": received_month["by_method"].get(method, ZERO_AMOUNT),
            }
            for method, label in PaymentMethod.choices
        ],
    }
    data["cards"] = [
        card(
            "Collected today",
            received_today["total"],
            money=True,
            href=revenue_link(user, today, today),
        ),
        card(
            "Collected this month",
            received_month["total"],
            money=True,
            sub=f"{first:%B %Y}",
            href=revenue_link(user, first, last),
        ),
        card(
            "Outstanding balance",
            outstanding_total,
            money=True,
            sub=f"{outstanding_count} unpaid invoice(s)",
            href=link(
                user,
                billing_permissions.VIEW_BILLING,
                "billing:invoice_list",
                "outstanding_only=on",
            ),
        ),
        # No link: the invoice list filters by the date an invoice was created, not issued.
        card("Invoices issued today", issued_today),
        card("Unbilled charges", unbilled, money=True, sub="Not on any invoice yet"),
    ]
    return data


DASHBOARDS = {
    Role.ADMIN: dashboard_admin,
    Role.RECEPTIONIST: dashboard_receptionist,
    Role.DOCTOR: dashboard_doctor,
    Role.NURSE: dashboard_nurse,
    Role.LAB_STAFF: dashboard_lab_staff,
    Role.PHARMACIST: dashboard_pharmacist,
    Role.ACCOUNTANT: dashboard_accountant,
}
