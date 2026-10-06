from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import ListView

from accounts.models import Role
from accounts.permissions import RoleRequiredMixin
from common.forms import add_service_errors
from staff import selectors, services
from staff.forms import (
    AttendanceFormSet,
    DayForm,
    DecisionForm,
    EmployeeFilterForm,
    EmployeeForm,
    EndEmploymentForm,
    LeaveFilterForm,
    LeaveRecordForm,
    LeaveRequestForm,
    MonthForm,
)
from staff.models import AttendanceStatus, Employee, LeaveRequest, LeaveStatus
from staff.permissions import HR, SELF


def flash_errors(request, error):
    for message in error.messages:
        messages.error(request, message)


def is_doctor_conflict(error):
    return getattr(error, "code", None) == "doctor_conflicts"


class HRMixin(RoleRequiredMixin):
    allowed_roles = HR


# --- Employees --------------------------------------------------------------------------


class EmployeeListView(HRMixin, ListView):
    template_name = "staff/employee_list.html"
    context_object_name = "employees"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = EmployeeFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.employee_list(**self.filter_form.cleaned_data)
        return selectors.employee_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class EmployeeFormView(HRMixin, View):
    """Register (no pk) or edit an employee."""

    template_name = "staff/employee_form.html"

    def get(self, request, pk=None):
        employee = get_object_or_404(Employee, pk=pk) if pk else None
        form = EmployeeForm(instance=employee)
        return render(request, self.template_name, {"employee": employee, "form": form})

    def post(self, request, pk=None):
        employee = get_object_or_404(Employee, pk=pk) if pk else None
        form = EmployeeForm(request.POST, instance=employee)
        if form.is_valid():
            try:
                if employee:
                    services.update_employee(
                        employee, acting_user=request.user, **form.cleaned_data
                    )
                else:
                    employee = services.create_employee(
                        acting_user=request.user, **form.cleaned_data
                    )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, f"{employee} saved.")
                return redirect("staff:employee_detail", pk=employee.pk)
        return render(request, self.template_name, {"employee": employee, "form": form})


class EmployeeDetailView(HRMixin, View):
    def get(self, request, pk):
        employee = get_object_or_404(Employee.objects.select_related("department", "user"), pk=pk)
        return render(
            request,
            "staff/employee_detail.html",
            {
                "employee": employee,
                "attendance": selectors.recent_attendance(employee),
                "leave_requests": selectors.leave_by_employee(employee),
                "end_form": EndEmploymentForm(),
            },
        )


