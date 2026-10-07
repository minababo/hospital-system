from datetime import timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView

from accounts.models import Role
from accounts.permissions import RoleRequiredMixin, user_has_role
from appointments import selectors, services
from appointments.forms import (
    AppointmentFilterForm,
    BookingConfirmForm,
    BookingSelectionForm,
    CancelForm,
    DatePickForm,
    RescheduleForm,
    slot_choices,
)
from appointments.models import Status
from appointments.permissions import BOOK_ROLES, CHECKIN_ROLES, COMPLETE_ROLES, VIEW_ROLES
from common.forms import add_service_errors
from doctors.models import Doctor, Weekday
from doctors.selectors import doctor_list, doctor_weekly_schedule
from patients.models import Patient
from patients.selectors import search_patients


def allowed_actions(appointment, user, now=None):
    """Action buttons to show this user. The services enforce the same rules again."""
    today, _ = selectors.local_now(now)
    actions = set()
    if appointment.status == Status.BOOKED and user_has_role(user, *BOOK_ROLES):
        actions |= {"reschedule", "cancel"}
        if appointment.is_past(now):
            actions.add("no_show")
    if (
        appointment.can_transition_to(Status.CHECKED_IN)
        and appointment.date == today
        and user_has_role(user, *CHECKIN_ROLES)
    ):
        actions.add("check_in")
    # Completing happens by finalizing the consultation record (records app), so there
    # is no separate "complete" button.
    return actions


def own_doctor_profile(user):
    return Doctor.objects.filter(user=user).first() if user.role == Role.DOCTOR else None


class AppointmentListView(RoleRequiredMixin, View):
    allowed_roles = VIEW_ROLES

    def get(self, request):
        today, _ = selectors.local_now()
        form = AppointmentFilterForm(request.GET)
        filters = form.cleaned_data if form.is_valid() else {}
        day = filters.get("date") or today
        is_doctor = request.user.role == Role.DOCTOR

        appointments = list(
            selectors.appointment_list(
                user=request.user,
                date=day,
                doctor=None if is_doctor else filters.get("doctor"),
                status=filters.get("status"),
                query=filters.get("q"),
            )
        )
        for appointment in appointments:
            appointment.actions = allowed_actions(appointment, request.user)

        return render(
            request,
            "appointments/appointment_list.html",
            {
                "form": form,
                "appointments": appointments,
                "day": day,
                "prev_day": day - timedelta(days=1),
                "next_day": day + timedelta(days=1),
                "today": today,
                "is_doctor": is_doctor,
                "can_book": user_has_role(request.user, *BOOK_ROLES),
            },
        )


class CalendarView(RoleRequiredMixin, View):
    allowed_roles = VIEW_ROLES

    def get(self, request):
        today, _ = selectors.local_now()
        week = DatePickForm({"date": request.GET.get("week")})
        week_start = (week.is_valid() and week.cleaned_data["date"]) or today

        # Doctors always see their own calendar; everyone else can pick a doctor.
        if request.user.role == Role.DOCTOR:
            doctor = own_doctor_profile(request.user)
        else:
            doctor_id = request.GET.get("doctor", "")
            doctor = Doctor.objects.filter(pk=doctor_id).first() if doctor_id.isdigit() else None

        calendar = selectors.week_calendar(user=request.user, doctor=doctor, week_start=week_start)
        return render(
            request,
            "appointments/calendar.html",
            {
                "calendar": calendar,
                "doctor": doctor,
                "doctors": doctor_list() if request.user.role != Role.DOCTOR else [],
                "today": today,
            },
        )


