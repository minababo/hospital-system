import ipaddress

from django.conf import settings

from audit.context import reset_request_context, set_request_context

USER_AGENT_LENGTH = 200


def valid_ip(value):
    """The address if it's a real IPv4/IPv6 address, else None (stored as empty)."""
    try:
        return str(ipaddress.ip_address((value or "").strip()))
    except ValueError:
        return None


def client_ip(request):
    """The client's IP. X-Forwarded-For can be set by anyone, so it's only used when a
    trusted proxy (Render) is known to set it: AUDIT_TRUST_X_FORWARDED_FOR=True."""
    if settings.AUDIT_TRUST_X_FORWARDED_FOR:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return valid_ip(forwarded.split(",")[0])
    return valid_ip(request.META.get("REMOTE_ADDR"))


class AuditContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        token = set_request_context(
            client_ip(request), request.META.get("HTTP_USER_AGENT", "")[:USER_AGENT_LENGTH]
        )
        try:
            return self.get_response(request)
        finally:
            reset_request_context(token)
