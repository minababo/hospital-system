from functools import wraps

from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ImproperlyConfigured, PermissionDenied

from accounts.models import Role

ALL_ROLES = tuple(Role)


def user_has_role(user, *roles):
    return user.is_authenticated and user.is_active and user.role in roles


class RoleRequiredMixin(LoginRequiredMixin):
    """Anonymous users go to the login page; logged-in users without one of
    `allowed_roles` get a 403."""

    allowed_roles = ()

    def dispatch(self, request, *args, **kwargs):
        if not self.allowed_roles:
            raise ImproperlyConfigured(f"{type(self).__name__} must set allowed_roles.")
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        if not user_has_role(request.user, *self.allowed_roles):
            raise PermissionDenied
        return super().dispatch(request, *args, **kwargs)


def role_required(*roles):
    """Function-view equivalent of RoleRequiredMixin."""
    if not roles:
        raise ImproperlyConfigured("role_required() needs at least one role.")

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not user_has_role(request.user, *roles):
                raise PermissionDenied
            return view_func(request, *args, **kwargs)

        return wrapper

    return decorator