class BookView(RoleRequiredMixin, View):
    """Booking wizard. Steps are driven by GET parameters, so every step is a plain
    link/form submit and works without JavaScript:
    1. no ?patient -> search and pick a patient
    2. ?patient -> pick department, doctor and date
    3. ?patient&doctor&date -> pick a free slot and enter the reason (POST books it)
    """

    allowed_roles = BOOK_ROLES
    template_name = "appointments/book.html"

    def get(self, request):
        patient_id = request.GET.get("patient", "")
        patient = Patient.objects.filter(pk=patient_id).first() if patient_id.isdigit() else None
        if patient is None:
            query = request.GET.get("q", "").strip()
            return render(
                request,
                self.template_name,
                {
                    "step": "patient",
                    "q": query,
                    "patients": search_patients(query)[:20] if query else [],
                },
            )

        selection = BookingSelectionForm(request.GET)
        confirm_form, slots = None, []
        if selection.is_valid():
            doctor, date = selection.cleaned_data["doctor"], selection.cleaned_data["date"]
            if doctor and date:
                slots = selectors.available_slots(doctor, date)
                confirm_form = BookingConfirmForm(
                    initial={"patient": patient, "doctor": doctor, "date": date}, slots=slots
                )
        return self.render_step(patient, selection, confirm_form, slots)

    def post(self, request):
        form = BookingConfirmForm(request.POST)
        form.is_valid()  # bind cleaned_data so the page can be rebuilt on errors
        data = form.cleaned_data
        patient, doctor, date = data.get("patient"), data.get("doctor"), data.get("date")
        if patient is None:
            messages.error(request, "Please choose a patient first.")
            return redirect("appointments:book")

        if not form.errors:
            try:
                appointment = services.book_appointment(
                    patient=patient,
                    doctor=doctor,
                    date=date,
                    start_time=data["start_time"],
                    reason=data["reason"],
                    acting_user=request.user,
                )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(
                    request,
                    f"Appointment booked for {patient.full_name} with {doctor} on "
                    f"{appointment.date:%d %b %Y} at {appointment.start_time:%H:%M}.",
                )
                return redirect("appointments:appointment_detail", pk=appointment.pk)

        # Show the step again with errors and freshly computed free slots.
        selection = BookingSelectionForm(
            {"patient": patient.pk, "doctor": getattr(doctor, "pk", ""), "date": date or ""}
        )
        selection.is_valid()
        slots = selectors.available_slots(doctor, date) if doctor and date else []
        form.fields["start_time"].widget.choices = slot_choices(slots)
        return self.render_step(patient, selection, form, slots)

    def render_step(self, patient, selection, confirm_form, slots):
        return render(
            self.request,
            self.template_name,
            {
                "step": "slot" if confirm_form else "doctor",
                "patient": patient,
                "selection": selection,
                "confirm_form": confirm_form,
                "slots": slots,
                **schedule_hints(patient, selection),
            },
        )


def schedule_hints(patient, selection):
    """Booking hints once a doctor is chosen: their weekly hours, a warning when the
    chosen date isn't a working day, and the next dates with free slots (as links that
    keep the patient, department and doctor). Read-only; booking rules are unchanged."""
    data = selection.cleaned_data if selection.is_valid() else {}
    doctor = data.get("doctor")
    if doctor is None:
        return {}
    schedule = [
        (day, [block for block in blocks if block.is_active])
        for day, blocks in doctor_weekly_schedule(doctor)
    ]
    working_weekdays = {block.weekday for _, blocks in schedule for block in blocks}
    date = data.get("date")
    department = data.get("department")
    params = {"patient": patient.pk, "doctor": doctor.pk}
    if department:
        params["department"] = department.pk
    next_dates = [
        (day, "?" + urlencode({**params, "date": day.isoformat()}))
        for day in selectors.next_available_dates(doctor)
    ]
    return {
        "chosen_doctor": doctor,
        "schedule": schedule,
        "off_day": (
            Weekday(date.weekday()).label if date and date.weekday() not in working_weekdays else ""
        ),
        "next_dates": next_dates,
    }


