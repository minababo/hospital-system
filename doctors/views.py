from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.utils.functional import cached_property
from django.views import View
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView, UpdateView

from accounts.models import Role, User
from accounts.permissions import RoleRequiredMixin, user_has_role
from common.forms import add_service_errors
from doctors import selectors, services
from doctors.forms import (
    DepartmentFilterForm,
    DepartmentForm,
    DoctorAccountForm,
    DoctorFilterForm,
    DoctorProfileForm,
    DoctorScheduleForm,
    DoctorUserUpdateForm,
)
from doctors.models import Department, Doctor, DoctorSchedule

MANAGE_ROLES = (Role.ADMIN,)
VIEW_ROLES = (Role.ADMIN, Role.RECEPTIONIST, Role.NURSE, Role.DOCTOR)


class ManageMixin(RoleRequiredMixin):
    allowed_roles = MANAGE_ROLES


class ViewMixin(RoleRequiredMixin):
    allowed_roles = VIEW_ROLES


# --- Departments ------------------------------------------------------------


class DepartmentListView(ManageMixin, ListView):
    template_name = "doctors/department_list.html"
    context_object_name = "departments"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = DepartmentFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.department_list(**self.filter_form.cleaned_data)
        return selectors.department_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class DepartmentCreateView(ManageMixin, FormView):
    form_class = DepartmentForm
    template_name = "doctors/department_form.html"
    success_url = reverse_lazy("doctors:department_list")

    def form_valid(self, form):
        try:
            department = services.create_department(
                acting_user=self.request.user, **form.cleaned_data
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, f"Department {department.name} was created.")
        return redirect(self.success_url)


class DepartmentUpdateView(ManageMixin, UpdateView):
    model = Department
    form_class = DepartmentForm
    template_name = "doctors/department_form.html"
    success_url = reverse_lazy("doctors:department_list")

    def form_valid(self, form):
        try:
            services.update_department(
                self.object, acting_user=self.request.user, **form.cleaned_data
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, f"Department {self.object.name} was updated.")
        return redirect(self.success_url)


@method_decorator(require_POST, name="dispatch")
class DepartmentToggleActiveView(ManageMixin, View):
    def post(self, request, pk):
        department = get_object_or_404(Department, pk=pk)
        services.set_department_active(
            department, not department.is_active, acting_user=request.user
        )
        state = "activated" if department.is_active else "deactivated"
        messages.success(request, f"Department {department.name} was {state}.")
        return redirect("doctors:department_list")


# --- Doctors ----------------------------------------------------------------


class DoctorListView(ViewMixin, ListView):
    template_name = "doctors/doctor_list.html"
    context_object_name = "doctors"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = DoctorFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.doctor_list(**self.filter_form.cleaned_data)
        return selectors.doctor_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        can_manage = user_has_role(self.request.user, *MANAGE_ROLES)
        context["filter_form"] = self.filter_form
        context["can_manage"] = can_manage
        context["users_without_profile"] = (
            selectors.doctor_users_without_profile() if can_manage else []
        )
        return context


class DoctorCreateView(ManageMixin, View):
    """Two forms (account + profile) submitted together and saved by one service call."""

    template_name = "doctors/doctor_form.html"

    def get(self, request):
        return self.render(DoctorAccountForm(prefix="account"), DoctorProfileForm(prefix="profile"))

    def post(self, request):
        account_form = DoctorAccountForm(request.POST, prefix="account")
        profile_form = DoctorProfileForm(request.POST, prefix="profile")
        # Validate both (no short-circuit) so every error shows at once.
        if not all([account_form.is_valid(), profile_form.is_valid()]):
            return self.render(account_form, profile_form)

        account = account_form.cleaned_data
        try:
            doctor = services.create_doctor(
                user_data={
                    "username": account["username"],
                    "password": account["password1"],
                    "first_name": account["first_name"],
                    "last_name": account["last_name"],
                    "email": account["email"],
                },
                profile_data=profile_form.cleaned_data,
                acting_user=request.user,
            )
        except ValidationError as error:
            add_service_errors(error, profile_form, account_form)
            return self.render(account_form, profile_form)

        messages.success(request, f"{doctor} was added.")
        return redirect("doctors:doctor_detail", pk=doctor.pk)

    def render(self, account_form, profile_form):
        return render(
            self.request,
            self.template_name,
            {"title": "Add doctor", "user_form": account_form, "profile_form": profile_form},
        )


