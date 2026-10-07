from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from accounts.permissions import ALL_ROLES, RoleRequiredMixin, role_required, user_has_role
from reports import permissions, report_selectors
from reports.csv_export import csv_response
from reports.dates import DateRangeForm
from reports.selectors import DASHBOARDS


@role_required(*ALL_ROLES)
def dashboard(request):
    # One template per role, e.g. reports/dashboards/lab_staff.html, filled by the
    # matching dashboard_<role>() function in reports/selectors.py.
    data = DASHBOARDS[request.user.role](request.user)
    return render(request, f"reports/dashboards/{request.user.role.lower()}.html", data)


# --- Reports ----------------------------------------------------------------------------


class ReportView(RoleRequiredMixin, View):
    """Shared behaviour of the six reports.

    GET ?date_from=&date_to= shows the report (default: this month).
    ?export=csv downloads its main table; ?print=1 shows a printable version.
    Subclasses set the attributes below and implement get_data() and csv_table().
    """

    title = ""
    slug = ""
    body_template = ""
    csv_description = ""

    def get(self, request):
        form = DateRangeForm(request.GET)
        date_from, date_to = form.date_range()
        data = self.get_data(date_from, date_to, timezone.localdate())
        if request.GET.get("export") == "csv":
            header, rows = self.csv_table(data)
            filename = f"{self.slug}-report-{date_from:%Y%m%d}-{date_to:%Y%m%d}.csv"
            return csv_response(filename, header, rows)
        template = (
            "reports/reports/print.html"
            if request.GET.get("print") == "1"
            else ("reports/reports/page.html")
        )
        return render(
            request,
            template,
            {
                "title": self.title,
                "body_template": self.body_template,
                "csv_description": self.csv_description,
                "form": form,
                "date_from": date_from,
                "date_to": date_to,
                "report": data,
            },
        )


class PatientReportView(ReportView):
    allowed_roles = permissions.PATIENT_REPORT
    title, slug = "Patient report", "patients"
    body_template = "reports/reports/patients.html"
    csv_description = "patients registered in the range"

    def get_data(self, date_from, date_to, today):
        return report_selectors.patient_report(date_from, date_to)

    def csv_table(self, data):
        header = ["MRN", "Name", "Gender", "Date of birth", "Age at registration", "Registered"]
        rows = [
            [
                row["patient"].mrn,
                row["patient"].full_name,
                row["patient"].get_gender_display(),
                row["patient"].date_of_birth,
                row["age"],
                timezone.localtime(row["patient"].created_at).strftime("%Y-%m-%d %H:%M"),
            ]
            for row in data["patients"]
        ]
        return header, rows


class AppointmentReportView(ReportView):
    allowed_roles = permissions.APPOINTMENT_REPORT
    title, slug = "Appointment report", "appointments"
    body_template = "reports/reports/appointments.html"
    csv_description = "appointments by doctor"

    def get_data(self, date_from, date_to, today):
        return report_selectors.appointment_report(self.request.user, date_from, date_to)

    def csv_table(self, data):
        header = ["Doctor", "Department", "Appointments", "Completed", "Cancelled", "No-show"]
        rows = [
            [r["doctor"], r["department"], r["count"], r["completed"], r["cancelled"], r["no_show"]]
            for r in data["by_doctor"]
        ]
        return header, rows


class RevenueReportView(ReportView):
    allowed_roles = permissions.REVENUE_REPORT
    title, slug = "Revenue report", "revenue"
    body_template = "reports/reports/revenue.html"
    csv_description = "outstanding invoices as of today"

    def get_data(self, date_from, date_to, today):
        return report_selectors.revenue_report(date_from, date_to, today)

    def csv_table(self, data):
        header = ["Invoice", "Patient", "MRN", "Issued", "Age (days)", "Age bucket", "Balance"]
        rows = [
            [
                row["invoice"].number,
                row["invoice"].patient.full_name,
                row["invoice"].patient.mrn,
                timezone.localtime(row["invoice"].issued_at).strftime("%Y-%m-%d"),
                row["age"],
                row["bucket"],
                row["invoice"].balance_amount,
            ]
            for row in data["outstanding"]
        ]
        return header, rows


