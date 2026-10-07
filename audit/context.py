"""Request details (IP address, browser) for audit entries.

Services don't receive the request, so the middleware stores these details in a
ContextVar for the duration of the request. A ContextVar is like a per-request global:
each request (thread or async task) sees its own value, so requests never mix.
"""

from contextvars import ContextVar

_request_context = ContextVar("audit_request_context", default=None)


def set_request_context(ip, user_agent):
    """Store the details; returns a token to pass to reset_request_context()."""
    return _request_context.set({"ip": ip, "user_agent": user_agent})


def reset_request_context(token):
    _request_context.reset(token)


def get_request_context():
    """{"ip": ..., "user_agent": ...}; empty values outside a request (shell, tests)."""
    return _request_context.get() or {"ip": None, "user_agent": ""}
