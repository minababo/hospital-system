from django.conf import settings
from django.urls import reverse

from accounts.navigation import NAV_ITEMS


def session_timeout(request):
    return {"session_timeout_seconds": settings.SESSION_COOKIE_AGE}


def navigation(request):
    user = request.user
    if not user.is_authenticated:
        return {"nav_items": []}

    match = request.resolver_match
    current = match.view_name if match else None
    items = [
        {"label": label, "url": reverse(url_name), "active": url_name == current}
        for label, url_name in NAV_ITEMS.get(user.role, [])
    ]
    return {"nav_items": items}