class DoctorCompleteProfileView(ManageMixin, FormView):
    form_class = DoctorProfileForm
    template_name = "doctors/doctor_form.html"

    @cached_property
    def doctor_user(self):
        return get_object_or_404(
            User,
            pk=self.kwargs["user_pk"],
            role=Role.DOCTOR,
            is_active=True,
            doctor_profile__isnull=True,
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Looking up doctor_user here (and in form_valid) gives a 404 for a user who
        # isn't an active doctor without a profile.
        name = self.doctor_user.get_full_name() or self.doctor_user.username
        context["title"] = f"Complete profile for {name}"
        context["profile_form"] = context.pop("form")
        return context

    def form_valid(self, form):
        try:
            doctor = services.create_doctor_profile(
                user=self.doctor_user, profile_data=form.cleaned_data, acting_user=self.request.user
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, f"Profile for {doctor} was created.")
        return redirect("doctors:doctor_detail", pk=doctor.pk)


class DoctorDetailView(ViewMixin, DetailView):
    queryset = Doctor.objects.select_related("user", "department")
    template_name = "doctors/doctor_detail.html"
    context_object_name = "doctor"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["weekly_schedule"] = selectors.doctor_weekly_schedule(self.object)
        context["can_manage"] = user_has_role(self.request.user, *MANAGE_ROLES)
        return context


class DoctorUpdateView(ManageMixin, View):
    template_name = "doctors/doctor_form.html"

    def get(self, request, pk):
        doctor = self.get_doctor(pk)
        return self.render(
            doctor,
            DoctorUserUpdateForm(instance=doctor.user, prefix="user"),
            DoctorProfileForm(instance=doctor, prefix="profile"),
        )

    def post(self, request, pk):
        doctor = self.get_doctor(pk)
        user_form = DoctorUserUpdateForm(request.POST, instance=doctor.user, prefix="user")
        profile_form = DoctorProfileForm(request.POST, instance=doctor, prefix="profile")
        if not all([user_form.is_valid(), profile_form.is_valid()]):
            return self.render(doctor, user_form, profile_form)

        try:
            services.update_doctor(
                doctor,
                user_fields=user_form.cleaned_data,
                profile_fields=profile_form.cleaned_data,
                acting_user=request.user,
            )
        except ValidationError as error:
            add_service_errors(error, profile_form, user_form)
            return self.render(doctor, user_form, profile_form)

        messages.success(request, f"{doctor} was updated.")
        return redirect("doctors:doctor_detail", pk=doctor.pk)

    def get_doctor(self, pk):
        return get_object_or_404(Doctor.objects.select_related("user"), pk=pk)

    def render(self, doctor, user_form, profile_form):
        return render(
            self.request,
            self.template_name,
            {
                "title": f"Edit {doctor}",
                "doctor": doctor,
                "user_form": user_form,
                "profile_form": profile_form,
            },
        )


class MyProfileView(RoleRequiredMixin, View):
    allowed_roles = (Role.DOCTOR,)

    def get(self, request):
        doctor = Doctor.objects.filter(user=request.user).first()
        if doctor is None:
            return render(request, "doctors/no_profile.html")
        return redirect("doctors:doctor_detail", pk=doctor.pk)


# --- Schedules --------------------------------------------------------------


class ScheduleCreateView(ManageMixin, FormView):
    form_class = DoctorScheduleForm
    template_name = "doctors/schedule_form.html"

    @cached_property
    def doctor(self):
        return get_object_or_404(Doctor.objects.select_related("user"), pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        # Attach the doctor before validation so DoctorSchedule.clean() can check overlaps.
        kwargs["instance"] = DoctorSchedule(doctor=self.doctor)
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["doctor"] = self.doctor
        return context

    def form_valid(self, form):
        try:
            services.create_schedule(
                doctor=self.doctor, acting_user=self.request.user, **form.cleaned_data
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, "Schedule block was added.")
        return redirect("doctors:doctor_detail", pk=self.doctor.pk)


class ScheduleUpdateView(ManageMixin, UpdateView):
    queryset = DoctorSchedule.objects.select_related("doctor__user")
    form_class = DoctorScheduleForm
    template_name = "doctors/schedule_form.html"
    context_object_name = "schedule"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["doctor"] = self.object.doctor
        return context

    def form_valid(self, form):
        try:
            services.update_schedule(
                self.object, acting_user=self.request.user, **form.cleaned_data
            )
        except ValidationError as error:
            add_service_errors(error, form)
            return self.form_invalid(form)
        messages.success(self.request, "Schedule block was updated.")
        return redirect("doctors:doctor_detail", pk=self.object.doctor_id)


@method_decorator(require_POST, name="dispatch")
class ScheduleDeleteView(ManageMixin, View):
    def post(self, request, pk):
        schedule = get_object_or_404(DoctorSchedule, pk=pk)
        doctor_pk = schedule.doctor_id
        services.delete_schedule(schedule, acting_user=request.user)
        messages.success(request, "Schedule block was deleted.")
        return redirect("doctors:doctor_detail", pk=doctor_pk)