@method_decorator(require_POST, name="dispatch")
class EndEmploymentView(HRMixin, View):
    def post(self, request, pk):
        employee = get_object_or_404(Employee, pk=pk)
        form = EndEmploymentForm(request.POST)
        if not form.is_valid():
            for errors in form.errors.values():
                for error in errors:
                    messages.error(request, error)
        else:
            try:
                services.end_employment(employee, acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                flash_errors(request, error)
            else:
                messages.success(request, f"Employment of {employee.full_name} ended.")
        return redirect("staff:employee_detail", pk=pk)


# --- Attendance -------------------------------------------------------------------------


class AttendanceSheetView(HRMixin, View):
    template_name = "staff/attendance_sheet.html"

    def get_day(self):
        form = DayForm(self.request.GET)
        return (form.is_valid() and form.cleaned_data["date"]) or timezone.localdate()

    def initial_rows(self, sheet):
        """Formset rows: one per employee not on leave, pre-filled with saved values."""
        initial = []
        for row in sheet:
            if row.leave:
                continue
            record = row.attendance
            initial.append(
                {
                    "employee_id": row.employee.pk,
                    "status": record.status if record else "",
                    "check_in": record.check_in if record else None,
                    "check_out": record.check_out if record else None,
                    "notes": record.notes if record else "",
                }
            )
        return initial

    def get(self, request):
        day = self.get_day()
        sheet = selectors.attendance_sheet(day)
        formset = AttendanceFormSet(initial=self.initial_rows(sheet))
        return self.render_sheet(day, sheet, formset)

    def post(self, request):
        day = self.get_day()
        sheet = selectors.attendance_sheet(day)
        formset = AttendanceFormSet(request.POST, initial=self.initial_rows(sheet))
        if formset.is_valid():
            rows = [form.cleaned_data for form in formset if form.cleaned_data]
            try:
                saved = services.save_attendance_sheet(
                    date=day, rows=rows, acting_user=request.user
                )
            except ValidationError as error:
                flash_errors(request, error)
            else:
                messages.success(request, f"Attendance saved for {len(saved)} employee(s).")
                return redirect(f"{reverse('staff:attendance')}?date={day:%Y-%m-%d}")
        return self.render_sheet(day, sheet, formset)

    def render_sheet(self, day, sheet, formset):
        employees = {row.employee.pk: row.employee for row in sheet}
        for form in formset:
            # The hidden employee_id says which person each row is for.
            form.employee = employees.get(int(form["employee_id"].value() or 0))
        counts = {status: 0 for status in AttendanceStatus.values}
        for row in sheet:
            if row.attendance:
                counts[row.attendance.status] += 1
        today = timezone.localdate()
        return render(
            self.request,
            self.template_name,
            {
                "day": day,
                "prev_day": day - timedelta(days=1),
                "next_day": day + timedelta(days=1),
                "is_future": day > today,
                "formset": formset,
                "on_leave": [row for row in sheet if row.leave],
                "summary": {
                    "employees": len(sheet),
                    "present": counts[AttendanceStatus.PRESENT],
                    "half_day": counts[AttendanceStatus.HALF_DAY],
                    "absent": counts[AttendanceStatus.ABSENT],
                    "on_leave": sum(1 for row in sheet if row.leave),
                    "unrecorded": sum(1 for row in sheet if not row.leave and not row.attendance),
                },
            },
        )


class MonthlyAttendanceView(HRMixin, View):
    def get(self, request):
        form = MonthForm(request.GET)
        first = (form.is_valid() and form.cleaned_data["month"]) or timezone.localdate()
        first = first.replace(day=1)
        prev_month = (first - timedelta(days=1)).replace(day=1)
        next_month = (first + timedelta(days=32)).replace(day=1)
        return render(
            request,
            "staff/attendance_monthly.html",
            {
                "month": first,
                "prev_month": prev_month,
                "next_month": next_month,
                "rows": selectors.monthly_attendance_summary(first.year, first.month),
            },
        )


# --- Leave (HR) -------------------------------------------------------------------------


class LeaveListView(HRMixin, View):
    """Leave list with filters; POST records leave for an employee (approved at once)."""

    template_name = "staff/leave_list.html"

    def get(self, request):
        return self.render_page(LeaveRecordForm())

    def post(self, request):
        form = LeaveRecordForm(request.POST)
        show_confirm = bool(request.POST.get("confirm_doctor_conflicts"))
        if form.is_valid():
            try:
                leave = services.record_leave(acting_user=request.user, **form.cleaned_data)
            except ValidationError as error:
                show_confirm = show_confirm or is_doctor_conflict(error)
                add_service_errors(error, form)
            else:
                messages.success(request, f"{leave} recorded and approved.")
                return redirect("staff:leave_list")
        return self.render_page(form, show_confirm=show_confirm)

    def render_page(self, record_form, show_confirm=False):
        filter_form = LeaveFilterForm(self.request.GET)
        filters = dict(filter_form.cleaned_data) if filter_form.is_valid() else {}
        status = filters.pop("status", "")
        filters["status"] = None if status == "ALL" else (status or LeaveStatus.PENDING)
        return render(
            self.request,
            self.template_name,
            {
                "leave_requests": selectors.leave_list(**filters),
                "filter_form": filter_form,
                "record_form": record_form,
                "show_confirm": show_confirm,
            },
        )


def render_leave_detail(request, leave, decision_form=None, show_confirm=False):
    return render(
        request,
        "staff/leave_detail.html",
        {
            "leave": leave,
            "appointments": selectors.booked_appointments_during(
                leave.employee, leave.start_date, leave.end_date
            ),
            "decision_form": decision_form or DecisionForm(),
            "show_confirm": show_confirm,
            "can_cancel": leave.status in (LeaveStatus.PENDING, LeaveStatus.APPROVED)
            and timezone.localdate() < leave.start_date,
        },
    )


def get_leave(pk):
    return get_object_or_404(
        LeaveRequest.objects.select_related(
            "employee", "employee__user", "requested_by", "decided_by"
        ),
        pk=pk,
    )


class LeaveDetailView(HRMixin, View):
    def get(self, request, pk):
        return render_leave_detail(request, get_leave(pk))


@method_decorator(require_POST, name="dispatch")
class ApproveLeaveView(HRMixin, View):
    def post(self, request, pk):
        leave = get_leave(pk)
        form = DecisionForm(request.POST)
        form.is_valid()
        try:
            services.approve_leave(
                leave,
                note=form.cleaned_data.get("note", ""),
                confirm_doctor_conflicts=form.cleaned_data.get("confirm_doctor_conflicts", False),
                acting_user=request.user,
            )
        except ValidationError as error:
            if is_doctor_conflict(error):
                # Show the page again with the "approve anyway" checkbox.
                add_service_errors(error, form)
                return render_leave_detail(request, leave, form, show_confirm=True)
            flash_errors(request, error)
        else:
            messages.success(request, "Leave approved.")
        return redirect("staff:leave_detail", pk=pk)


@method_decorator(require_POST, name="dispatch")
class RejectLeaveView(HRMixin, View):
    def post(self, request, pk):
        try:
            services.reject_leave(
                get_leave(pk), note=request.POST.get("note", ""), acting_user=request.user
            )
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(request, "Leave rejected.")
        return redirect("staff:leave_detail", pk=pk)


@method_decorator(require_POST, name="dispatch")
class CancelLeaveView(RoleRequiredMixin, View):
    """Admin, or the employee themself (the service checks which)."""

    allowed_roles = SELF

    def post(self, request, pk):
        leave = get_leave(pk)
        try:
            services.cancel_leave(leave, acting_user=request.user)
        except ValidationError as error:
            flash_errors(request, error)
        else:
            messages.success(request, "Leave cancelled.")
        if request.user.role == Role.ADMIN:
            return redirect("staff:leave_detail", pk=pk)
        return redirect("staff:my_leave")


# --- Self-service ----------------------------------------------------------------------


class MyLeaveView(RoleRequiredMixin, View):
    allowed_roles = SELF
    template_name = "staff/my_leave.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.employee = getattr(request.user, "employee_profile", None)
        return super().dispatch(request, *args, **kwargs)

    def no_employee(self):
        return render(self.request, "staff/no_employee.html", status=404)

    def get(self, request):
        if self.employee is None:
            return self.no_employee()
        return self.render_page(LeaveRequestForm())

    def post(self, request):
        if self.employee is None:
            return self.no_employee()
        form = LeaveRequestForm(request.POST)
        if form.is_valid():
            try:
                services.request_leave(user=request.user, **form.cleaned_data)
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(request, "Leave requested. You'll see the decision here.")
                return redirect("staff:my_leave")
        return self.render_page(form)

    def render_page(self, form):
        today = timezone.localdate()
        return render(
            self.request,
            self.template_name,
            {
                "employee": self.employee,
                "leave_requests": selectors.my_leave(self.request.user),
                "form": form,
                "today": today,
                "cancellable": (LeaveStatus.PENDING, LeaveStatus.APPROVED),
            },
        )
