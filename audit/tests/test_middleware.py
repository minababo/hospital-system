import pytest
from django.test import RequestFactory

from audit.context import get_request_context
from audit.middleware import AuditContextMiddleware, client_ip


@pytest.fixture
def rf():
    return RequestFactory()


def test_ip_is_remote_addr_by_default(rf, settings):
    settings.AUDIT_TRUST_X_FORWARDED_FOR = False
    request = rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="203.0.113.7")
    assert client_ip(request) == "10.0.0.5"


def test_first_forwarded_address_is_used_only_when_trusted(rf, settings):
    settings.AUDIT_TRUST_X_FORWARDED_FOR = True
    request = rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="203.0.113.7, 10.0.0.1")
    assert client_ip(request) == "203.0.113.7"


def test_invalid_addresses_are_stored_as_none(rf, settings):
    settings.AUDIT_TRUST_X_FORWARDED_FOR = True
    request = rf.get("/", REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="not-an-ip")
    assert client_ip(request) is None


def test_middleware_sets_context_during_the_request_and_clears_it_after(rf, settings):
    settings.AUDIT_TRUST_X_FORWARDED_FOR = False
    seen = {}

    def view(request):
        seen.update(get_request_context())
        return "response"

    middleware = AuditContextMiddleware(view)
    request = rf.get("/", REMOTE_ADDR="192.0.2.1", HTTP_USER_AGENT="A" * 500)
    assert middleware(request) == "response"
    assert seen == {"ip": "192.0.2.1", "user_agent": "A" * 200}
    assert get_request_context() == {"ip": None, "user_agent": ""}


def test_context_is_cleared_even_when_the_view_raises(rf):
    def view(request):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        AuditContextMiddleware(view)(rf.get("/", REMOTE_ADDR="192.0.2.1"))
    assert get_request_context()["ip"] is None
