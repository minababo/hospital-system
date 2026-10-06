from django.conf import settings
from django.urls import reverse

from accounts.navigation import CHANGE_PASSWORD, MY_LEAVE, NAV_ITEMS


def session_timeout(request):
    return {"session_timeout_seconds": settings.SESSION_COOKIE_AGE}


def navigation(request):
    user = request.user
    if not user.is_authenticated:
        return {"nav_items": []}

    match = request.resolver_match
    current = match.view_name if match else None
    links = list(NAV_ITEMS.get(user.role, []))
    if has_active_employee_record(request):
        position = links.index(CHANGE_PASSWORD) if CHANGE_PASSWORD in links else len(links)
        links.insert(position, MY_LEAVE)
    items = [
        {"label": label, "url": reverse(url_name), "active": url_name == current}
        for label, url_name in links
    ]
    return {"nav_items": items}


def has_active_employee_record(request):
    """True if the logged-in user is linked to a current employee.

    Uses the reverse one-to-one (user.employee_profile) instead of importing the staff
    app, so accounts doesn't depend on staff. The answer is cached on the request,
    because the context processor can run more than once per page.
    """
    if not hasattr(request, "_has_active_employee_record"):
        employee = getattr(request.user, "employee_profile", None)
        request._has_active_employee_record = employee is not None and employee.status == "ACTIVE"
    return request._has_active_employee_record
