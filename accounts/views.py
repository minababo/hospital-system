from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.functional import cached_property
from django.views import View
from django.views.generic import FormView, ListView, UpdateView

from accounts import selectors, services
from accounts.forms import AdminSetPasswordForm, UserCreateForm, UserFilterForm, UserUpdateForm
from accounts.models import Role, User
from accounts.permissions import ALL_ROLES, RoleRequiredMixin


class PasswordChangeView(RoleRequiredMixin, auth_views.PasswordChangeView):
    allowed_roles = ALL_ROLES
    template_name = "accounts/password_change.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        # The parent view saves the password and keeps the user logged in.
        response = super().form_valid(form)
        messages.success(self.request, "Your password has been changed.")
        return response


class AdminOnlyMixin(RoleRequiredMixin):
    allowed_roles = (Role.ADMIN,)


class UserListView(AdminOnlyMixin, ListView):
    template_name = "accounts/user_list.html"
    context_object_name = "users"
    paginate_by = 20

    def get_queryset(self):
        self.filter_form = UserFilterForm(self.request.GET)
        if self.filter_form.is_valid():
            return selectors.user_list(**self.filter_form.cleaned_data)
        return selectors.user_list()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.filter_form
        return context


class UserCreateView(AdminOnlyMixin, FormView):
    form_class = UserCreateForm
    template_name = "accounts/user_form.html"
    success_url = reverse_lazy("accounts:user_list")

    def form_valid(self, form):
        data = form.cleaned_data
        user = services.create_user(
            username=data["username"],
            password=data["password1"],
            role=data["role"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            email=data["email"],
            acting_user=self.request.user,
        )
        messages.success(self.request, f"User {user.username} was created.")
        return redirect(self.success_url)


class UserUpdateView(AdminOnlyMixin, UpdateView):
    model = User
    form_class = UserUpdateForm
    template_name = "accounts/user_form.html"
    # Not "user": that name is already the logged-in user in every template.
    context_object_name = "managed_user"
    success_url = reverse_lazy("accounts:user_list")

    def form_valid(self, form):
        try:
            services.update_user(self.object, acting_user=self.request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
            return self.form_invalid(form)
        messages.success(self.request, f"User {self.object.username} was updated.")
        return redirect(self.success_url)


class UserSetPasswordView(AdminOnlyMixin, FormView):
    form_class = AdminSetPasswordForm
    template_name = "accounts/user_set_password.html"
    success_url = reverse_lazy("accounts:user_list")

    @cached_property
    def managed_user(self):
        return get_object_or_404(User, pk=self.kwargs["pk"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.managed_user
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["managed_user"] = self.managed_user
        return context

    def form_valid(self, form):
        user = self.managed_user
        services.set_user_password(
            user, form.cleaned_data["new_password1"], acting_user=self.request.user
        )
        # A password change logs out that user's sessions; keep the admin logged in
        # if they reset their own password here.
        if user.pk == self.request.user.pk:
            update_session_auth_hash(self.request, user)
        messages.success(self.request, f"Password for {user.username} was changed.")
        return redirect(self.success_url)


class UserToggleActiveView(AdminOnlyMixin, View):
    # Only post() is defined, so GET gets 405 Method Not Allowed (same effect as @require_POST).
    def post(self, request, pk):
        user = get_object_or_404(User, pk=pk)
        try:
            services.set_user_active(user, not user.is_active, acting_user=request.user)
        except ValidationError as error:
            messages.error(request, error.messages[0])
        else:
            state = "activated" if user.is_active else "deactivated"
            messages.success(request, f"User {user.username} was {state}.")
        return redirect("accounts:user_list")