class PharmacyReportView(ReportView):
    allowed_roles = permissions.PHARMACY_REPORT
    title, slug = "Pharmacy report", "pharmacy"
    body_template = "reports/reports/pharmacy.html"
    csv_description = "medicines dispensed in the range"

    def get_data(self, date_from, date_to, today):
        return report_selectors.pharmacy_report(date_from, date_to, today)

    def csv_table(self, data):
        header = ["Medicine", "Quantity dispensed", "Value"]
        rows = [[r["medicine"], r["quantity"], r["value"]] for r in data["dispensed"]]
        return header, rows


class LaboratoryReportView(ReportView):
    allowed_roles = permissions.LABORATORY_REPORT
    title, slug = "Laboratory report", "laboratory"
    body_template = "reports/reports/laboratory.html"
    csv_description = "per-test figures"

    def get_data(self, date_from, date_to, today):
        return report_selectors.lab_report(date_from, date_to)

    def csv_table(self, data):
        header = [
            "Code",
            "Test",
            "Ordered",
            "Revenue",
            "Completed",
            "Average hours",
            "Target hours",
            "Over target",
            "Numeric results",
            "Abnormal",
            "Abnormal %",
        ]
        rows = [
            [
                t["code"],
                t["name"],
                t["ordered"],
                t["revenue"],
                t["completed"],
                t["average_hours"] if t["average_hours"] is not None else "",
                t["target"],
                t["over_target"],
                t["numeric"],
                t["abnormal"],
                t["abnormal_rate"],
            ]
            for t in data["per_test"]
        ]
        return header, rows


class StaffReportView(ReportView):
    allowed_roles = permissions.STAFF_REPORT
    title, slug = "Staff report", "staff"
    body_template = "reports/reports/staff.html"
    csv_description = "monthly attendance summary"

    def get_data(self, date_from, date_to, today):
        return report_selectors.staff_report(date_from, date_to)

    def csv_table(self, data):
        header = ["Employee", "Department", "Present", "Half day", "Absent", "Leave days"]
        rows = [
            [
                r.employee.full_name,
                r.employee.department.name,
                r.present,
                r.half_day,
                r.absent,
                r.leave_days,
            ]
            for r in data["attendance"]
        ]
        return header, rows


REPORTS = [
    ("Patient report", "reports:patients", permissions.PATIENT_REPORT,
     "Registrations, gender and age, admissions and length of stay"),
    ("Appointment report", "reports:appointments", permissions.APPOINTMENT_REPORT,
     "Appointments by status, doctor, department and day; cancellation and no-show rates"),
    ("Revenue report", "reports:revenue", permissions.REVENUE_REPORT,
     "Payments received, invoiced by charge type, discounts and outstanding invoices"),
    ("Pharmacy report", "reports:pharmacy", permissions.PHARMACY_REPORT,
     "Medicines dispensed, adjustments and write-offs, stock value and alerts"),
    ("Laboratory report", "reports:laboratory", permissions.LABORATORY_REPORT,
     "Orders, tests ordered, turnaround times and abnormal results"),
    ("Staff report", "reports:staff", permissions.STAFF_REPORT,
     "Headcount, monthly attendance and leave taken"),
]  # fmt: skip


class ReportIndexView(RoleRequiredMixin, View):
    allowed_roles = permissions.ANY_REPORT

    def get(self, request):
        reports = [
            {"title": title, "url": reverse(url_name), "description": description}
            for title, url_name, roles, description in REPORTS
            if user_has_role(request.user, *roles)
        ]
        return render(request, "reports/reports/index.html", {"reports": reports})