class AppointmentDetailView(RoleRequiredMixin, DetailView):
    allowed_roles = VIEW_ROLES
    template_name = "appointments/appointment_detail.html"
    context_object_name = "appointment"

    def get_queryset(self):
        return selectors.visible_appointments(self.request.user).select_related(
            "created_by", "checked_in_by", "completed_by", "cancelled_by"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        appointment, user = self.object, self.request.user
        context["actions"] = allowed_actions(appointment, user)
        context["cancel_form"] = CancelForm()

        # Consultation links. Reverse one-to-one lookups (no import of the records app);
        # getattr gives None when the record/vitals don't exist yet.
        record = getattr(appointment, "medical_record", None)
        is_own_doctor = appointment.doctor.user_id == user.pk
        checked_in = appointment.status == Status.CHECKED_IN
        context["record"] = record
        context["vitals"] = getattr(appointment, "vitals", None)
        context["can_start_consultation"] = is_own_doctor and record is None and checked_in
        context["can_open_draft"] = (
            is_own_doctor and record is not None and record.status == "DRAFT"
        )
        context["can_view_record"] = (
            record is not None
            and record.status == "FINALIZED"
            and user.role in (Role.ADMIN, Role.DOCTOR, Role.NURSE)
        )
        context["can_record_vitals"] = (
            checked_in
            and (user.role == Role.NURSE or is_own_doctor)
            and (record is None or record.status == "DRAFT")
        )
        return context


class RescheduleView(RoleRequiredMixin, View):
    allowed_roles = BOOK_ROLES
    template_name = "appointments/reschedule.html"

    def get_appointment(self, pk):
        # Called from get/post, so it runs after the role check in dispatch().
        return get_object_or_404(selectors.visible_appointments(self.request.user), pk=pk)

    def get(self, request, pk):
        appointment = self.get_appointment(pk)
        if appointment.status != Status.BOOKED:
            messages.error(request, "Only booked appointments can be rescheduled.")
            return redirect("appointments:appointment_detail", pk=pk)
        picker = DatePickForm(request.GET)
        date = picker.cleaned_data["date"] if picker.is_valid() else None
        slots = (
            selectors.available_slots(appointment.doctor, date, exclude_appointment=appointment)
            if date
            else []
        )
        form = RescheduleForm(initial={"date": date}, slots=slots) if date else None
        return self.render_page(appointment, picker, form, slots, date)

    def post(self, request, pk):
        appointment = self.get_appointment(pk)
        form = RescheduleForm(request.POST)
        if form.is_valid():
            try:
                services.reschedule_appointment(
                    appointment,
                    date=form.cleaned_data["date"],
                    start_time=form.cleaned_data["start_time"],
                    acting_user=request.user,
                )
            except ValidationError as error:
                add_service_errors(error, form)
            else:
                messages.success(
                    request,
                    f"Appointment moved to {appointment.date:%d %b %Y} at "
                    f"{appointment.start_time:%H:%M}.",
                )
                return redirect("appointments:appointment_detail", pk=pk)

        appointment.refresh_from_db()
        date = form.cleaned_data.get("date")
        slots = (
            selectors.available_slots(appointment.doctor, date, exclude_appointment=appointment)
            if date
            else []
        )
        form.fields["start_time"].widget.choices = slot_choices(slots)
        return self.render_page(appointment, DatePickForm({"date": date}), form, slots, date)

    def render_page(self, appointment, picker, form, slots, date):
        return render(
            self.request,
            self.template_name,
            {
                "appointment": appointment,
                "picker": picker,
                "form": form,
                "slots": slots,
                "date": date,
            },
        )


# --- Status actions (POST only) ----------------------------------------------


@method_decorator(require_POST, name="dispatch")
class AppointmentActionView(RoleRequiredMixin, View):
    """Base for the status buttons. Subclasses set allowed_roles, success_message and
    implement perform(). Doctors can only act on their own appointments (404 otherwise)."""

    success_message = ""

    def post(self, request, pk):
        appointment = get_object_or_404(selectors.visible_appointments(request.user), pk=pk)
        try:
            self.perform(appointment)
        except ValidationError as error:
            for message in error.messages:
                messages.error(request, message)
        else:
            messages.success(request, self.success_message)
        return redirect(self.next_url(pk))

    def next_url(self, pk):
        next_url = self.request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url, allowed_hosts={self.request.get_host()}
        ):
            return next_url
        return reverse("appointments:appointment_detail", args=[pk])


class CancelView(AppointmentActionView):
    allowed_roles = BOOK_ROLES
    success_message = "Appointment cancelled."

    def perform(self, appointment):
        services.cancel_appointment(
            appointment, reason=self.request.POST.get("reason", ""), acting_user=self.request.user
        )


class CheckInView(AppointmentActionView):
    allowed_roles = CHECKIN_ROLES
    success_message = "Patient checked in."

    def perform(self, appointment):
        services.check_in_appointment(appointment, acting_user=self.request.user)


class CompleteView(AppointmentActionView):
    allowed_roles = COMPLETE_ROLES
    success_message = "Appointment completed."

    def perform(self, appointment):
        services.complete_appointment(appointment, acting_user=self.request.user)


class NoShowView(AppointmentActionView):
    allowed_roles = BOOK_ROLES
    success_message = "Appointment marked as no-show."

    def perform(self, appointment):
        services.mark_no_show(appointment, acting_user=self.request.user)
